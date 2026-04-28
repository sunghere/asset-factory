"""Unit tests for lib.comfyui_catalog — pure transformation functions.

PLAN: docs/PLAN_comfyui_catalog.md §3.1.2, §4 Task 2.

본 모듈은 순수 함수 (서버 / aiohttp 무관) 라서 fixture 기반 단위 테스트만으로
- ComfyUI `/object_info` 응답 파싱
- WorkflowRegistry 와 cross-reference (used_by_workflows)
- family 추론 (sdxl / pony / sd1.5 / unknown) 등을 검증한다.

Endpoint 통합 테스트는 ``tests/test_workflow_endpoints.py`` 의
``test_comfyui_catalog_*`` 케이스 참고.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# 본 import 는 RED phase 에서 의도적으로 fail 한다 — Task 2 GREEN 에서 모듈 추가.
from lib import comfyui_catalog  # noqa: E402


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "comfyui_object_info.json"


@pytest.fixture
def object_info() -> dict:
    """ComfyUI /object_info 골든 응답 (5 노드, 6.5KB)."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ── 단위 1 ─ 노드별 파일 목록 추출 ───────────────────────────────────────


def test_extracts_checkpoints_loras_vaes_controlnets_upscalers(object_info: dict) -> None:
    """fixture 의 5개 카테고리 모두 추출. 빈 카테고리는 빈 리스트."""
    catalog = comfyui_catalog.build_catalog(object_info, used_by_index={})

    # PLAN §3.1.2 의 schema — 5개 카테고리 모두 keys 로 존재
    assert set(catalog.keys()) >= {
        "checkpoints", "loras", "vaes", "controlnets", "upscalers"
    }

    # fixture 기반 기대 — sample 데이터에서 실측한 모델 수
    ckpt_names = [c["name"] for c in catalog["checkpoints"]]
    assert "AnythingXL_xl.safetensors" in ckpt_names
    assert "animagineXL40Opt_clear.safetensors" in ckpt_names
    assert len(ckpt_names) >= 5

    lora_names = [l["name"] for l in catalog["loras"]]
    # fixture 의 LoraLoader 노드에 어떤 lora 가 들어있는지는 fixture 본문에 의존
    assert isinstance(lora_names, list)

    vae_names = [v["name"] for v in catalog["vaes"]]
    assert "xlVAEC_g102.safetensors" in vae_names

    cn_names = [c["name"] for c in catalog["controlnets"]]
    assert any("openpose" in n.lower() for n in cn_names)


def test_missing_node_type_yields_empty_list() -> None:
    """fixture 에 LoraLoader 가 없어도 loras=[] 로 정상 동작 (KeyError 안 남)."""
    sparse = {"VAELoader": {"input": {"required": {"vae_name": [["only.safetensors"]]}}}}
    catalog = comfyui_catalog.build_catalog(sparse, used_by_index={})
    assert catalog["loras"] == []
    assert catalog["checkpoints"] == []
    assert catalog["vaes"] == [{"name": "only.safetensors", "used_by_workflows": []}]


# ── 단위 2 ─ family 추론 ────────────────────────────────────────────────


def test_family_inference_marks_sdxl_pony_sd15(object_info: dict) -> None:
    """checkpoint name pattern 으로 family 추론.

    - "*XL*" / "*xl*" → sdxl
    - "*pony*" → pony
    - 그 외 → unknown (sd1.5 도 unknown — 명시 패턴 없으면 안전하게 unknown)
    """
    catalog = comfyui_catalog.build_catalog(object_info, used_by_index={})

    by_name = {c["name"]: c for c in catalog["checkpoints"]}

    # XL → sdxl
    assert by_name["AnythingXL_xl.safetensors"]["family"] == "sdxl"
    assert by_name["animagineXL40Opt_clear.safetensors"]["family"] == "sdxl"

    # pony 패턴 (fixture 에 prefectPonyXL_v6.safetensors 있음)
    if "prefectPonyXL_v6.safetensors" in by_name:
        assert by_name["prefectPonyXL_v6.safetensors"]["family"] == "pony"

    # meinamix 같은 sd1.5 → unknown
    if "meinamix_v12Final.safetensors" in by_name:
        assert by_name["meinamix_v12Final.safetensors"]["family"] == "unknown"


