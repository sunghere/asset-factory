"""ComfyUIClient HTTP 레벨 통합 + edge case 테스트.

기존 ``test_workflow_endpoints.py`` / ``test_sd_backend.py`` 는 ``submit_and_wait``
를 통째로 monkeypatch 해 ComfyUIClient 의 진짜 HTTP 시퀀스 (POST /prompt → poll
/history → GET /view) 를 한 줄도 안 거친다. 본 파일은 ``aioresponses`` 로
ComfyUI 응답을 흉내내 그 시퀀스를 직접 검증한다 (P1.b/P1.h).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aioresponses import aioresponses

from generator import SDError
from generator_comfyui import ComfyUIClient


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _client(**overrides):  # noqa: ANN001
    kwargs = {
        "host": "comfy:8188",
        "request_timeout": 1.0,
        "completion_timeout": 5.0,
        "poll_interval": 0.01,
        "retries": 2,
        "prompt_lost_threshold": 2,
    }
    kwargs.update(overrides)
    return ComfyUIClient(**kwargs)


# ----------------------------------------------------------------------------
# P1.b — full happy-path integration (POST /prompt → /history → /view)
# ----------------------------------------------------------------------------


def test_submit_and_wait_full_http_sequence_assembles_result() -> None:
    """ComfyUIClient.submit_and_wait 가 실제 ComfyUI HTTP 시퀀스를 흉내낸 응답에서
    prompt_id, outputs 매핑, image bytes 까지 정확히 조립해야 한다."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/prompt",
                    payload={"prompt_id": "P-1", "number": 0, "node_errors": {}},
                )
                # 첫 번째 history 폴링: 아직 running (status.completed=false)
                m.get(
                    "http://comfy:8188/history/P-1",
                    payload={"P-1": {"status": {"completed": False}}},
                )
                # 두 번째 폴링: 완료 + outputs
                m.get(
                    "http://comfy:8188/history/P-1",
                    payload={
                        "P-1": {
                            "status": {"completed": True, "status_str": "success"},
                            "outputs": {
                                "13": {
                                    "images": [
                                        {
                                            "filename": "a.png",
                                            "subfolder": "",
                                            "type": "output",
                                        }
                                    ]
                                }
                            },
                        }
                    },
                )
                m.get(
                    "http://comfy:8188/view?filename=a.png&subfolder=&type=output",
                    body=b"PNG_BYTES_A",
                )

                wf = {"5": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}}}
                result = await client.submit_and_wait(wf)

                assert result.prompt_id == "P-1"
                assert len(result.outputs) == 1
                img = result.outputs[0]
                assert img.node_id == "13"
                assert img.filename == "a.png"
                assert img.image_bytes == b"PNG_BYTES_A"

    asyncio.run(scenario())


# ----------------------------------------------------------------------------
# P1.h — edge cases
# ----------------------------------------------------------------------------


def test_submit_node_errors_raises_sd_client_error() -> None:
    """ComfyUI 가 200 응답 + node_errors 비지 않음 → SDError(sd_client_error)."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/prompt",
                    payload={
                        "prompt_id": "P-2",
                        "node_errors": {"5": {"errors": ["bad input"]}},
                    },
                )
                with pytest.raises(SDError) as ei:
                    await client.submit({"5": {"class_type": "X"}})
                assert ei.value.code == "sd_client_error"

    asyncio.run(scenario())


def test_submit_missing_prompt_id_raises_server_error() -> None:
    """200 인데 prompt_id 누락 → sd_server_error."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post("http://comfy:8188/prompt", payload={"node_errors": {}})
                with pytest.raises(SDError) as ei:
                    await client.submit({})
                assert ei.value.code == "sd_server_error"

    asyncio.run(scenario())


def test_request_503_classified_as_sd_server_error() -> None:
    """5xx 응답 → sd_server_error."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.get(
                    "http://comfy:8188/system_stats",
                    status=503,
                    body="Service Unavailable",
                )
                with pytest.raises(SDError) as ei:
                    await client.health_check()
                assert ei.value.code == "sd_server_error"

    asyncio.run(scenario())


def test_request_504_classified_as_timeout() -> None:
    """504 응답 → timeout 코드."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.get(
                    "http://comfy:8188/system_stats",
                    status=504,
                    body="Gateway Timeout",
                )
                with pytest.raises(SDError) as ei:
                    await client.health_check()
                assert ei.value.code == "timeout"

    asyncio.run(scenario())


