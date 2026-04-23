"""generator.py 단위 테스트."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import TracebackType
from typing import Any

import aiohttp
import pytest

from generator import GenerationResult, SDClient, SDError, save_candidate_slot_image, save_generated_image


def test_health_check_and_list_models_filter_non_dict_items(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")

    async def fake_request(_method: str, _endpoint: str, json_payload: dict[str, Any] | None = None) -> Any:
        assert json_payload is None
        return [{"model_name": "model-a"}, {"title": "title-only"}, "bad", {"model_name": "model-b"}]

    monkeypatch.setattr(client, "_request_json", fake_request)

    health = asyncio.run(client.health_check())
    models = asyncio.run(client.list_models())

    assert health == {"ok": True, "model_count": 3, "models": ["model-a", "model-b"]}
    assert models == [{"model_name": "model-a"}, {"title": "title-only"}, {"model_name": "model-b"}]


def test_list_loras_returns_empty_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")

    async def fake_request(_method: str, _endpoint: str, json_payload: dict[str, Any] | None = None) -> Any:
        raise SDError("missing", code="sd_client_error", http_status=404)

    monkeypatch.setattr(client, "_request_json", fake_request)

    assert asyncio.run(client.list_loras()) == []


def test_list_loras_reraises_non_404_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")

    async def fake_request(_method: str, _endpoint: str, json_payload: dict[str, Any] | None = None) -> Any:
        raise SDError("boom", code="sd_server_error", http_status=500)

    monkeypatch.setattr(client, "_request_json", fake_request)

    with pytest.raises(SDError, match="boom"):
        asyncio.run(client.list_loras())


def test_txt2img_builds_payload_and_ignores_invalid_info(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SDClient("sd.local")
    captured: dict[str, Any] = {}
    png_bytes = b"png-bytes"

    async def fake_request(method: str, endpoint: str, json_payload: dict[str, Any] | None = None) -> Any:
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["payload"] = json_payload
        return {"images": [base64.b64encode(png_bytes).decode("ascii")], "info": "{bad json"}

    monkeypatch.setattr(client, "_request_json", fake_request)

    result = asyncio.run(
        client.txt2img(
            prompt="pixel hero",
            negative_prompt="blurry",
            model_name="pixel-xl",
            width=64,
            height=96,
            steps=12,
            cfg_scale=6.5,
            sampler_name="Euler a",
            seed=123,
        )
    )

    assert result == GenerationResult(
        image_bytes=png_bytes,
        seed=None,
        model="pixel-xl",
        prompt="pixel hero",
        width=64,
        height=96,
    )
    assert captured == {
        "method": "POST",
        "endpoint": "/sdapi/v1/txt2img",
        "payload": {
            "prompt": "pixel hero",
            "negative_prompt": "blurry",
            "width": 64,
            "height": 96,
            "steps": 12,
            "cfg_scale": 6.5,
            "sampler_name": "Euler a",
            "n_iter": 1,
            "batch_size": 1,
            "seed": 123,
            "override_settings": {"sd_model_checkpoint": "pixel-xl"},
        },
    }


def test_choose_native_resolution_prefers_explicit_and_detects_xl() -> None:
    assert SDClient.choose_native_resolution("any", 128, 256) == (128, 256)
    assert SDClient.choose_native_resolution("pixelArtDiffusionXL", None, None) == (1024, 1024)
    assert SDClient.choose_native_resolution("dreamshaper_8", None, None) == (512, 512)


class _FakeResponse:
    def __init__(self, status: int, payload: Any = None, body: str = "") -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def text(self) -> str:
        return self._body

    async def json(self) -> Any:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[Any], calls: list[tuple[str, str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def request(self, method: str, url: str, json: dict[str, Any] | None = None) -> Any:  # noqa: A002
        self._calls.append((method, url, json))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_request_json_retries_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []
    sleeps: list[int] = []
    responses = [asyncio.TimeoutError(), _FakeResponse(200, payload={"ok": True})]

    def fake_session(*, timeout: aiohttp.ClientTimeout) -> _FakeSession:
        assert isinstance(timeout, aiohttp.ClientTimeout)
        return _FakeSession(responses, calls)

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    client = SDClient("sd.local", retries=2)
    monkeypatch.setattr("generator.aiohttp.ClientSession", fake_session)
    monkeypatch.setattr("generator.asyncio.sleep", fake_sleep)

    result = asyncio.run(client._request_json("POST", "/sdapi/v1/txt2img", {"prompt": "hero"}))

    assert result == {"ok": True}
    assert calls == [
        ("POST", "http://sd.local/sdapi/v1/txt2img", {"prompt": "hero"}),
        ("POST", "http://sd.local/sdapi/v1/txt2img", {"prompt": "hero"}),
    ]
    assert sleeps == [2]


def test_request_json_raises_sd_error_for_http_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [_FakeResponse(500, body="CUDA out of memory")]

    def fake_session(*, timeout: aiohttp.ClientTimeout) -> _FakeSession:
        return _FakeSession(responses, [])

    client = SDClient("sd.local", retries=1)
    monkeypatch.setattr("generator.aiohttp.ClientSession", fake_session)

    with pytest.raises(SDError) as exc_info:
        asyncio.run(client._request_json("GET", "/sdapi/v1/sd-models"))

    assert exc_info.value.code == "oom"
    assert exc_info.value.http_status == 500


def test_request_json_raises_unreachable_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [aiohttp.ClientError("connection refused"), aiohttp.ClientError("connection refused")]

    def fake_session(*, timeout: aiohttp.ClientTimeout) -> _FakeSession:
        return _FakeSession(responses, [])

    async def fake_sleep(_seconds: int) -> None:
        return None

    client = SDClient("sd.local", retries=2)
    monkeypatch.setattr("generator.aiohttp.ClientSession", fake_session)
    monkeypatch.setattr("generator.asyncio.sleep", fake_sleep)

    with pytest.raises(SDError) as exc_info:
        asyncio.run(client._request_json("GET", "/sdapi/v1/sd-models"))

    assert exc_info.value.code == "unreachable"


def test_save_generated_image_sanitizes_segments_and_keeps_job_specific_files(tmp_path: Path) -> None:
    first = save_generated_image(b"first", tmp_path, "proj/name", "asset\\..", "job/1")
    second = save_generated_image(b"second", tmp_path, "proj/name", "asset\\..", "job/2")

    assert first.name == "asset____job_1.png"
    assert second.name == "asset____job_2.png"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert first != second


def test_save_candidate_slot_image_writes_nested_slot_path(tmp_path: Path) -> None:
    path = save_candidate_slot_image(b"slot", tmp_path, "proj/name", "asset\\key", "job/42", 3)

    assert path == tmp_path / "candidates" / "proj_name" / "asset_key" / "job_42" / "slot_3.png"
    assert path.read_bytes() == b"slot"
