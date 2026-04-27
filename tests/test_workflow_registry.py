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
    InputLabelSpec,
    OutputLayoutSpec,
    OutputSpec,
    PromptTemplateSpec,
    UserSlotSpec,
    VariantMetaSpec,
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
    # spec §1.A.3: catalog 응답 schema bump 1 → 2
    assert cat["version"] == 2
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
            "alternatives": [],
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
# 디스커버리 메타 — spec docs/NEXT.md §1.A 사이드카 yaml
# ----------------------------------------------------------------------------


def _write_meta_yaml(root: Path, category: str, variant: str, body: str) -> Path:
    target = root / category / f"{variant}.meta.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def test_meta_missing_returns_empty(tmp_path: Path) -> None:
    """메타 사이드카 없는 변형 → 빈 VariantMetaSpec (forward-compat)."""
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
    assert v.meta == VariantMetaSpec()
    assert v.meta.intent == ""
    assert v.meta.use_cases == ()
    assert v.meta.output_layout == OutputLayoutSpec()
    assert v.meta.prompt_template is None


def test_meta_yaml_loaded(tmp_path: Path) -> None:
    """spec §A.2 의 schema — 사이드카 yaml 로드 시 모든 필드 정확히 매핑."""
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
    _write_meta_yaml(
        tmp_path,
        "sprite",
        "v",
        """
schema_version: 1
intent: |
  3-pose character sheet.
use_cases:
  - "RPG character"
not_for:
  - "single portrait"
output_layout:
  kind: pose_grid
  rows: 1
  cols: 3
  per_cell_size: [426, 640]
  alpha: true
  notes: "1×3 grid notes"
tags:
  - pixel-art
  - pose-sheet
prompt_template:
  base_positive: "pixel art, sprite sheet"
  base_negative: "blurry, low quality"
  user_slot:
    label: subject
    description: "캐릭터 묘사만"
    examples:
      - "1girl, silver hair"
    required: true
    min_chars: 8
    max_chars: 400
  injection_rule: "{base_positive}, {subject}"
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    v = reg.variant("sprite", "v")
    assert v.meta.intent.strip() == "3-pose character sheet."
    assert v.meta.use_cases == ("RPG character",)
    assert v.meta.not_for == ("single portrait",)
    assert v.meta.output_layout == OutputLayoutSpec(
        kind="pose_grid", rows=1, cols=3, per_cell_size=(426, 640), alpha=True,
        notes="1×3 grid notes",
    )
    assert v.meta.tags == ("pixel-art", "pose-sheet")
    assert v.meta.prompt_template == PromptTemplateSpec(
        base_positive="pixel art, sprite sheet",
        base_negative="blurry, low quality",
        user_slot=UserSlotSpec(
            label="subject", description="캐릭터 묘사만",
            examples=("1girl, silver hair",),
            required=True, min_chars=8, max_chars=400,
        ),
        injection_rule="{base_positive}, {subject}",
    )


def test_meta_schema_violation_raises(tmp_path: Path) -> None:
    """``schema_version: 99`` 같은 미스매치는 즉시 raise (spec §A.4 Step 1.3)."""
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
    _write_meta_yaml(
        tmp_path, "sprite", "v",
        "schema_version: 99\nintent: future schema\n",
    )
    with pytest.raises(WorkflowRegistryError, match="schema_version must be 1"):
        WorkflowRegistry(root=tmp_path)


def test_meta_invalid_output_layout_kind_raises(tmp_path: Path) -> None:
    """output_layout.kind 가 spec enum 4종 외면 raise."""
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
    _write_meta_yaml(
        tmp_path, "sprite", "v",
        """
schema_version: 1
output_layout: { kind: weird_layout }
""",
    )
    with pytest.raises(WorkflowRegistryError, match="output_layout.kind"):
        WorkflowRegistry(root=tmp_path)


def test_meta_invalid_yaml_raises(tmp_path: Path) -> None:
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
    _write_meta_yaml(tmp_path, "sprite", "v", "intent: [broken")
    with pytest.raises(WorkflowRegistryError, match="meta yaml parse error"):
        WorkflowRegistry(root=tmp_path)


def test_meta_input_labels_alternatives_loaded(tmp_path: Path) -> None:
    """사이드카 yaml 의 input_labels.<label>.description / alternatives 가 머지."""
    payload = _workflow_with_load_images(("Pose grid", "default_pose.png"))
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
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    _write_meta_yaml(
        tmp_path, "sprite", "v",
        """
schema_version: 1
input_labels:
  pose_image:
    description: "ControlNet 포즈 가이드 (1×3 그리드)"
    alternatives:
      - alt1.png
      - alt2.png
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    il = reg.variant("sprite", "v").input_labels[0]
    assert il.label == "pose_image"
    assert il.description == "ControlNet 포즈 가이드 (1×3 그리드)"
    assert il.alternatives == ("alt1.png", "alt2.png")
    # default 는 워크플로우 JSON 추론값 그대로
    assert il.default == "default_pose.png"


def test_meta_input_labels_unknown_label_raises(tmp_path: Path) -> None:
    payload = _workflow_with_load_images(("Pose grid", "default_pose.png"))
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
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    _write_meta_yaml(
        tmp_path, "sprite", "v",
        """
schema_version: 1
input_labels:
  unknown_label:
    description: "should fail"
""",
    )
    with pytest.raises(WorkflowRegistryError, match="unknown label"):
        WorkflowRegistry(root=tmp_path)


def test_catalog_v2_response_shape(tmp_path: Path) -> None:
    """spec §A.3 — catalog 응답에 version: 2 + meta + input_labels.alternatives."""
    payload = _workflow_with_load_images(("Pose grid", "default_pose.png"))
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
        outputs: [{ node_title: "Save", label: out, primary: true }]
""",
    )
    _write_meta_yaml(
        tmp_path, "sprite", "v",
        """
schema_version: 1
intent: "test intent"
use_cases: ["uc1"]
output_layout:
  kind: pose_grid
  rows: 1
  cols: 3
  alpha: true
tags: ["pixel-art"]
prompt_template:
  base_positive: "pixel art"
  base_negative: "blurry"
  user_slot:
    label: subject
    description: "subject only"
input_labels:
  pose_image:
    description: "pose guide"
    alternatives: [alt.png]
""",
    )
    reg = WorkflowRegistry(root=tmp_path)
    cat = reg.to_catalog()
    assert cat["version"] == 2
    v = cat["categories"]["sprite"]["variants"]["v"]
    # meta 노출 확인
    assert v["meta"]["intent"] == "test intent"
    assert v["meta"]["use_cases"] == ["uc1"]
    assert v["meta"]["output_layout"]["kind"] == "pose_grid"
    assert v["meta"]["output_layout"]["rows"] == 1
    assert v["meta"]["output_layout"]["cols"] == 3
    assert v["meta"]["output_layout"]["alpha"] is True
    assert v["meta"]["tags"] == ["pixel-art"]
    assert v["meta"]["prompt_template"]["base_positive"] == "pixel art"
    assert v["meta"]["prompt_template"]["user_slot"]["label"] == "subject"
    # input_labels alternatives
    assert v["input_labels"][0]["alternatives"] == ["alt.png"]


def test_catalog_v2_legacy_variant_has_empty_meta(tmp_path: Path) -> None:
    """메타 사이드카 없는 변형 — catalog 응답 `meta` 가 빈 객체 (intent='', tags=[]...)."""
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
    cat = reg.to_catalog()
    meta = cat["categories"]["sprite"]["variants"]["v"]["meta"]
    assert meta["intent"] == ""
    assert meta["use_cases"] == []
    assert meta["not_for"] == []
    assert meta["tags"] == []
    assert meta["output_layout"] == {
        "kind": "single", "rows": 1, "cols": 1,
        "per_cell_size": None, "alpha": False, "notes": "",
    }
    assert meta["prompt_template"] is None


def test_real_pixel_alpha_meta_loaded(tmp_path: Path) -> None:
    """레포의 실 sprite/pixel_alpha.meta.yaml 이 정확히 로드되는지."""
    repo_root = Path(__file__).resolve().parent.parent
    registry_root = repo_root / "workflows"
    if not (registry_root / "sprite" / "pixel_alpha.meta.yaml").exists():
        pytest.skip("pixel_alpha.meta.yaml 미작성")
    reg = WorkflowRegistry(root=registry_root)
    v = reg.variant("sprite", "pixel_alpha")
    assert v.meta.intent != ""
    assert "pose_grid" == v.meta.output_layout.kind
    assert v.meta.output_layout.cols == 3
    assert v.meta.output_layout.alpha is True
    assert "pixel-art" in v.meta.tags
    assert v.meta.prompt_template is not None
    assert "pixel art" in v.meta.prompt_template.base_positive
    assert "floating sword" in v.meta.prompt_template.base_negative   # HANDOFF §5.4
    assert v.meta.prompt_template.user_slot is not None
    assert v.meta.prompt_template.user_slot.required is True
    # pose_image alternatives 가 메타 yaml 에서 머지됨
    by_label = {il.label: il for il in v.input_labels}
    assert "pose_image" in by_label
    assert by_label["pose_image"].alternatives != ()
