"""ValidationPolicy / OutputValidationRule 단위 테스트.

workflow_registry.py 의 ValidationPolicy 파싱 + for_output 룩업을 검증한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_registry import (
    OutputValidationRule,
    ValidationPolicy,
    WorkflowRegistry,
    WorkflowRegistryError,
)


# ──────────────────────────────────────────────────────────────
# fixture helpers
# ──────────────────────────────────────────────────────────────

_MINIMAL_WORKFLOW = json.dumps({
    "1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
          "_meta": {"title": "Save Stage1"}},
})

_PIXEL_ALPHA_WORKFLOW = json.dumps({
    "1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
          "_meta": {"title": "Save Stage1"}},
    "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
          "_meta": {"title": "Save Pixelized"}},
    "3": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
          "_meta": {"title": "Save PixelAlpha"}},
})


def _make_registry(tmp_path: Path, yml: str) -> WorkflowRegistry:
    (tmp_path / "sprite").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sprite" / "v.json").write_text(_MINIMAL_WORKFLOW, encoding="utf-8")
    (tmp_path / "sprite" / "pa.json").write_text(_PIXEL_ALPHA_WORKFLOW, encoding="utf-8")
    manifest = tmp_path / "registry.yml"
    manifest.write_text(yml, encoding="utf-8")
    return WorkflowRegistry(root=tmp_path, manifest_path=manifest)


# ──────────────────────────────────────────────────────────────
# ValidationPolicy.for_output
# ──────────────────────────────────────────────────────────────

def test_for_output_returns_explicit_rule() -> None:
    rule_pa = OutputValidationRule(max_colors=128, require_alpha=True)
    policy = ValidationPolicy(
        default=OutputValidationRule(max_colors=None),
        outputs={"pixel_alpha": rule_pa},
    )
    assert policy.for_output("pixel_alpha") is rule_pa


def test_for_output_falls_back_to_default() -> None:
    default_rule = OutputValidationRule(max_colors=None, require_alpha=False)
    policy = ValidationPolicy(default=default_rule, outputs={})
    assert policy.for_output("stage1") is default_rule
    assert policy.for_output("unknown_label") is default_rule


def test_for_output_default_policy_is_32() -> None:
    policy = ValidationPolicy()
    rule = policy.for_output("stage1")
    assert rule.max_colors == 32
    assert rule.require_alpha is False


# ──────────────────────────────────────────────────────────────
# Registry parsing — validation 블록 부재
# ──────────────────────────────────────────────────────────────

def test_registry_no_validation_block_uses_default_32(tmp_path: Path) -> None:
    yml = """
version: 1
categories:
  sprite:
    description: test
    variants:
      v:
        description: test variant
        file: sprite/v.json
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
        defaults: {}
"""
    reg = _make_registry(tmp_path, yml)
    variant = reg.variant("sprite", "v")
    rule = variant.validation.for_output("stage1")
    assert rule.max_colors == 32
    assert rule.require_alpha is False


# ──────────────────────────────────────────────────────────────
# Registry parsing — validation 블록 정상 파싱
# ──────────────────────────────────────────────────────────────

def test_registry_parses_validation_outputs(tmp_path: Path) -> None:
    yml = """
version: 1
categories:
  sprite:
    description: test
    variants:
      pa:
        description: pixel alpha test
        file: sprite/pa.json
        outputs:
          - { node_title: "Save Stage1",     label: stage1 }
          - { node_title: "Save Pixelized",  label: pixelized }
          - { node_title: "Save PixelAlpha", label: pixel_alpha, primary: true }
        validation:
          default: { max_colors: null, require_alpha: false }
          outputs:
            pixelized:   { max_colors: 128 }
            pixel_alpha: { max_colors: 128, require_alpha: true }
        defaults: {}
"""
    reg = _make_registry(tmp_path, yml)
    variant = reg.variant("sprite", "pa")

    stage1_rule = variant.validation.for_output("stage1")
    assert stage1_rule.max_colors is None
    assert stage1_rule.require_alpha is False

    pixelized_rule = variant.validation.for_output("pixelized")
    assert pixelized_rule.max_colors == 128
    assert pixelized_rule.require_alpha is False

    pa_rule = variant.validation.for_output("pixel_alpha")
    assert pa_rule.max_colors == 128
    assert pa_rule.require_alpha is True


def test_registry_null_max_colors_parses_to_none(tmp_path: Path) -> None:
    yml = """
version: 1
categories:
  sprite:
    description: test
    variants:
      v:
        description: test variant
        file: sprite/v.json
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
        validation:
          default: { max_colors: null }
        defaults: {}
"""
    reg = _make_registry(tmp_path, yml)
    rule = reg.variant("sprite", "v").validation.for_output("stage1")
    assert rule.max_colors is None


# ──────────────────────────────────────────────────────────────
# Registry parsing — unknown output label raises
# ──────────────────────────────────────────────────────────────

def test_registry_unknown_output_label_raises_at_load(tmp_path: Path) -> None:
    yml = """
version: 1
categories:
  sprite:
    description: test
    variants:
      v:
        description: test variant
        file: sprite/v.json
        outputs:
          - { node_title: "Save Stage1", label: stage1, primary: true }
        validation:
          outputs:
            nonexistent_label: { max_colors: 128 }
        defaults: {}
"""
    with pytest.raises(WorkflowRegistryError, match="nonexistent_label"):
        _make_registry(tmp_path, yml)


# ──────────────────────────────────────────────────────────────
# Full registry.yml loading — smoke test
# ──────────────────────────────────────────────────────────────

def test_real_registry_loads_and_policy_lookup_works() -> None:
    """실제 workflows/registry.yml 이 로드되고 정책 룩업이 올바르게 동작하는지 검증."""
    registry_root = Path(__file__).resolve().parent.parent / "workflows"
    reg = WorkflowRegistry(root=registry_root)

    # sprite/pixel_alpha: pixel_alpha 출력 → max_colors=128, require_alpha=True
    pa_variant = reg.variant("sprite", "pixel_alpha")
    pa_rule = pa_variant.validation.for_output("pixel_alpha")
    assert pa_rule.max_colors == 128
    assert pa_rule.require_alpha is True

    # sprite/pixel_alpha: stage1 출력 → max_colors=None
    s1_rule = pa_variant.validation.for_output("stage1")
    assert s1_rule.max_colors is None

    # icon/flat: stage1 → null, alpha → null + require_alpha
    flat_variant = reg.variant("icon", "flat")
    flat_stage1 = flat_variant.validation.for_output("stage1")
    assert flat_stage1.max_colors is None
    assert flat_stage1.require_alpha is False

    flat_alpha = flat_variant.validation.for_output("alpha")
    assert flat_alpha.max_colors is None
    assert flat_alpha.require_alpha is True

    # pixel_bg: 256
    sdxl = reg.variant("pixel_bg", "sdxl_hires")
    sdxl_rule = sdxl.validation.for_output("hires")
    assert sdxl_rule.max_colors == 256

    # illustration: null
    anim = reg.variant("illustration", "animagine_hires")
    anim_rule = anim.validation.for_output("hires")
    assert anim_rule.max_colors is None
