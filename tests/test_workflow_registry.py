"""WorkflowRegistry 단위 테스트.

매니페스트 로딩, preset 치환, file 존재 기반 available 판정, primary 자동
선택, status=needs_api_conversion 분기, 잘못된 매니페스트 에러 등.

외부 워크플로우 파일에 의존하지 않게 모든 테스트는 ``tmp_path`` 에 자체
매니페스트와 더미 JSON 을 생성한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_registry import (
    CostEstimate,
    ForbiddenToken,
    InputLabelSpec,
    OutputSpec,
    PromptExample,
    PromptTemplate,
    PromptToken,
    RelatedRefs,
    TagSet,
    VariantSpec,
    WorkflowRegistry,
    WorkflowRegistryError,
    get_default_registry,
    infer_input_labels,
)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _write_manifest(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "registry.yml"
    manifest.write_text(body, encoding="utf-8")
    return manifest


def _write_workflow(root: Path, rel_path: str, payload: dict | None = None) -> Path:
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload or {"3": {"class_type": "KSampler", "inputs": {"seed": 0}}}),
        encoding="utf-8",
    )
    return target


# ----------------------------------------------------------------------------
# 기본 로딩
# ----------------------------------------------------------------------------


def test_loads_basic_manifest(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    description: "캐릭터"
    variants:
      v:
        description: "test variant"
        file: sprite/v.json
        primary: true
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
        defaults:
          steps: 30
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    assert "sprite" in reg.categories
    cat = reg.category("sprite")
    assert cat.description == "캐릭터"
    v = reg.variant("sprite", "v")
    assert v.available is True
    assert v.primary is True
    assert v.defaults == {"steps": 30}
    assert v.outputs == (OutputSpec(node_title="Save Stage1", label="stage1", primary=True),)


def test_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkflowRegistryError, match="not found"):
        WorkflowRegistry(root=tmp_path)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "categories: [broken")
    with pytest.raises(WorkflowRegistryError, match="YAML parse error"):
        WorkflowRegistry(root=tmp_path)


def test_categories_must_be_mapping(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "categories: [not-a-mapping]")
    with pytest.raises(WorkflowRegistryError, match="must be a mapping"):
        WorkflowRegistry(root=tmp_path)


# ----------------------------------------------------------------------------
# available / needs_api_conversion 판정
# ----------------------------------------------------------------------------


def test_variant_available_requires_existing_file(tmp_path: Path) -> None:
    # file 경로는 매니페스트엔 있지만 실제 파일은 없다
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      missing_file:
        file: sprite/does_not_exist.json
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "missing_file")
    # status=ready (기본) 이지만 file 이 디스크에 없으면 available=False
    assert v.status == "ready"
    assert v.available is False


def test_needs_api_conversion_variant(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  pixel_bg:
    variants:
      sdxl:
        ui_file: pixel_bg/c1.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("pixel_bg", "sdxl")
    assert v.available is False
    assert v.status == "needs_api_conversion"
    needing = reg.variants_needing_conversion()
    assert len(needing) == 1
    assert needing[0].name == "sdxl"


def test_ready_status_requires_file_field(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      bad:
        outputs: []
""",
    )
    with pytest.raises(WorkflowRegistryError, match="status=ready 인데 file 미지정"):
        WorkflowRegistry(root=tmp_path)


def test_invalid_status_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        status: weird_state
        outputs:
          - { node_title: "X", label: x, primary: true }
""",
    )
    with pytest.raises(WorkflowRegistryError, match="invalid status"):
        WorkflowRegistry(root=tmp_path)


# ----------------------------------------------------------------------------
# preset 치환
# ----------------------------------------------------------------------------


def test_preset_substitution(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
presets:
  NEG_BASE: "blurry, low quality"
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
        defaults:
          negative_prompt: ${preset:NEG_BASE}
          steps: 25
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert v.defaults["negative_prompt"] == "blurry, low quality"
    assert v.defaults["steps"] == 25  # non-string 그대로


def test_unknown_preset_raises(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
        defaults:
          negative_prompt: ${preset:DOES_NOT_EXIST}
""",
    )
    with pytest.raises(WorkflowRegistryError, match="unknown preset"):
        WorkflowRegistry(root=tmp_path)


