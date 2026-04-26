"""ComfyUI 워크플로우 API JSON 동적 patcher.

설계 원칙 (HANDOFF §4):
1. **노드 ID 하드코딩 금지** — `class_type` + (선택) `_meta.title` 정규식으로 검색
2. **없는 노드는 조용히 스킵** — 변형마다 가진 노드가 다르므로 (V36 Pro 는
   ControlNet 없음 등) 매칭 실패가 곧 에러는 아님
3. **원본 보존** — 항상 deep copy 한 사본을 반환
4. **단일 패치 API** — `patch_workflow(...)` 한 함수로 모든 키 처리

지원 키:
    prompt, negative_prompt        → CLIPTextEncode (title 로 positive/negative 구분)
    seed                           → 모든 KSampler 의 seed 동기화 (변형 간 일관성)
    steps, cfg, sampler_name,
    scheduler                      → 모든 KSampler
    pose_image                     → LoadImage where title contains "Pose grid"
    controlnet_strength            → ControlNetApply
    lora_strengths {name: weight}  → LoraLoader (`lora_name` 으로 매칭)
    checkpoint                     → CheckpointLoaderSimple
    width, height                  → EmptyLatentImage

Roadmap:
- ControlNet 워크플로우 외 다른 LoadImage 가 등장하면 `title_match` 추가 필요
- 다중 ControlNet (예: pose + depth) 시 title 로 구분 추가 가능
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable

# ----------------------------------------------------------------------------
# 노드 검색 유틸
# ----------------------------------------------------------------------------

WorkflowJson = dict[str, dict[str, Any]]


def _node_title(node: dict[str, Any]) -> str:
    return str(node.get("_meta", {}).get("title", ""))


def find_nodes(
    wf: WorkflowJson,
    class_type: str,
    *,
    title_match: str | re.Pattern[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """`class_type` 일치 노드 (+ 선택 title 정규식 매칭) 를 (id, node) 리스트로 반환.

    여러 노드가 매칭되는 경우(예: KSampler 가 stage1/hires 둘) 모두 반환한다 —
    호출자가 일괄 패치하거나 첫 번째만 쓴다.
    """
    pattern: re.Pattern[str] | None = None
    if title_match is not None:
        pattern = (
            title_match
            if isinstance(title_match, re.Pattern)
            else re.compile(title_match, re.IGNORECASE)
        )

    out: list[tuple[str, dict[str, Any]]] = []
    for nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != class_type:
            continue
        if pattern is not None and not pattern.search(_node_title(node)):
            continue
        out.append((str(nid), node))
    return out


# ----------------------------------------------------------------------------
# 패치 규칙 — 어느 노드의 어느 입력 필드를 어떻게 채울지
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class _PatchRule:
    """단일 패치 키의 노드 검색 + 입력 필드 적용 규칙."""

    class_type: str
    title_match: str | None
    apply: Callable[[dict[str, Any], Any], None]  # (node["inputs"], value) -> None


def _set_input(field: str) -> Callable[[dict[str, Any], Any], None]:
    """단순 input 필드 1개에 값 대입."""
    def _apply(inputs: dict[str, Any], value: Any) -> None:
        inputs[field] = value
    return _apply


# 각 패치 키에 대한 단일 규칙. (lora_strengths 만 dict 라 별도 처리)
_RULES: dict[str, _PatchRule] = {
    "prompt": _PatchRule(
        class_type="CLIPTextEncode",
        title_match=r"^Positive Prompt$",
        apply=_set_input("text"),
    ),
    "negative_prompt": _PatchRule(
        class_type="CLIPTextEncode",
        title_match=r"^Negative Prompt$",
        apply=_set_input("text"),
    ),
    "seed": _PatchRule(
        class_type="KSampler",
        title_match=None,
        apply=_set_input("seed"),
    ),
    "steps": _PatchRule(
        class_type="KSampler",
        title_match=None,
        apply=_set_input("steps"),
    ),
    "cfg": _PatchRule(
        class_type="KSampler",
        title_match=None,
        apply=_set_input("cfg"),
    ),
    "sampler_name": _PatchRule(
        class_type="KSampler",
        title_match=None,
        apply=_set_input("sampler_name"),
    ),
    "scheduler": _PatchRule(
        class_type="KSampler",
        title_match=None,
        apply=_set_input("scheduler"),
    ),
    "pose_image": _PatchRule(
        class_type="LoadImage",
        title_match=r"Pose grid",  # PoseExtract 의 "Load source image" 는 매칭 안 됨
        apply=_set_input("image"),
    ),
    "controlnet_strength": _PatchRule(
        class_type="ControlNetApply",
        title_match=None,
        apply=_set_input("strength"),
    ),
    "checkpoint": _PatchRule(
        class_type="CheckpointLoaderSimple",
        title_match=None,
        apply=_set_input("ckpt_name"),
    ),
    "width": _PatchRule(
        class_type="EmptyLatentImage",
        title_match=None,
        apply=_set_input("width"),
    ),
    "height": _PatchRule(
        class_type="EmptyLatentImage",
        title_match=None,
        apply=_set_input("height"),
    ),
}


# ----------------------------------------------------------------------------
# 메인 API
# ----------------------------------------------------------------------------


@dataclass(slots=True)
class PatchReport:
    """패치 결과 진단 — 어떤 키가 어느 노드에 적용/스킵됐는지."""

    applied: dict[str, list[str]]  # {patch_key: [matched_node_id, ...]}
    skipped: list[str]             # 매칭 노드 0개라 스킵된 키들

    def __bool__(self) -> bool:
        return bool(self.applied)


def patch_workflow(
    api_json: WorkflowJson,
    *,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    sampler_name: str | None = None,
    scheduler: str | None = None,
    pose_image: str | None = None,
    controlnet_strength: float | None = None,
    lora_strengths: dict[str, float] | None = None,
    checkpoint: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> tuple[WorkflowJson, PatchReport]:
    """워크플로우 API JSON 의 패치된 사본을 반환한다.

    None 인 인자는 무시. 매칭되는 노드가 없는 키는 `report.skipped` 에 기록만
    하고 진행 (변형마다 노드 구성이 다르므로 정상 동작).
    """
    wf = copy.deepcopy(api_json)
    applied: dict[str, list[str]] = {}
    skipped: list[str] = []

    kv_args: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "steps": steps,
        "cfg": cfg,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "pose_image": pose_image,
        "controlnet_strength": controlnet_strength,
        "checkpoint": checkpoint,
        "width": width,
        "height": height,
    }

    for key, value in kv_args.items():
        if value is None:
            continue
        rule = _RULES[key]
        matches = find_nodes(wf, rule.class_type, title_match=rule.title_match)
        if not matches:
            skipped.append(key)
            continue
        applied[key] = []
        for nid, node in matches:
            inputs = node.setdefault("inputs", {})
            rule.apply(inputs, value)
            applied[key].append(nid)

    if lora_strengths:
        applied_loras = _apply_lora_strengths(wf, lora_strengths)
        if applied_loras:
            applied["lora_strengths"] = applied_loras
        if not applied_loras and lora_strengths:
            skipped.append("lora_strengths")

    return wf, PatchReport(applied=applied, skipped=skipped)


def _apply_lora_strengths(
    wf: WorkflowJson,
    lora_strengths: dict[str, float],
) -> list[str]:
    """LoraLoader 노드 의 `lora_name` 으로 매칭 후 strength 양쪽(model/clip) 동시 변경.

    `lora_strengths` 키는 `.safetensors` 확장자 유무 모두 허용 — 정규화해서 비교.
    """
    norm = {_normalize_lora_key(k): float(v) for k, v in lora_strengths.items()}
    applied: list[str] = []

    for nid, node in find_nodes(wf, "LoraLoader"):
        lora_name = node.get("inputs", {}).get("lora_name")
        if not lora_name:
            continue
        key = _normalize_lora_key(str(lora_name))
        if key not in norm:
            continue
        weight = norm[key]
        node["inputs"]["strength_model"] = weight
        node["inputs"]["strength_clip"] = weight
        applied.append(nid)

    return applied


def _normalize_lora_key(value: str) -> str:
    name = value.strip().lower()
    for ext in (".safetensors", ".ckpt", ".pt", ".bin"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name
