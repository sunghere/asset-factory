"""ComfyUI catalog — `/object_info` 응답 + WorkflowRegistry cross-reference.

PLAN: docs/PLAN_comfyui_catalog.md §3.1.2, §4 Task 2.

순수 변환 로직 (서버/aiohttp 무관). endpoint 통합은 ``server.py`` 의
``/api/comfyui/catalog`` 핸들러가 담당.

핵심 함수:
  - ``build_catalog(object_info, used_by_index)``
        /object_info 응답 → {checkpoints, loras, vaes, controlnets, upscalers}.
  - ``index_workflows(registry)``
        WorkflowRegistry 의 모든 available variant 의 api.json 을 정적 분석해서
        used_by_index 를 빌드.
  - ``build_full_payload(object_info, registry, fetched_at, stale)``
        endpoint 가 그대로 반환할 최종 dict (workflows 섹션까지 합본).
"""
from __future__ import annotations

from typing import Any

# 노드 type → (catalog_key, input field name) 매핑.
# /object_info 응답의 `<NodeType>.input.required.<field>` 가 list 면 첫 원소가
# 사용 가능한 값들의 list.
CATALOG_SOURCES: list[tuple[str, str, str]] = [
    ("checkpoints", "CheckpointLoaderSimple", "ckpt_name"),
    ("loras", "LoraLoader", "lora_name"),
    ("vaes", "VAELoader", "vae_name"),
    ("controlnets", "ControlNetLoader", "control_net_name"),
    ("upscalers", "UpscaleModelLoader", "model_name"),
]

CATEGORY_KEYS = tuple(c[0] for c in CATALOG_SOURCES)


# ── family 추론 ────────────────────────────────────────────────────────────
# checkpoint name 기반 — 간단 패턴 매칭. 명시 패턴이 없으면 unknown 으로 안전 처리.

def _infer_family(name: str) -> str:
    """모델 파일명에서 family 추론.

    - "*pony*" (대소문자 무시) → pony
    - "XL" / "xl" 토큰 포함 → sdxl
    - 그 외 → unknown
    """
    lower = name.lower()
    if "pony" in lower:
        return "pony"
    # XL 토큰: 단어 경계 또는 점 / 언더바 / 하이픈 직전
    if "xl" in lower:
        return "sdxl"
    return "unknown"


# ── /object_info 파싱 ──────────────────────────────────────────────────────


def _extract_node_files(object_info: dict[str, Any], node_type: str, field: str) -> list[str]:
    """``object_info[node_type].input.required[field]`` 의 옵션 list 추출.

    ComfyUI 응답 구조:
        {
          "<NodeType>": {
            "input": {
              "required": {
                "<field>": [["a.safetensors", "b.safetensors"], {...}]
              }
            }
          }
        }
    """
    node = object_info.get(node_type)
    if not isinstance(node, dict):
        return []
    required = (node.get("input") or {}).get("required") or {}
    spec = required.get(field)
    if not isinstance(spec, list) or not spec:
        return []
    options = spec[0]
    if not isinstance(options, list):
        return []
    return [str(opt) for opt in options if isinstance(opt, (str, int, float))]


def build_catalog(
    object_info: dict[str, Any],
    *,
    used_by_index: dict[str, dict[str, list[str]]],
) -> dict[str, list[dict[str, Any]]]:
    """``/object_info`` 응답을 카탈로그 dict 로 변환.

    Args:
        object_info: ComfyUI ``/object_info`` 응답 (또는 fixture).
        used_by_index: ``index_workflows`` 결과. 카테고리별 ``{filename: [variant_id, ...]}``.

    Returns:
        ``{checkpoints: [...], loras: [...], vaes: [...], controlnets: [...], upscalers: [...]}``
        각 row 는 ``{"name": str, "used_by_workflows": [str, ...]}`` 이며,
        ``checkpoints`` row 에는 ``family`` 필드가 추가된다.
    """
    catalog: dict[str, list[dict[str, Any]]] = {key: [] for key in CATEGORY_KEYS}

    for catalog_key, node_type, field in CATALOG_SOURCES:
        files = _extract_node_files(object_info, node_type, field)
        cat_used_by = used_by_index.get(catalog_key, {})
        for fname in files:
            row: dict[str, Any] = {
                "name": fname,
                "used_by_workflows": sorted(cat_used_by.get(fname, [])),
            }
            if catalog_key == "checkpoints":
                row["family"] = _infer_family(fname)
            catalog[catalog_key].append(row)

    return catalog