def test_history_status_error_raises_server_error() -> None:
    """status.completed=True, status_str=error → sd_server_error."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.get(
                    "http://comfy:8188/history/P-3",
                    payload={
                        "P-3": {
                            "status": {
                                "completed": True,
                                "status_str": "error",
                                "messages": [["execution_error", {"why": "OOM"}]],
                            }
                        }
                    },
                )
                with pytest.raises(SDError) as ei:
                    await client.wait_for_history("P-3")
                assert ei.value.code == "sd_server_error"
                assert "OOM" in str(ei.value)

    asyncio.run(scenario())


def test_history_completion_timeout_raises_timeout() -> None:
    """deadline 초과 → timeout. completion_timeout 짧게 + 항상 미완성 응답."""

    async def scenario() -> None:
        async with _client(completion_timeout=0.05, prompt_lost_threshold=999) as client:
            with aioresponses() as m:
                # 항상 incomplete — 폴링 무한 반복하다 deadline 으로 터짐
                m.get(
                    "http://comfy:8188/history/P-4",
                    payload={"P-4": {"status": {"completed": False}}},
                    repeat=True,
                )
                with pytest.raises(SDError) as ei:
                    await client.wait_for_history("P-4")
                assert ei.value.code == "timeout"

    asyncio.run(scenario())


def test_prompt_lost_when_history_empty_and_queue_empty() -> None:
    """P1.d — history 에 N회 연속 missing + /queue 도 비어있으면 즉시 fail-fast."""

    async def scenario() -> None:
        async with _client(
            prompt_lost_threshold=2,
            completion_timeout=10.0,
        ) as client:
            with aioresponses() as m:
                # history 가 항상 빈 dict (prompt_id 없음)
                m.get("http://comfy:8188/history/P-5", payload={}, repeat=True)
                # queue 도 비어있음
                m.get(
                    "http://comfy:8188/queue",
                    payload={"queue_running": [], "queue_pending": []},
                    repeat=True,
                )
                with pytest.raises(SDError) as ei:
                    await client.wait_for_history("P-5")
                assert ei.value.code == "sd_server_error"
                assert "lost" in str(ei.value).lower()

    asyncio.run(scenario())


def test_prompt_in_queue_means_keep_waiting_not_lost() -> None:
    """history missing 이지만 queue 에 prompt 있으면 lost 처리 안 하고 계속 대기."""

    async def scenario() -> None:
        async with _client(
            prompt_lost_threshold=2,
            completion_timeout=0.3,
        ) as client:
            with aioresponses() as m:
                m.get("http://comfy:8188/history/P-6", payload={}, repeat=True)
                m.get(
                    "http://comfy:8188/queue",
                    payload={
                        "queue_running": [],
                        "queue_pending": [[0, "P-6", {}, {}, []]],
                    },
                    repeat=True,
                )
                # queue 에 있으니 lost 안 되고, 결국 deadline 으로 timeout
                with pytest.raises(SDError) as ei:
                    await client.wait_for_history("P-6")
                assert ei.value.code == "timeout"

    asyncio.run(scenario())


def test_partial_history_no_outputs_returns_empty_result() -> None:
    """status.completed=True 인데 outputs 키 없음 → ComfyUIResult.outputs 빈 리스트."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/prompt",
                    payload={"prompt_id": "P-7", "node_errors": {}},
                )
                m.get(
                    "http://comfy:8188/history/P-7",
                    payload={
                        "P-7": {
                            "status": {"completed": True, "status_str": "success"},
                            # outputs 키 자체가 없음 (또는 빈 dict)
                        }
                    },
                )
                result = await client.submit_and_wait({"5": {"class_type": "X"}})
                assert result.prompt_id == "P-7"
                assert result.outputs == []

    asyncio.run(scenario())


def test_view_404_classified_as_sd_client_error() -> None:
    """fetch_image 의 4xx → sd_client_error (NON_RETRYABLE)."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.get(
                    "http://comfy:8188/view?filename=missing.png&subfolder=&type=output",
                    status=404,
                    body="not found",
                )
                with pytest.raises(SDError) as ei:
                    await client.fetch_image("missing.png")
                assert ei.value.code == "sd_client_error"

    asyncio.run(scenario())


# ----------------------------------------------------------------------------
# 세션 라이프사이클 (P1.c)
# ----------------------------------------------------------------------------


def test_close_idempotent() -> None:
    """close() 를 두 번 불러도 예외 없이 멱등."""

    async def scenario() -> None:
        client = _client()
        await client.close()
        await client.close()
        assert client._session is None

    asyncio.run(scenario())


def test_async_context_manager_closes_session() -> None:
    """async with 빠져나오면 세션이 닫힌다."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.get(
                    "http://comfy:8188/system_stats",
                    payload={"system": {"comfyui_version": "x"}, "devices": []},
                )
                await client.health_check()
            assert client._session is not None
            assert not client._session.closed
        # exit 후 닫힘
        assert client._session is None

    asyncio.run(scenario())


