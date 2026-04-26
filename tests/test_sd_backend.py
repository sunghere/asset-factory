"""sd_backend 단위 테스트.

A1111Backend / ComfyUIBackend / BackendRegistry 의 핵심 분기:
- A1111: SDClient.txt2img 결과를 GenerationOutcome 으로 래핑
- ComfyUI: registry 변형 → patch → submit_and_wait → 결과 매핑
- primary 보장 (모든 outputs.primary=False 일 때 [0] 강제 승격)
- 정렬 (primary 가 outputs[0])
- needs_conversion / unknown variant 시 SDError

외부 네트워크/디스크 의존 없음 — 모든 것이 in-memory 픽스처.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from generator import GenerationResult, SDClient, SDError
from generator_comfyui import ComfyUIClient, ComfyUIOutputImage, ComfyUIResult
from sd_backend import (
    A1111Backend,
    BackendRegistry,
    ComfyUIBackend,
    GeneratedOutput,
    GenerationOutcome,
)
from workflow_registry import WorkflowRegistry


# ----------------------------------------------------------------------------
# 픽스처 헬퍼
# ----------------------------------------------------------------------------


def _build_registry(tmp_path: Path) -> WorkflowRegistry:
    """V38 처럼 SaveImage 3개 (Stage1/Pixelized/PixelAlpha) 변형을 가진 레지스트리."""
    wf_payload = {
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "ORIGINAL"},
              "_meta": {"title": "Positive Prompt"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "OG_NEG"},
              "_meta": {"title": "Negative Prompt"}},
        "11": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 30, "cfg": 6.5,
                                                     "sampler_name": "dpmpp_2m", "scheduler": "karras"},
               "_meta": {"title": "KSampler #1"}},
        "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "stage1"},
               "_meta": {"title": "Save Stage1"}},
        "16": {"class_type": "SaveImage", "inputs": {"filename_prefix": "pixelized"},
               "_meta": {"title": "Save Pixelized"}},
        "18": {"class_type": "SaveImage", "inputs": {"filename_prefix": "pixel_alpha"},
               "_meta": {"title": "Save PixelAlpha"}},
    }
    (tmp_path / "sprite").mkdir(parents=True)
    (tmp_path / "sprite" / "v.json").write_text(json.dumps(wf_payload), encoding="utf-8")
    (tmp_path / "registry.yml").write_text(
        """
version: 1
categories:
  sprite:
    variants:
      v:
        file: sprite/v.json
        primary: true
        outputs:
          - { node_title: "Save Stage1",     label: stage1 }
          - { node_title: "Save Pixelized",  label: pixelized }
          - { node_title: "Save PixelAlpha", label: pixel_alpha, primary: true }
        defaults: {}
  pixel_bg:
    variants:
      sdxl:
        ui_file: pixel_bg/c1.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
        encoding="utf-8",
    )
    return WorkflowRegistry(root=tmp_path)


def _comfy_result(prompt_id: str, mapping: list[tuple[str, str, bytes]]) -> ComfyUIResult:
    """``mapping`` 은 ``[(node_id, filename, image_bytes), ...]``."""
    outputs = [
        ComfyUIOutputImage(
            node_id=nid, filename=fn, subfolder="", type="output", image_bytes=b
        )
        for nid, fn, b in mapping
    ]
    return ComfyUIResult(prompt_id=prompt_id, outputs=outputs)


# ----------------------------------------------------------------------------
# A1111Backend
# ----------------------------------------------------------------------------