# ── WorkflowRegistry → used_by_index 정적 분석 ─────────────────────────────


# variant api.json 의 class_type → catalog 카테고리 + input field 매핑.
_NODE_TO_CATEGORY: dict[str, tuple[str, str]] = {
    "CheckpointLoaderSimple": ("checkpoints", "ckpt_name"),
    "LoraLoader": ("loras", "lora_name"),
    "VAELoader": ("vaes", "vae_name"),
    "ControlNetLoader": ("controlnets", "control_net_name"),
    "UpscaleModelLoader": ("upscalers", "model_name"),
}


def index_workflows(registry: Any) -> dict[str, dict[str, list[str]]]:
    """WorkflowRegistry → 카탈로그 cross-ref 인덱스.

    각 available variant 의 api.json 을 훑어서 어떤 모델/lora 가 어떤 variant
    에서 쓰이는지 정적으로 추출. variant_id 형식: ``"{category}:{name}"``.

    동적 patch (런타임에 ckpt_name 을 결정하는 경우) 는 추적 불가 — 결과에서 누락.
    PLAN §8 위험 항목.
    """
    index: dict[str, dict[str, list[str]]] = {key: {} for key in CATEGORY_KEYS}

    for variant in registry.available_variants():
        variant_id = f"{variant.category}:{variant.name}"
        try:
            workflow = variant.load_api_json()
        except Exception:  # noqa: BLE001 — variant 하나의 로드 실패가 catalog 전체를 망치지 않도록
            continue
        if not isinstance(workflow, dict):
            continue

        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type")
            mapping = _NODE_TO_CATEGORY.get(ct) if isinstance(ct, str) else None
            if mapping is None:
                continue
            cat_key, field = mapping
            inputs = node.get("inputs") or {}
            value = inputs.get(field)
            # 보통 string. ComfyUI graph 에서 다른 노드 출력에 연결되면 [src_id, port]
            # 형식의 list 인데, 그건 정적 매칭 불가 — 무시.
            if not isinstance(value, str) or not value:
                continue
            bucket = index[cat_key].setdefault(value, [])
            if variant_id not in bucket:
                bucket.append(variant_id)

    return index


# ── 최종 페이로드 (endpoint 가 반환할 dict) ────────────────────────────────


def build_full_payload(
    *,
    object_info: dict[str, Any],
    registry: Any,
    fetched_at: str,
    stale: bool,
) -> dict[str, Any]:
    """`/api/comfyui/catalog` 응답 schema (PLAN §3.1.2).

    Returns:
        ``{fetched_at, stale, checkpoints, loras, vaes, controlnets, upscalers, workflows}``
    """
    used_by_index = index_workflows(registry)
    catalog = build_catalog(object_info, used_by_index=used_by_index)

    # workflow row: variant 단위가 아니라 **카테고리 (예: sprite:character_stack)** 단위.
    # PLAN §3.1.2 의 예시는 "sprite:character-stack" 같은 슬러그. 우리 registry 는
    # category/variant 트리지만, UI 친화적으로 variant 1건 = workflow 1행으로 매핑.
    workflows: list[dict[str, Any]] = []
    for variant in registry.available_variants():
        variant_id = f"{variant.category}:{variant.name}"
        uses_models: list[str] = []
        uses_loras: list[str] = []
        try:
            workflow = variant.load_api_json()
        except Exception:  # noqa: BLE001
            workflow = None
        if isinstance(workflow, dict):
            for node in workflow.values():
                if not isinstance(node, dict):
                    continue
                ct = node.get("class_type")
                inputs = node.get("inputs") or {}
                if ct == "CheckpointLoaderSimple":
                    v = inputs.get("ckpt_name")
                    if isinstance(v, str) and v and v not in uses_models:
                        uses_models.append(v)
                elif ct == "LoraLoader":
                    v = inputs.get("lora_name")
                    if isinstance(v, str) and v and v not in uses_loras:
                        uses_loras.append(v)

        label = variant.description or variant.name
        workflows.append({
            "id": variant_id,
            "category": variant.category,
            "label": label,
            "variants": 1,
            "uses_models": uses_models,
            "uses_loras": uses_loras,
        })

    payload: dict[str, Any] = {
        "fetched_at": fetched_at,
        "stale": stale,
        **catalog,
        "workflows": workflows,
    }
    return payload
