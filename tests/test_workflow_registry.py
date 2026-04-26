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
    OutputSpec,
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
