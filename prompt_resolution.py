"""§1.B subject-injection 모드의 prompt 합성 (`docs/NEXT.md §1.B`).

사용자가 `subject` (캐릭터 묘사만) 만 보내면 변형 yaml 의 `prompt_template.
base_positive` + `injection_rule` 로 final prompt 를 합성한다. legacy 모드 (기존
prompt 통째 입력) 는 그대로 통과 — `WorkflowGenerateRequest.prompt` 가 그대로
ComfyUI 로 전달.

설계 (spec §B.2/B.5):
- 모드 선택: subject 명시 / prompt_mode='legacy' / 변형에 prompt_template 없음 /
  자동 감지 휴리스틱 (`_looks_like_legacy_prompt`) — 우선순위 순.
- 합성: `injection_rule.format(base_positive=..., subject=...)` + 옵션
  style_extra 뒤붙임. negative 는 base_negative 에 user negative 추가만 (override 불가).
- 검증: required / min_chars / max_chars 위반 → `SubjectValidationError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from workflow_registry import PromptTemplateSpec, VariantSpec


PromptMode = Literal["legacy", "subject"]


class PromptResolutionError(Exception):
    """prompt 합성 단계의 사용자-측 입력 오류 (HTTP 400 매핑).

    code: spec §B.5 의 에러 코드 (subject_required / subject_length_invalid /
    prompt_template_missing_for_variant). 클라이언트가 이 코드로 분기 가능.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True, frozen=True)
class PromptResolution:
    """generate 응답·DB 저장의 prompt 합성 결과 (spec §B.4 Response).

    재현·디버깅용 — final_positive / final_negative 가 ComfyUI 로 실제 전송된
    값. legacy 모드면 mode='legacy', user_slot=None, final_positive=request.prompt,
    final_negative=request.negative_prompt.
    """

    mode: PromptMode
    user_slot: str | None       # subject 모드에서 user_slot.label, legacy 면 None
    user_input: str             # subject 또는 legacy prompt 원본
    final_positive: str
    final_negative: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "mode": self.mode,
            "user_slot": self.user_slot,
            "user_input": self.user_input,
            "final_positive": self.final_positive,
            "final_negative": self.final_negative,
        }


def _looks_like_legacy_prompt(
    prompt: str | None, template: PromptTemplateSpec
) -> bool:
    """자동 감지 — prompt 가 legacy 통째 입력 같은 형태인지 휴리스틱 판단 (spec §B.2).

    1. user_slot.max_chars 보다 길면 legacy.
    2. base_positive 의 시그니처 토큰 (예: "pixel art", "sprite sheet") 이 prompt
       안에 *이미 있음* → 사용자가 통째 작성한 것 → legacy.

    이 휴리스틱은 옵션이라 정확하지 않아도 OK — `prompt_resolution.mode`
    응답으로 사용자가 어느 모드로 분기됐는지 항상 알 수 있다.
    """
    if not prompt:
        return False
    user_slot = template.user_slot
    if user_slot is not None and user_slot.max_chars is not None:
        if len(prompt) > user_slot.max_chars:
            return True
    # base_positive 의 키워드 시그니처 (콤마 구분 첫 4 토큰) 가 prompt 에 다 있으면 legacy.
    sig_tokens = [
        t.strip()
        for t in template.base_positive.split(",")[:4]
        if t.strip() and len(t.strip()) > 3   # "1girl" 같은 짧은 토큰 false-positive 회피
    ]
    if sig_tokens and all(tok.lower() in prompt.lower() for tok in sig_tokens):
        return True
    return False