# ----------------------------------------------------------------------------
# primary 자동 선택
# ----------------------------------------------------------------------------


def test_primary_variant_picks_explicit_primary(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/a.json")
    _write_workflow(tmp_path, "sprite/b.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      a:
        file: sprite/a.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
      b:
        file: sprite/b.json
        primary: true
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.category("sprite")
    assert cat.primary_variant is not None
    assert cat.primary_variant.name == "b"  # explicit primary 가 우선


def test_primary_variant_falls_back_to_first_available(tmp_path: Path) -> None:
    # 첫번째 변형은 file 미존재 → available=False, 두번째가 primary 자동선택돼야 함
    _write_workflow(tmp_path, "sprite/b.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      a:
        file: sprite/missing.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
      b:
        file: sprite/b.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.category("sprite")
    assert cat.primary_variant.name == "b"


def test_primary_variant_none_when_all_unavailable(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  pixel_bg:
    variants:
      x:
        ui_file: pixel_bg/x.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    assert reg.category("pixel_bg").primary_variant is None


# ----------------------------------------------------------------------------
# load_api_json
# ----------------------------------------------------------------------------


def test_load_api_json_returns_dict(tmp_path: Path) -> None:
    payload = {"3": {"class_type": "KSampler", "inputs": {"seed": 99}}}
    _write_workflow(tmp_path, "sprite/v.json", payload)
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    wf = v.load_api_json()
    assert wf == payload


def test_load_api_json_raises_when_unavailable(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  pixel_bg:
    variants:
      x:
        ui_file: pixel_bg/x.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("pixel_bg", "x")
    with pytest.raises(WorkflowRegistryError, match="호출 불가"):
        v.load_api_json()


def test_load_api_json_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "sprite" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not json", encoding="utf-8")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/bad.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    with pytest.raises(WorkflowRegistryError, match="workflow JSON 로드 실패"):
        reg.variant("sprite", "v").load_api_json()


# ----------------------------------------------------------------------------
# 잘못된 출력 / 변형 룩업
# ----------------------------------------------------------------------------


def test_unknown_category_raises(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    with pytest.raises(WorkflowRegistryError, match="unknown category"):
        reg.category("missing")
    with pytest.raises(WorkflowRegistryError, match="unknown variant"):
        reg.variant("sprite", "no_such")


def test_output_entry_requires_node_title_and_label(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Only" }
""",
    )
    with pytest.raises(WorkflowRegistryError, match="needs both"):
        WorkflowRegistry(root=tmp_path)


# ----------------------------------------------------------------------------
# to_catalog (REST 응답)
# ----------------------------------------------------------------------------


def test_to_catalog_shape(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/a.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    description: "스프라이트"
    variants:
      a:
        description: "v a"
        file: sprite/a.json
        primary: true
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
          - { node_title: "Save HiRes",  label: hires }
        defaults:
          steps: 30
  pixel_bg:
    description: "픽셀 배경"
    variants:
      x:
        ui_file: pixel_bg/x.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.to_catalog()
    assert cat["version"] == 1
    sprite = cat["categories"]["sprite"]
    assert sprite["description"] == "스프라이트"
    assert sprite["primary_variant"] == "a"
    a = sprite["variants"]["a"]
    assert a["available"] is True
    assert a["status"] == "ready"
    assert a["primary"] is True
    assert a["outputs"] == [
        {"label": "stage1", "primary": True},
        {"label": "hires", "primary": False},
    ]
    pb = cat["categories"]["pixel_bg"]
    assert pb["primary_variant"] is None
    assert pb["variants"]["x"]["available"] is False
    assert pb["variants"]["x"]["status"] == "needs_api_conversion"


# ----------------------------------------------------------------------------
# 실 매니페스트 sanity (workflows/registry.yml 이 깨지지 않았나)
# ----------------------------------------------------------------------------


def test_real_registry_loads(tmp_path: Path) -> None:
    """레포의 실제 ``workflows/registry.yml`` 로드 자체는 성공해야 한다.

    카테고리 4개 (sprite/illustration/pixel_bg/icon) 가 모두 보이고,
    sprite 의 primary 가 ``pixel_alpha`` 인지만 검증.
    """
    repo_root = Path(__file__).resolve().parent.parent
    registry_root = repo_root / "workflows"
    if not (registry_root / "registry.yml").exists():
        pytest.skip("workflows/registry.yml 이 없음 (PoC 미배포 환경)")
    reg = WorkflowRegistry(root=registry_root)
    cats = set(reg.categories.keys())
    assert {"sprite", "illustration", "pixel_bg", "icon"} <= cats
    sprite_primary = reg.category("sprite").primary_variant
    assert sprite_primary is not None
    assert sprite_primary.name == "pixel_alpha"


def test_get_default_registry_singleton(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    a = get_default_registry(tmp_path)
    b = get_default_registry()
    # 같은 root 로 한번만 만들면, 후속 호출은 캐시된 인스턴스를 반환 (Note: get_default_registry
    # 는 root 인자가 주어지면 새로 만든다 — singleton 의 reset semantics 확인)
    # 위 호출에서 b 는 cached singleton
    assert isinstance(a, WorkflowRegistry)
    assert isinstance(b, WorkflowRegistry)


# ----------------------------------------------------------------------------
# input_labels — 워크플로우 JSON 자동 추론 + YAML override
# ----------------------------------------------------------------------------


def _workflow_with_load_images(*titles_and_defaults: tuple[str, str]) -> dict:
    """LoadImage 노드들을 가진 워크플로우 JSON 픽스처 생성.

    각 인자는 (title, image_filename). class_type=LoadImage 로 고정.
    """
    wf: dict = {}
    for idx, (title, image) in enumerate(titles_and_defaults, start=1):
        wf[str(idx)] = {
            "class_type": "LoadImage",
            "_meta": {"title": title},
            "inputs": {"image": image},
        }
    # KSampler 한 개도 같이 — LoadImage 만 있는 게 아니라는 사실 확인용
    wf[str(len(titles_and_defaults) + 1)] = {
        "class_type": "KSampler",
        "_meta": {"title": "KSampler"},
        "inputs": {"seed": 0},
    }
    return wf


def test_infer_input_labels_from_workflow_json() -> None:
    wf = _workflow_with_load_images(
        ("Pose grid (ControlNet input)", "pose_grid.png"),
        ("Load source image", "source.png"),
        ("Some unrelated LoadImage", "other.png"),  # 매칭 안 됨 → 무시
    )
    labels = infer_input_labels(wf)
    by_label = {il.label: il for il in labels}
    assert set(by_label.keys()) == {"pose_image", "source_image"}
    assert by_label["pose_image"].default == "pose_grid.png"
    assert by_label["source_image"].default == "source.png"
    assert by_label["pose_image"].required is False
    assert by_label["pose_image"].description == ""


def test_input_labels_inferred_at_load(tmp_path: Path) -> None:
    payload = _workflow_with_load_images(
        ("Pose grid (mini 1280x640)", "pose_grid.png"),
    )
    _write_workflow(tmp_path, "sprite/v.json", payload)
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert len(v.input_labels) == 1
    il = v.input_labels[0]
    assert il.label == "pose_image"
    assert il.default == "pose_grid.png"
    assert il.description == ""


def test_yaml_overrides_inferred_metadata(tmp_path: Path) -> None:
    payload = _workflow_with_load_images(
        ("Pose grid (ControlNet input)", "pose_grid.png"),
    )
    _write_workflow(tmp_path, "sprite/v.json", payload)
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
        input_labels:
          - label: pose_image
            description: "Pose grid (ControlNet OpenPose 입력)"
            required: true
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    il = {x.label: x for x in v.input_labels}["pose_image"]
    assert il.description == "Pose grid (ControlNet OpenPose 입력)"
    assert il.required is True
    # default 는 YAML 미지정 → 추론값 유지
    assert il.default == "pose_grid.png"


def test_yaml_label_not_in_workflow_raises(tmp_path: Path) -> None:
    payload = _workflow_with_load_images(
        ("Pose grid", "pose_grid.png"),
    )
    _write_workflow(tmp_path, "sprite/v.json", payload)
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
        input_labels:
          - label: nonexistent_label
            description: "should fail"
""",
    )
    with pytest.raises(WorkflowRegistryError, match="unknown label"):
        WorkflowRegistry(root=tmp_path)


def test_catalog_emits_input_labels(tmp_path: Path) -> None:
    payload = _workflow_with_load_images(
        ("Pose grid", "default_pose.png"),
    )
    _write_workflow(tmp_path, "sprite/v.json", payload)
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.to_catalog()
    v = cat["categories"]["sprite"]["variants"]["v"]
    assert v["input_labels"] == [
        {
            "label": "pose_image",
            "required": False,
            "default": "default_pose.png",
            "description": "",
        }
    ]


def test_input_labels_empty_when_no_load_image_nodes(tmp_path: Path) -> None:
    """LoadImage 노드 없는 변형은 빈 input_labels."""
    _write_workflow(
        tmp_path,
        "sprite/v.json",
        {"3": {"class_type": "KSampler", "inputs": {"seed": 0}}},
    )
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save", label: out, primary: true }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert v.input_labels == ()


def test_variant_spec_immutable(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    # frozen dataclass — 필드 변경은 FrozenInstanceError
    with pytest.raises(Exception):
        v.name = "other"  # type: ignore[misc]
    assert isinstance(v, VariantSpec)


# ----------------------------------------------------------------------------
# 디스커버리 메타 — use_cases / tags / prompt_template / cost / pitfalls / related
# ----------------------------------------------------------------------------


def test_meta_absent_keeps_defaults(tmp_path: Path) -> None:
    """메타 미작성 변형은 None / 빈 tuple — 기존 호출 호환성 확보."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert v.use_cases == ()
    assert v.not_for == ()
    assert v.tags is None
    assert v.character_proportions is None
    assert v.prompt_template is None
    assert v.cost is None
    assert v.pitfalls == ()
    assert v.related is None


def test_meta_full_roundtrip(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_workflow(tmp_path, "sprite/other.json")
    _write_workflow(tmp_path, "sprite/upstream_one.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        use_cases:
          - "use case A"
          - "use case B"
        not_for:
          - "anti use 1"
        tags:
          kind: character
          style: pixel-art
          format: multi-view-1x3
          output: alpha-pixel
          model_family: illustrious
        character_proportions:
          head_ratio: 2.5
          chibi: true
        prompt_template:
          skeleton: |
            {gender}, {hair}, masterpiece
          model_triggers:
            - "score_9, score_8_up"
          required_tokens:
            - { placeholder: "{gender}", examples: ["1girl", "1boy"] }
            - placeholder: "{hair}"
              examples: ["black hair"]
          forbidden_tokens:
            - { token: "(chibi:1.4)", reason: "ControlNet 충돌" }
          examples:
            - prompt: "1girl, black hair, masterpiece"
              seed: 42
              note: "검증된 prompt"
        cost:
          est_seconds: 45
          vram_gb: 12
        pitfalls:
          - "pitfall A"
        related:
          sibling: [sprite/other]
          upstream: [sprite/upstream_one]
          downstream: []
      other:
        file: sprite/other.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
      upstream_one:
        file: sprite/upstream_one.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert v.use_cases == ("use case A", "use case B")
    assert v.not_for == ("anti use 1",)
    assert v.tags == TagSet(
        kind="character",
        style="pixel-art",
        format="multi-view-1x3",
        output="alpha-pixel",
        model_family="illustrious",
    )
    assert v.character_proportions == {"head_ratio": 2.5, "chibi": True}
    assert v.prompt_template is not None
    assert "{gender}, {hair}" in v.prompt_template.skeleton
    assert v.prompt_template.model_triggers == ("score_9, score_8_up",)
    assert v.prompt_template.required_tokens == (
        PromptToken(placeholder="{gender}", examples=("1girl", "1boy")),
        PromptToken(placeholder="{hair}", examples=("black hair",)),
    )
    assert v.prompt_template.forbidden_tokens == (
        ForbiddenToken(token="(chibi:1.4)", reason="ControlNet 충돌"),
    )
    assert v.prompt_template.examples == (
        PromptExample(
            prompt="1girl, black hair, masterpiece", seed=42, note="검증된 prompt"
        ),
    )
    assert v.cost == CostEstimate(est_seconds=45, vram_gb=12.0)
    assert v.pitfalls == ("pitfall A",)
    assert v.related == RelatedRefs(
        sibling=("sprite/other",),
        upstream=("sprite/upstream_one",),
        downstream=(),
    )


def test_catalog_emits_full_meta(tmp_path: Path) -> None:
    """to_catalog 가 신규 메타 필드를 모두 직렬화."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_workflow(tmp_path, "sprite/x.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        use_cases: ["uc 1"]
        tags: { kind: character, style: pixel-art }
        character_proportions: { head_ratio: 2.5 }
        prompt_template:
          skeleton: "skeleton"
          model_triggers: ["t1"]
          required_tokens:
            - { placeholder: "{x}", examples: ["a", "b"] }
          forbidden_tokens:
            - { token: "bad", reason: "why" }
          examples:
            - { prompt: "p", seed: 1, note: "n" }
        cost: { est_seconds: 10, vram_gb: 8 }
        pitfalls: ["pf"]
        related: { sibling: ["sprite/x"] }
      x:
        file: sprite/x.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.to_catalog()
    v = cat["categories"]["sprite"]["variants"]["v"]
    assert v["use_cases"] == ["uc 1"]
    assert v["not_for"] == []
    assert v["tags"] == {
        "kind": "character",
        "style": "pixel-art",
        "format": None,
        "output": None,
        "model_family": None,
    }
    assert v["character_proportions"] == {"head_ratio": 2.5}
    assert v["prompt_template"] == {
        "skeleton": "skeleton",
        "model_triggers": ["t1"],
        "required_tokens": [{"placeholder": "{x}", "examples": ["a", "b"]}],
        "forbidden_tokens": [{"token": "bad", "reason": "why"}],
        "examples": [{"prompt": "p", "seed": 1, "note": "n"}],
    }
    assert v["cost"] == {"est_seconds": 10, "vram_gb": 8.0}
    assert v["pitfalls"] == ["pf"]
    assert v["related"] == {
        "sibling": ["sprite/x"],
        "upstream": [],
        "downstream": [],
    }


def test_catalog_emits_none_for_absent_meta(tmp_path: Path) -> None:
    """메타 미작성 변형의 catalog 직렬화 — None / 빈 list 로 노출."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.to_catalog()["categories"]["sprite"]["variants"]["v"]
    assert v["use_cases"] == []
    assert v["not_for"] == []
    assert v["tags"] is None
    assert v["character_proportions"] is None
    assert v["prompt_template"] is None
    assert v["cost"] is None
    assert v["pitfalls"] == []
    assert v["related"] is None


def test_invalid_tag_key_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        tags:
          kind: character
          weird_key: foo
""",
    )
    with pytest.raises(WorkflowRegistryError, match="unknown keys"):
        WorkflowRegistry(root=tmp_path)


def test_forbidden_token_requires_reason(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        prompt_template:
          forbidden_tokens:
            - { token: "(chibi:1.4)" }
""",
    )
    with pytest.raises(WorkflowRegistryError, match="`token` and `reason`"):
        WorkflowRegistry(root=tmp_path)


def test_required_token_requires_placeholder(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        prompt_template:
          required_tokens:
            - { examples: ["1girl"] }
""",
    )
    with pytest.raises(WorkflowRegistryError, match="needs `placeholder`"):
        WorkflowRegistry(root=tmp_path)


def test_cost_must_be_numbers(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        cost:
          est_seconds: "not a number"
""",
    )
    with pytest.raises(WorkflowRegistryError, match="est_seconds"):
        WorkflowRegistry(root=tmp_path)


def test_cost_rejects_bool_for_est_seconds(tmp_path: Path) -> None:
    """YAML 의 `est_seconds: yes` 가 True (= 1) 로 silent 통과 방지."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        cost:
          est_seconds: true
""",
    )
    with pytest.raises(WorkflowRegistryError, match="est_seconds"):
        WorkflowRegistry(root=tmp_path)


def test_cost_rejects_bool_for_vram_gb(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        cost:
          vram_gb: true
""",
    )
    with pytest.raises(WorkflowRegistryError, match="vram_gb"):
        WorkflowRegistry(root=tmp_path)


def test_related_unknown_key_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        related:
          sibling: [sprite/x]
          unknown_rel: foo
""",
    )
    with pytest.raises(WorkflowRegistryError, match="unknown keys"):
        WorkflowRegistry(root=tmp_path)


def test_related_dead_ref_rejected(tmp_path: Path) -> None:
    """``related.sibling/upstream/downstream`` 가 존재하지 않는 변형 참조 시 startup 실패.

    오타 (예: ``sprite/pixel_alpa``) 가 silent 통과해 런타임에 dead ref 로
    깨지는 걸 방지.
    """
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        related:
          sibling: [sprite/does_not_exist]
""",
    )
    with pytest.raises(WorkflowRegistryError, match="존재하지 않는 변형 참조"):
        WorkflowRegistry(root=tmp_path)


def test_related_dead_ref_in_upstream_rejected(tmp_path: Path) -> None:
    """``related.upstream`` 의 dead ref 도 동일하게 startup 실패."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        related:
          upstream: [illustration/typo_variant]
""",
    )
    with pytest.raises(WorkflowRegistryError, match="related.upstream"):
        WorkflowRegistry(root=tmp_path)


def test_related_cross_category_ref_ok(tmp_path: Path) -> None:
    """``related.sibling`` 가 다른 카테고리의 실제 변형을 참조하면 통과."""
    _write_workflow(tmp_path, "sprite/v.json")
    _write_workflow(tmp_path, "illustration/i.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        related:
          sibling: [illustration/i]
  illustration:
    variants:
      i:
        file: illustration/i.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    assert reg.variant("sprite", "v").related is not None
    assert reg.variant("sprite", "v").related.sibling == ("illustration/i",)


def test_real_registry_all_variants_have_meta(tmp_path: Path) -> None:
    """레포의 실제 매니페스트 — 25 변형 모두 디스커버리 메타 작성됨."""
    repo_root = Path(__file__).resolve().parent.parent
    registry_root = repo_root / "workflows"
    if not (registry_root / "registry.yml").exists():
        pytest.skip("workflows/registry.yml 이 없음")
    reg = WorkflowRegistry(root=registry_root)
    missing: list[str] = []
    for cname, cat in reg.categories.items():
        for vname, v in cat.variants.items():
            if v.tags is None or not v.use_cases or v.cost is None:
                missing.append(f"{cname}/{vname}")
    assert not missing, f"메타 누락 변형: {missing}"


# ----------------------------------------------------------------------------
# recommend (rule-based filter)
# ----------------------------------------------------------------------------


def _recommend_fixture(tmp_path: Path) -> WorkflowRegistry:
    """sprite/char (character + pixel-art), illust/anime (illustration + anime),
    sprite/no_meta (메타 없음 — 필터 적용 시 매칭 불가) 를 가진 미니 레지스트리."""
    _write_workflow(tmp_path, "a.json")
    _write_workflow(tmp_path, "b.json")
    _write_workflow(tmp_path, "c.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      char:
        file: a.json
        primary: true
        outputs: [{ node_title: "Save", label: out, primary: true }]
        use_cases: ["pixel character"]
        tags: { kind: character, style: pixel-art, model_family: illustrious }
        cost: { est_seconds: 45, vram_gb: 12 }
      no_meta:
        file: b.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
  illust:
    variants:
      anime:
        file: c.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        use_cases: ["anime illust"]
        tags: { kind: illustration, style: anime, model_family: pony }
        cost: { est_seconds: 50, vram_gb: 12 }
""",
    )
    return WorkflowRegistry(root=tmp_path)


def test_recommend_no_filters_returns_all_available(tmp_path: Path) -> None:
    reg = _recommend_fixture(tmp_path)
    matches = reg.recommend()
    names = {(m["category"], m["variant"]) for m in matches}
    # no_meta 는 메타 없지만 필터 없으면 포함 (available 만)
    assert names == {("sprite", "char"), ("sprite", "no_meta"), ("illust", "anime")}
    # 응답 shape — score / matched_axes 노출 안 함 (strict-AND)
    assert "score" not in matches[0]
    assert "matched_axes" not in matches[0]


def test_recommend_single_filter(tmp_path: Path) -> None:
    reg = _recommend_fixture(tmp_path)
    matches = reg.recommend(kind="character")
    assert len(matches) == 1
    m = matches[0]
    assert m["category"] == "sprite"
    assert m["variant"] == "char"
    assert m["tags"]["kind"] == "character"
    assert "use_cases" in m and "cost" in m


def test_recommend_multi_filter_all_match(tmp_path: Path) -> None:
    reg = _recommend_fixture(tmp_path)
    matches = reg.recommend(kind="character", style="pixel-art")
    assert len(matches) == 1
    assert matches[0]["variant"] == "char"


def test_recommend_filter_no_match(tmp_path: Path) -> None:
    reg = _recommend_fixture(tmp_path)
    # character 인데 anime 스타일 — strict-AND, 어느 변형도 매칭 안 됨
    matches = reg.recommend(kind="character", style="anime")
    assert matches == []


def test_recommend_strict_and_one_axis_mismatch_excludes(tmp_path: Path) -> None:
    """한 축이라도 mismatch 면 제외 (partial 매치 안 함)."""
    reg = _recommend_fixture(tmp_path)
    # sprite/char 는 kind=character / style=pixel-art / model_family=illustrious
    # style 만 일부러 다르게 → partial 매치라면 score<1 로 들어왔을 텐데, strict 라 빈 결과
    matches = reg.recommend(kind="character", style="anime")
    assert matches == []


def test_recommend_excludes_no_meta_when_filtered(tmp_path: Path) -> None:
    reg = _recommend_fixture(tmp_path)
    # 메타 없는 변형 (sprite/no_meta) 은 필터 적용 시 제외됨
    matches = reg.recommend(model_family="illustrious")
    names = {(m["category"], m["variant"]) for m in matches}
    assert names == {("sprite", "char")}


def test_recommend_primary_sorted_first(tmp_path: Path) -> None:
    """primary=True 가 첫 번째."""
    _write_workflow(tmp_path, "a.json")
    _write_workflow(tmp_path, "b.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      a:
        file: a.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        tags: { kind: character }
      b:
        file: b.json
        primary: true
        outputs: [{ node_title: "Save", label: out, primary: true }]
        tags: { kind: character }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    matches = reg.recommend(kind="character")
    # b 가 primary → 첫 번째
    assert matches[0]["variant"] == "b"
    assert matches[1]["variant"] == "a"


def test_recommend_excludes_unavailable_variants(tmp_path: Path) -> None:
    """file 없는 (available=False) 변형은 결과에서 제외."""
    _write_workflow(tmp_path, "exists.json")
    _write_manifest(
        tmp_path,
        """
version: 1
categories:
  sprite:
    variants:
      ok:
        file: exists.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        tags: { kind: character }
      missing:
        file: gone.json
        outputs: [{ node_title: "Save", label: out, primary: true }]
        tags: { kind: character }
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    matches = reg.recommend(kind="character")
    names = {m["variant"] for m in matches}
    assert names == {"ok"}