def test_a1111_backend_wraps_txt2img(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")
    captured: dict[str, Any] = {}

    async def fake_txt2img(**kwargs: Any) -> GenerationResult:
        captured.update(kwargs)
        return GenerationResult(
            image_bytes=b"PNG", seed=42, model="dummy.safetensors",
            prompt=kwargs["prompt"], width=kwargs["width"], height=kwargs["height"],
        )

    async def fake_health() -> dict[str, Any]:
        return {"ok": True, "model_count": 1, "models": ["dummy"]}

    monkeypatch.setattr(client, "txt2img", fake_txt2img)
    monkeypatch.setattr(client, "health_check", fake_health)
    backend = A1111Backend(client)

    task = {
        "prompt": "p", "negative_prompt": "n",
        "model_name": "dummy.safetensors", "width": 512, "height": 512,
        "steps": 25, "cfg": 6.0, "sampler": "DPM++ 2M", "seed": 42,
    }
    outcome = asyncio.run(backend.generate(task))

    assert outcome.backend == "a1111"
    assert len(outcome.outputs) == 1
    assert outcome.outputs[0].is_primary is True
    assert outcome.outputs[0].image_bytes == b"PNG"
    assert outcome.outputs[0].label == "image"
    assert outcome.seed == 42
    assert outcome.model == "dummy.safetensors"
    assert outcome.width == 512
    assert outcome.height == 512
    assert captured["prompt"] == "p"
    assert captured["seed"] == 42


def test_a1111_backend_health_includes_backend_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")

    async def fake_health() -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(client, "health_check", fake_health)
    info = asyncio.run(A1111Backend(client).health_check())
    assert info == {"ok": True, "backend": "a1111"}


# ----------------------------------------------------------------------------
# ComfyUIBackend — 정상 흐름
# ----------------------------------------------------------------------------


def test_comfyui_backend_generates_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _build_registry(tmp_path)
    client = ComfyUIClient("comfy.local")
    captured: dict[str, Any] = {}

    async def fake_submit_and_wait(api_json: dict, timeout: float | None = None) -> ComfyUIResult:
        captured["api_json"] = api_json
        return _comfy_result(
            "p1",
            [
                ("13", "stage1.png", b"S1"),
                ("16", "pixelized.png", b"PX"),
                ("18", "pixel_alpha.png", b"PA"),
            ],
        )

    monkeypatch.setattr(client, "submit_and_wait", fake_submit_and_wait)
    backend = ComfyUIBackend(client, reg)

    task = {
        "prompt": "warrior",
        "seed": 7,
        "workflow_category": "sprite",
        "workflow_variant": "v",
    }
    outcome = asyncio.run(backend.generate(task))

    assert outcome.backend == "comfyui"
    assert outcome.model == "sprite/v"
    assert outcome.seed == 7
    # 출력 3개, primary 가 [0]
    assert len(outcome.outputs) == 3
    assert outcome.outputs[0].label == "pixel_alpha"
    assert outcome.outputs[0].is_primary is True
    # 나머지는 variant.outputs 선언 순서: stage1, pixelized
    assert [o.label for o in outcome.outputs[1:]] == ["stage1", "pixelized"]
    # raw 에 prompt_id / patch_report 들어있음
    assert outcome.raw["prompt_id"] == "p1"
    assert "patch_applied" in outcome.raw

    # submit_and_wait 에 들어간 api_json 의 Positive Prompt 가 패치됐는지
    assert captured["api_json"]["5"]["inputs"]["text"] == "warrior"
    assert captured["api_json"]["11"]["inputs"]["seed"] == 7


def test_comfyui_backend_health_includes_registry_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _build_registry(tmp_path)
    client = ComfyUIClient("comfy.local")

    async def fake_health() -> dict[str, Any]:
        return {"ok": True, "comfyui_version": "x.y.z", "device_count": 1, "device_names": ["cuda"]}

    monkeypatch.setattr(client, "health_check", fake_health)
    info = asyncio.run(ComfyUIBackend(client, reg).health_check())
    assert info["backend"] == "comfyui"
    assert info["workflows_available"] == 1   # sprite/v
    assert info["workflows_needing_conversion"] == 1  # pixel_bg/sdxl


# ----------------------------------------------------------------------------
# ComfyUIBackend — 에러 경로
# ----------------------------------------------------------------------------


def test_comfyui_backend_missing_workflow_keys(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    backend = ComfyUIBackend(ComfyUIClient("comfy.local"), reg)
    with pytest.raises(SDError, match="workflow_category/workflow_variant 누락"):
        asyncio.run(backend.generate({"prompt": "x"}))


def test_comfyui_backend_unknown_variant(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    backend = ComfyUIBackend(ComfyUIClient("comfy.local"), reg)
    with pytest.raises(SDError, match="unknown variant"):
        asyncio.run(
            backend.generate({
                "prompt": "x",
                "workflow_category": "sprite",
                "workflow_variant": "no_such",
            })
        )


def test_comfyui_backend_needs_conversion_blocked(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    backend = ComfyUIBackend(ComfyUIClient("comfy.local"), reg)
    with pytest.raises(SDError, match="호출 불가"):
        asyncio.run(
            backend.generate({
                "prompt": "x",
                "workflow_category": "pixel_bg",
                "workflow_variant": "sdxl",
            })
        )


def test_comfyui_backend_no_outputs_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComfyUI 가 SaveImage 결과를 줬는데 registry 가 인지 못 하는 노드면 빈 outputs."""
    reg = _build_registry(tmp_path)
    client = ComfyUIClient("comfy.local")

    async def fake_submit_and_wait(api_json: dict, timeout: float | None = None) -> ComfyUIResult:
        # node_id 99 는 variant 가 모르는 노드
        return _comfy_result("p2", [("99", "weird.png", b"X")])

    monkeypatch.setattr(client, "submit_and_wait", fake_submit_and_wait)
    backend = ComfyUIBackend(client, reg)
    with pytest.raises(SDError, match="매칭되는 SaveImage 결과 없음"):
        asyncio.run(backend.generate({
            "prompt": "x",
            "workflow_category": "sprite",
            "workflow_variant": "v",
        }))


def test_comfyui_backend_invalid_workflow_params_json(tmp_path: Path) -> None:
    reg = _build_registry(tmp_path)
    backend = ComfyUIBackend(ComfyUIClient("comfy.local"), reg)
    with pytest.raises(SDError, match="workflow_params_json 파싱 실패"):
        asyncio.run(backend.generate({
            "prompt": "x",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "workflow_params_json": "{broken",
        }))


# ----------------------------------------------------------------------------
# ComfyUIBackend — primary 보장 / 정렬
# ----------------------------------------------------------------------------


def test_comfyui_backend_promotes_first_when_no_primary_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """variant.outputs 에 primary 마커가 없을 때 첫 번째 매칭 결과를 primary 로."""
    (tmp_path / "sprite").mkdir(parents=True)
    (tmp_path / "sprite" / "v.json").write_text(
        json.dumps({
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": ""},
                  "_meta": {"title": "Positive Prompt"}},
            "11": {"class_type": "KSampler",
                    "inputs": {"seed": 0, "steps": 20, "cfg": 7.0,
                               "sampler_name": "dpmpp_2m", "scheduler": "karras"},
                    "_meta": {"title": "KSampler #1"}},
            "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "a"},
                   "_meta": {"title": "Save A"}},
            "14": {"class_type": "SaveImage", "inputs": {"filename_prefix": "b"},
                   "_meta": {"title": "Save B"}},
        }),
        encoding="utf-8",
    )
    (tmp_path / "registry.yml").write_text(
        """
version: 1
categories:
  cat:
    variants:
      v:
        file: sprite/v.json
        outputs:
          - { node_title: "Save A", label: a }
          - { node_title: "Save B", label: b }
        defaults: {}
""",
        encoding="utf-8",
    )
    reg = WorkflowRegistry(root=tmp_path)
    client = ComfyUIClient("comfy.local")

    async def fake_submit_and_wait(api_json: dict, timeout: float | None = None) -> ComfyUIResult:
        return _comfy_result("p", [("14", "b.png", b"B"), ("13", "a.png", b"A")])

    monkeypatch.setattr(client, "submit_and_wait", fake_submit_and_wait)
    outcome = asyncio.run(ComfyUIBackend(client, reg).generate({
        "prompt": "x",
        "workflow_category": "cat",
        "workflow_variant": "v",
    }))
    # variant.outputs 선언 순서 (a, b) — primary 마커 없으니 첫 번째(a) 강제 승격
    assert outcome.outputs[0].label == "a"
    assert outcome.outputs[0].is_primary is True
    assert outcome.outputs[1].label == "b"


# ----------------------------------------------------------------------------
# BackendRegistry
# ----------------------------------------------------------------------------


def test_backend_registry_dispatch_known_names() -> None:
    a = A1111Backend(SDClient("sd.local"))
    br = BackendRegistry({"a1111": a})
    assert br.get("a1111") is a
    assert br.names == ["a1111"]


def test_backend_registry_none_falls_back_to_a1111() -> None:
    """기존 행 (backend NULL) 의 호환을 위해 None → a1111."""
    a = A1111Backend(SDClient("sd.local"))
    br = BackendRegistry({"a1111": a})
    assert br.get(None) is a
    assert br.get("") is a
    assert br.get("  ") is a


def test_backend_registry_unknown_raises() -> None:
    a = A1111Backend(SDClient("sd.local"))
    br = BackendRegistry({"a1111": a})
    with pytest.raises(SDError, match="unknown backend"):
        br.get("rocket")


# ----------------------------------------------------------------------------
# GeneratedOutput / GenerationOutcome 헬퍼
# ----------------------------------------------------------------------------


def test_generation_outcome_primary_property() -> None:
    o1 = GeneratedOutput(label="a", image_bytes=b"a", is_primary=True)
    o2 = GeneratedOutput(label="b", image_bytes=b"b", is_primary=False)
    outcome = GenerationOutcome(
        outputs=[o1, o2], seed=1, model="x", backend="a1111", width=64, height=64,
    )
    assert outcome.primary is o1
    assert outcome.extras == [o2]