# ── 단위 3 ─ used_by_workflows cross-reference ───────────────────────────


def test_used_by_workflows_cross_reference(object_info: dict) -> None:
    """variant api.json 을 정적 분석한 used_by_index 가 catalog 에 반영된다."""
    used_by_index = {
        "checkpoints": {
            "AnythingXL_xl.safetensors": ["sprite:pixel_alpha", "sprite:hires"],
        },
        "loras": {
            "pixel-art-xl-v1.1.safetensors": ["sprite:pixel_alpha"],
        },
        "vaes": {},
        "controlnets": {},
        "upscalers": {},
    }
    catalog = comfyui_catalog.build_catalog(object_info, used_by_index=used_by_index)

    by_name = {c["name"]: c for c in catalog["checkpoints"]}
    assert sorted(by_name["AnythingXL_xl.safetensors"]["used_by_workflows"]) == [
        "sprite:hires",
        "sprite:pixel_alpha",
    ]

    # cross-ref 없는 모델은 빈 배열
    assert by_name["animagineXL40Opt_clear.safetensors"]["used_by_workflows"] == []


# ── 단위 4 ─ index_workflows: WorkflowRegistry → used_by_index ──────────


def test_index_workflows_extracts_uses_from_registry() -> None:
    """실제 WorkflowRegistry 기반으로 used_by_index 빌드.

    각 available variant 의 api.json 을 훑어서 CheckpointLoaderSimple /
    LoraLoader / VAELoader / ControlNetLoader / UpscaleModelLoader 에서
    파일명 추출 → {category: {filename: [variant_id, ...]}} 인덱스.
    """
    from workflow_registry import get_default_registry

    registry = get_default_registry()
    index = comfyui_catalog.index_workflows(registry)

    # spec: 5개 카테고리 모두 keys 로 존재 (값은 비어있을 수 있음)
    assert set(index.keys()) == {
        "checkpoints", "loras", "vaes", "controlnets", "upscalers"
    }

    # 실 데이터에서 AnythingXL_xl.safetensors 가 어떤 variant 에서 쓰임 (sprite/* )
    ckpt_index = index["checkpoints"]
    assert "AnythingXL_xl.safetensors" in ckpt_index, (
        f"AnythingXL_xl 이 어떤 variant 에도 cross-ref 되지 않음: keys={sorted(ckpt_index.keys())[:5]}"
    )
    used_by = ckpt_index["AnythingXL_xl.safetensors"]
    # variant_id 형식: "{category}:{name}"
    assert all(":" in v for v in used_by), used_by
    # 실 registry 에서 AnythingXL 을 쓰는 variant 가 1개 이상 존재 (현재는
    # illustration:anything_* 계열). 어떤 카테고리든 무방.
    assert len(used_by) >= 1, used_by


# ── 단위 5 ─ build_full_payload: catalog + workflows 합본 ───────────────


def test_build_full_payload_includes_workflows_section(object_info: dict) -> None:
    """`/api/comfyui/catalog` 의 최종 응답 (PLAN §3.1.2 schema).

    - fetched_at, stale, checkpoints, loras, vaes, controlnets, upscalers, workflows
    - workflows[*] = {id, category, label, variants(int), uses_models, uses_loras}
    """
    from workflow_registry import get_default_registry

    registry = get_default_registry()
    payload = comfyui_catalog.build_full_payload(
        object_info=object_info,
        registry=registry,
        fetched_at="2026-04-29T01:30:00Z",
        stale=False,
    )

    assert payload["fetched_at"] == "2026-04-29T01:30:00Z"
    assert payload["stale"] is False
    assert isinstance(payload["checkpoints"], list)
    assert isinstance(payload["workflows"], list)
    assert len(payload["workflows"]) >= 1

    wf = payload["workflows"][0]
    # PLAN §3.1.2 의 workflow row schema
    assert set(wf.keys()) >= {
        "id", "category", "label", "variants", "uses_models", "uses_loras"
    }
    assert ":" in wf["id"], wf["id"]
    assert isinstance(wf["uses_models"], list)
    assert isinstance(wf["uses_loras"], list)
