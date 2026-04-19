"""SSE keep-alive 회귀 테스트.

이벤트 스트림 자체를 HTTP로 열고 빠져나오는 패턴은 여러 클라이언트(httpx 동기/
비동기, TestClient)에서 chunk flush 타이밍에 의존해 flaky 하기 때문에,
endpoint에서 분리한 ``sse_event_generator`` async generator를 직접 검증한다.
프로덕션 ``stream_events``는 이 generator + Cache-Control/X-Accel-Buffering
헤더만 추가로 감싸므로 본질적인 keep-alive 로직은 동일하게 보장된다.
"""

from __future__ import annotations

import asyncio

import server


def test_sse_event_generator_emits_keepalive_when_idle() -> None:
    """idle 상태에서 keep-alive 코멘트 프레임이 흐름에 포함되어야 한다."""
    broker = server.EventBroker()

    async def scenario() -> str:
        gen = server.sse_event_generator(broker, keepalive_seconds=0.05)
        # 첫 프레임은 idle이므로 keep-alive 여야 한다.
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        await gen.aclose()
        return frame

    frame = asyncio.run(scenario())
    assert frame == ": keep-alive\n\n"


def test_sse_event_generator_yields_published_event() -> None:
    """publish 된 이벤트가 data 프레임으로 흘러나와야 한다."""
    broker = server.EventBroker()

    async def scenario() -> list[str]:
        gen = server.sse_event_generator(broker, keepalive_seconds=5.0)
        # 잠시 후 이벤트 publish.
        async def _publisher() -> None:
            await asyncio.sleep(0.05)
            await broker.publish({"type": "ping", "value": 1})

        pub_task = asyncio.create_task(_publisher())
        try:
            frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        finally:
            pub_task.cancel()
            try:
                await pub_task
            except asyncio.CancelledError:
                pass
            await gen.aclose()
        return [frame]

    frames = asyncio.run(scenario())
    assert frames[0].startswith("data: ")
    assert '"type": "ping"' in frames[0]


def test_sse_endpoint_sets_no_buffering_header(monkeypatch) -> None:  # noqa: ANN001
    """엔드포인트가 X-Accel-Buffering: no, Cache-Control: no-cache 헤더를 노출.

    HTTP 스트림을 실제로 열면 응답 본문이 무한이라 클라이언트가 hang 한다.
    핸들러를 직접 호출해 ``StreamingResponse`` 객체를 받아 헤더만 검사한다.
    """
    monkeypatch.setenv("SSE_KEEPALIVE_SEC", "0.05")
    response = asyncio.run(server.stream_events())
    assert response.headers.get("x-accel-buffering") == "no"
    assert response.headers.get("cache-control") == "no-cache"
    assert response.media_type == "text/event-stream"