# ----------------------------------------------------------------------------
# upload_input_image (POST /upload/image, multipart)
# ----------------------------------------------------------------------------


def test_upload_input_image_happy_path_returns_response_dict() -> None:
    """200 + ComfyUI JSON 응답을 그대로 호출자에게 전달."""

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    payload={
                        "name": "abc_input.png",
                        "subfolder": "asset-factory",
                        "type": "input",
                    },
                )
                resp = await client.upload_input_image(
                    image_bytes=b"PNG_BYTES",
                    filename="abc_input.png",
                )
                assert resp == {
                    "name": "abc_input.png",
                    "subfolder": "asset-factory",
                    "type": "input",
                }

    asyncio.run(scenario())


def test_upload_input_image_413_classified_as_sd_client_error() -> None:
    """ComfyUI 가 4xx 거부 → sd_client_error (재시도 안 함)."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    status=413,
                    body="Payload Too Large",
                )
                with pytest.raises(SDError) as ei:
                    await client.upload_input_image(
                        image_bytes=b"x", filename="x.png",
                    )
                assert ei.value.code == "sd_client_error"
                assert ei.value.http_status == 413

    asyncio.run(scenario())


def test_upload_input_image_503_classified_as_sd_server_error() -> None:
    """5xx → sd_server_error."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    status=503,
                    body="Service Unavailable",
                )
                with pytest.raises(SDError) as ei:
                    await client.upload_input_image(
                        image_bytes=b"x", filename="x.png",
                    )
                assert ei.value.code == "sd_server_error"

    asyncio.run(scenario())


def test_upload_input_image_504_classified_as_timeout() -> None:
    """504 → timeout."""

    async def scenario() -> None:
        async with _client(retries=1) as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    status=504,
                    body="Gateway Timeout",
                )
                with pytest.raises(SDError) as ei:
                    await client.upload_input_image(
                        image_bytes=b"x", filename="x.png",
                    )
                assert ei.value.code == "timeout"

    asyncio.run(scenario())


def test_upload_input_image_connection_error_classified_as_unreachable() -> None:
    """aiohttp.ClientError → unreachable (모든 재시도 소진 후)."""
    import aiohttp

    async def scenario() -> None:
        async with _client(retries=2) as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    exception=aiohttp.ClientConnectionError("conn refused"),
                )
                m.post(
                    "http://comfy:8188/upload/image",
                    exception=aiohttp.ClientConnectionError("conn refused"),
                )
                with pytest.raises(SDError) as ei:
                    await client.upload_input_image(
                        image_bytes=b"x", filename="x.png",
                    )
                assert ei.value.code == "unreachable"

    asyncio.run(scenario())


def test_upload_input_image_retry_then_success() -> None:
    """첫 시도 실패 → 재시도 성공. FormData 가 재구성돼야 한다 (소진 회귀 방어)."""
    import aiohttp

    async def scenario() -> None:
        async with _client(retries=2) as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    exception=aiohttp.ClientConnectionError("transient"),
                )
                m.post(
                    "http://comfy:8188/upload/image",
                    payload={
                        "name": "x.png",
                        "subfolder": "asset-factory",
                        "type": "input",
                    },
                )
                resp = await client.upload_input_image(
                    image_bytes=b"PNG_BYTES",
                    filename="x.png",
                )
                assert resp["name"] == "x.png"

    asyncio.run(scenario())


def test_upload_input_image_overwrite_false_param_propagates() -> None:
    """overwrite=False → form field 'false'. aioresponses 의 requests 기록으로 검증."""
    from yarl import URL

    async def scenario() -> None:
        async with _client() as client:
            with aioresponses() as m:
                m.post(
                    "http://comfy:8188/upload/image",
                    payload={"name": "x.png", "subfolder": "asset-factory", "type": "input"},
                )
                await client.upload_input_image(
                    image_bytes=b"PNG",
                    filename="x.png",
                    overwrite=False,
                )

                # aioresponses 가 잡은 호출 — kwargs['data'] 가 FormData 인스턴스
                key = ("POST", URL("http://comfy:8188/upload/image"))
                calls = m.requests.get(key) or []
                assert len(calls) == 1
                form = calls[0].kwargs["data"]
                # FormData._fields: list of (info, headers, value)
                fields = {f[0]["name"]: f[2] for f in form._fields}
                assert fields["overwrite"] == "false"
                assert fields["type"] == "input"
                assert fields["subfolder"] == "asset-factory"

    asyncio.run(scenario())
