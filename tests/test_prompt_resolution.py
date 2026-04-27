"""§1.B subject-injection 단위 테스트 (`prompt_resolution.resolve_prompt`).

spec docs/NEXT.md §B.9 의 6 케이스 + 추가 검증 (자동 감지 / style_extra /
auto-detect heuristic / template 부재).

VariantSpec / PromptTemplateSpec / UserSlotSpec 은 in-memory 로 직접 생성 —
파일 의존 없음.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_resolution import (
    PromptResolution,
    PromptResolutionError,
    resolve_prompt,
)
from workflow_registry import (
    OutputLayoutSpec,
    OutputSpec,
    PromptTemplateSpec,
    UserSlotSpec,
    VariantMetaSpec,
    VariantSpec,
)


# ----------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------


def _variant_with_template(
    *,
    base_positive: str = "pixel art, sprite sheet",
    base_negative: str = "blurry, low quality",
    user_slot: UserSlotSpec | None = None,
    injection_rule: str = "{base_positive}, {subject}",
) -> VariantSpec:
    """prompt_template 이 있는 변형 (subject 모드 가능)."""
    return VariantSpec(
        category="sprite",
        name="test_variant",
        description="",
        file=Path("/tmp/_does_not_exist.json"),
        ui_file=None,
        status="ready",
        primary=False,
        outputs=(OutputSpec(node_title="Save", label="out", primary=True),),
        defaults={},
        meta=VariantMetaSpec(
            output_layout=OutputLayoutSpec(),
            prompt_template=PromptTemplateSpec(
                base_positive=base_positive,
                base_negative=base_negative,
                user_slot=user_slot or UserSlotSpec(
                    label="subject", required=True, min_chars=8, max_chars=400,
                ),
                injection_rule=injection_rule,
            ),
        ),
    )


def _variant_without_template() -> VariantSpec:
    """prompt_template 없는 변형 (legacy 모드만 가능)."""
    return VariantSpec(
        category="sprite",
        name="legacy_variant",
        description="",
        file=Path("/tmp/_does_not_exist.json"),
        ui_file=None,
        status="ready",
        primary=False,
        outputs=(OutputSpec(node_title="Save", label="out", primary=True),),
        defaults={},
        meta=VariantMetaSpec(),  # prompt_template = None
    )


# ----------------------------------------------------------------------------
# spec §B.9 핵심 6 케이스
# ----------------------------------------------------------------------------


def test_subject_mode_injects_base_positive() -> None:
    """subject 모드 — base_positive 가 합성 결과에 포함."""
    res = resolve_prompt(_variant_with_template(), subject="1girl, silver hair")
    assert res.mode == "subject"
    assert "pixel art" in res.final_positive
    assert "1girl, silver hair" in res.final_positive
    assert res.user_slot == "subject"
    assert res.user_input == "1girl, silver hair"


def test_legacy_mode_passes_through() -> None:
    """legacy 모드 — prompt 통째 그대로 final_positive 로."""
    res = resolve_prompt(
        _variant_with_template(),
        prompt="completely custom prompt with all keywords",
        prompt_mode="legacy",
    )
    assert res.mode == "legacy"
    assert res.final_positive == "completely custom prompt with all keywords"
    assert res.user_slot is None


def test_auto_detects_legacy_for_long_prompt() -> None:
    """auto 모드 — max_chars 초과 prompt 면 legacy 로 자동 분기."""
    long = "x " * 250  # > 400 chars (max_chars default)
    res = resolve_prompt(_variant_with_template(), prompt=long, prompt_mode="auto")
    assert res.mode == "legacy"


def test_negative_appended_not_replaced() -> None:
    """subject 모드의 user negative — base_negative 뒤에 추가만 (override 불가)."""
    res = resolve_prompt(
        _variant_with_template(),
        subject="1girl, hair",
        negative_prompt="extra negative tokens",
    )
    assert "blurry" in res.final_negative   # base 유지
    assert "extra negative tokens" in res.final_negative   # user 추가


def test_variant_without_template_falls_back_to_legacy() -> None:
    """prompt_template 없는 변형 — 자동 legacy 모드."""
    res = resolve_prompt(_variant_without_template(), prompt="user prompt")
    assert res.mode == "legacy"
    assert res.final_positive == "user prompt"


def test_subject_required_rejected() -> None:
    """required=True 인데 빈 subject — PromptResolutionError(subject_required)."""
    with pytest.raises(PromptResolutionError) as exc_info:
        resolve_prompt(_variant_with_template(), subject="")
    assert exc_info.value.code == "subject_required"


# ----------------------------------------------------------------------------
# 추가 케이스
# ----------------------------------------------------------------------------


def test_subject_min_chars_violation() -> None:
    """min_chars 위반 — subject_length_invalid."""
    v = _variant_with_template(
        user_slot=UserSlotSpec(label="subject", required=True, min_chars=20, max_chars=400),
    )
    with pytest.raises(PromptResolutionError) as exc_info:
        resolve_prompt(v, subject="too short")
    assert exc_info.value.code == "subject_length_invalid"


def test_subject_max_chars_violation() -> None:
    v = _variant_with_template(
        user_slot=UserSlotSpec(label="subject", required=True, min_chars=5, max_chars=20),
    )
    with pytest.raises(PromptResolutionError) as exc_info:
        resolve_prompt(v, subject="x" * 100)
    assert exc_info.value.code == "subject_length_invalid"


def test_explicit_subject_mode_with_template_missing_raises() -> None:
    """prompt_mode='subject' 인데 변형에 template 없으면 명시적 실패."""
    with pytest.raises(PromptResolutionError) as exc_info:
        resolve_prompt(
            _variant_without_template(),
            prompt="x", subject="x", prompt_mode="subject",
        )
    assert exc_info.value.code == "prompt_template_missing_for_variant"


def test_style_extra_appended_to_final_positive() -> None:
    """subject 모드 — style_extra 가 final_positive 끝에 추가."""
    res = resolve_prompt(
        _variant_with_template(),
        subject="1girl, hair",
        style_extra="cinematic lighting, dramatic",
    )
    assert res.mode == "subject"
    assert "cinematic lighting, dramatic" in res.final_positive
    assert res.final_positive.endswith("cinematic lighting, dramatic")


def test_injection_rule_subject_first() -> None:
    """injection_rule 이 ``{subject}, {base_positive}`` 패턴이면 subject 가 앞에."""
    v = _variant_with_template(injection_rule="{subject}, {base_positive}")
    res = resolve_prompt(v, subject="1girl, custom")
    assert res.final_positive.startswith("1girl, custom")
    assert "pixel art" in res.final_positive


def test_subject_mode_with_negative_only_user() -> None:
    """user negative 만 있고 base_negative 가 빈 문자열일 때."""
    v = _variant_with_template(base_negative="")
    res = resolve_prompt(v, subject="1girl, hair", negative_prompt="bad anatomy")
    assert res.final_negative == "bad anatomy"


def test_resolution_to_dict_shape() -> None:
    """PromptResolution.to_dict — 응답 직렬화 shape (spec §B.4)."""
    res = resolve_prompt(_variant_with_template(), subject="1girl, hair")
    d = res.to_dict()
    assert set(d.keys()) == {"mode", "user_slot", "user_input", "final_positive", "final_negative"}
    assert d["mode"] == "subject"
    assert d["user_input"] == "1girl, hair"


def test_auto_detects_legacy_when_signature_in_prompt() -> None:
    """base_positive 의 시그니처 토큰이 prompt 에 이미 있으면 legacy 로 자동 분기."""
    v = _variant_with_template(
        base_positive="pixel art, retro game art, sprite sheet, 1x3 layout",
    )
    # base_positive 시그니처 4 토큰 모두 prompt 에 있음 → legacy 자동 감지
    res = resolve_prompt(
        v,
        prompt="pixel art, retro game art, sprite sheet, 1x3 layout, 1girl, additional",
        prompt_mode="auto",
    )
    assert res.mode == "legacy"


def test_auto_subject_when_short_prompt() -> None:
    """짧은 prompt — auto 면 subject 모드로 (legacy 휴리스틱 다 false)."""
    res = resolve_prompt(
        _variant_with_template(),
        prompt="1girl, blue dress",
        prompt_mode="auto",
    )
    assert res.mode == "subject"
    assert "pixel art" in res.final_positive


def test_legacy_mode_user_negative_only() -> None:
    """legacy 모드 — user negative 만 통과 (base_negative 미적용)."""
    res = resolve_prompt(
        _variant_with_template(),
        prompt="custom prompt",
        negative_prompt="bad anatomy",
        prompt_mode="legacy",
    )
    assert res.final_negative == "bad anatomy"
    assert "blurry" not in res.final_negative   # base_negative 미적용


def test_subject_optional_user_slot_required_false() -> None:
    """user_slot.required=False — 빈 subject 통과."""
    v = _variant_with_template(
        user_slot=UserSlotSpec(label="subject", required=False, max_chars=400),
    )
    res = resolve_prompt(v, subject="", prompt_mode="subject")
    assert res.mode == "subject"
    assert res.user_input == ""