def _choose_mode(
    *,
    subject: str | None,
    prompt: str | None,
    prompt_mode: str,
    template: PromptTemplateSpec | None,
) -> PromptMode:
    """모드 선택 우선순위 (spec §B.2 Mode Selection):

    1. ``prompt_mode == 'subject'`` 또는 ``subject`` 명시 → subject (강제).
    2. ``prompt_mode == 'legacy'`` → legacy (강제).
    3. 변형에 ``prompt_template`` 없음 → legacy (자동 — 변형이 안 받음).
    4. 자동 감지 (``_looks_like_legacy_prompt``) → legacy.
    5. 기본 → subject.
    """
    if prompt_mode == "subject" or subject is not None:
        return "subject"
    if prompt_mode == "legacy":
        return "legacy"
    if template is None:
        return "legacy"
    if _looks_like_legacy_prompt(prompt, template):
        return "legacy"
    return "subject"


def _validate_user_slot(template: PromptTemplateSpec, user_input: str) -> None:
    """user_slot 의 required / min_chars / max_chars 검증 (spec §B.5).

    위반 시 ``PromptResolutionError`` (HTTP 400 매핑) raise.
    """
    user_slot = template.user_slot
    if user_slot is None:
        # template.user_slot 미지정 — 검증 없이 통과 (드물지만 yaml 작성자가 의도적으로 뺄 수 있음).
        return
    text = user_input.strip()
    if user_slot.required and not text:
        raise PromptResolutionError(
            "subject_required",
            f"variant 의 user_slot '{user_slot.label}' 은 required — 빈 입력 거부",
        )
    if user_slot.min_chars is not None and len(text) < user_slot.min_chars:
        raise PromptResolutionError(
            "subject_length_invalid",
            f"user_slot '{user_slot.label}' min_chars={user_slot.min_chars} 위반 "
            f"(입력 길이 {len(text)})",
        )
    if user_slot.max_chars is not None and len(text) > user_slot.max_chars:
        raise PromptResolutionError(
            "subject_length_invalid",
            f"user_slot '{user_slot.label}' max_chars={user_slot.max_chars} 위반 "
            f"(입력 길이 {len(text)})",
        )


def resolve_prompt(
    variant: VariantSpec,
    *,
    subject: str | None = None,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    prompt_mode: str = "auto",
    style_extra: str | None = None,
) -> PromptResolution:
    """prompt 합성 (spec §B.5).

    호출자 (`server.py` generate 핸들러) 가 `WorkflowGenerateRequest` 의 필드를
    풀어서 전달. 결과의 ``final_positive`` / ``final_negative`` 가 ComfyUI 로
    실제 디스패치되는 값.

    Raises:
        PromptResolutionError: subject 모드인데 prompt_template 없음 /
        required 위반 / 길이 위반.
    """
    template = variant.meta.prompt_template
    mode = _choose_mode(
        subject=subject,
        prompt=prompt,
        prompt_mode=prompt_mode,
        template=template,
    )

    if mode == "legacy":
        # legacy: prompt 통째 그대로. negative 도 그대로.
        return PromptResolution(
            mode="legacy",
            user_slot=None,
            user_input=prompt or "",
            final_positive=prompt or "",
            final_negative=negative_prompt or "",
        )

    # subject 모드 — template 필수.
    if template is None:
        raise PromptResolutionError(
            "prompt_template_missing_for_variant",
            f"variant {variant.category}/{variant.name} 은 prompt_template 미설정 — "
            f"subject 모드 사용 불가. prompt_mode='legacy' 또는 변형에 메타 추가 필요.",
        )

    user_input = (subject or prompt or "").strip()
    _validate_user_slot(template, user_input)

    base_positive = template.base_positive.strip()
    final_positive = template.injection_rule.format(
        base_positive=base_positive,
        subject=user_input,
    )
    if style_extra:
        final_positive = f"{final_positive}, {style_extra.strip()}"

    final_negative = template.base_negative.strip()
    if negative_prompt:
        user_neg = negative_prompt.strip()
        # base_negative 가 빈 문자열이면 user_neg 그대로 — 앞 ", " 회피.
        final_negative = (
            f"{final_negative}, {user_neg}" if final_negative else user_neg
        )

    return PromptResolution(
        mode="subject",
        user_slot=template.user_slot.label if template.user_slot else None,
        user_input=user_input,
        final_positive=final_positive,
        final_negative=final_negative,
    )
