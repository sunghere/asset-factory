"""ComfyUI HTTP 백엔드 클라이언트.

A1111 의 `SDClient` (generator.py) 와 같은 책임을 하지만 ComfyUI 의 다른
프로토콜에 맞춘다.

ComfyUI 프로토콜 요약:
    POST /prompt          {prompt: <api_json>, client_id: <uuid>}
                          → {prompt_id, number, node_errors}
    GET  /history/{id}    → {<id>: {prompt, outputs: {node_id: {images: [...]}},
                                    status: {status_str, completed, messages}}}
    GET  /view            ?filename=...&subfolder=...&type=output → image bytes
    GET  /system_stats    → {system, devices, ...}
    GET  /queue           → {queue_running, queue_pending}

특징:
- 동기식 (워커는 한 번에 한 prompt 처리). websocket 미사용 — `/history` 폴링.
- `submit_and_wait()` 가 모든 SaveImage 노드의 이미지를 미리 다운로드해서
  `ComfyUIResult.outputs` 로 묶어 돌려준다 (지연 로딩 X — 호출자 단순화 우선).
- 에러 코드 분류는 SDError 와 호환 (`timeout`, `oom`, `unreachable`, ...) 로
  asset-factory 워커의 재시도 로직(`NON_RETRYABLE_SD_CODES`) 을 그대로 재사용
  가능하게 한다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from generator import SDError  # 코드 분류 호환을 위해 그대로 재사용


# ----------------------------------------------------------------------------
# Result dataclasses
# ----------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ComfyUIOutputImage:
    """한 SaveImage 노드의 결과 이미지 한 장."""

    node_id: str
    filename: str
    subfolder: str
    type: str               # "output" | "temp"
    image_bytes: bytes


@dataclass(slots=True)
class ComfyUIResult:
    """submit_and_wait() 결과 — 워크플로우 한 번 실행에 N개 이미지."""

    prompt_id: str
    outputs: list[ComfyUIOutputImage] = field(default_factory=list)
    raw_history: dict[str, Any] = field(default_factory=dict)

    def by_node(self, node_id: str) -> list[ComfyUIOutputImage]:
        return [o for o in self.outputs if o.node_id == node_id]


# ----------------------------------------------------------------------------
# 에러 분류
# ----------------------------------------------------------------------------


def _classify_comfy_failure(status: int, body: str) -> str:
    """SDClient._classify_sd_failure 와 호환되는 에러 코드 분류."""
    lower = body.lower()
    if status == 504 or "timeout" in lower:
        return "timeout"
    if "out of memory" in lower or "cuda out of memory" in lower or "oom" in lower:
        return "oom"
    if status >= 500:
        return "sd_server_error"
    return "sd_client_error"


# ----------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------


class ComfyUIClient:
    """ComfyUI HTTP API 클라이언트.

    Args:
        host: "host:port" 형식. 예: "localhost:8188"
        request_timeout: 단일 HTTP 요청 타임아웃 (초)
        completion_timeout: submit_and_wait 가 한 prompt 완료를 기다리는 최대 시간
        poll_interval: /history 폴링 주기 (초)
        retries: HTTP 일시 장애 시 재시도 횟수
        prompt_lost_threshold: wait_for_history 폴링 중 N회 연속 history 미존재 +
            queue 에도 없으면 "prompt lost" 로 즉시 fail-fast (10분 헛 대기 방지)

    P1.c — `ClientSession` 은 호출 시점에 lazy 생성해 재사용한다.
    명시적으로 닫으려면 ``await client.close()`` 또는 ``async with`` 사용.
    """

    def __init__(
        self,
        host: str,
        request_timeout: float = 30.0,
        completion_timeout: float = 600.0,
        poll_interval: float = 2.0,
        retries: int = 3,
        client_id: str | None = None,
        prompt_lost_threshold: int = 3,
    ) -> None:
        self.base_url = f"http://{host}"
        self.request_timeout = aiohttp.ClientTimeout(total=request_timeout)
        self.completion_timeout = completion_timeout
        self.poll_interval = poll_interval
        self.retries = retries
        self.prompt_lost_threshold = prompt_lost_threshold
        # 같은 client_id 를 유지하면 ComfyUI 가 큐에서 우리 prompt 를 식별 가능
        self.client_id = client_id or str(uuid.uuid4())
        # P1.c — 세션 재사용. None 이면 lazy 생성.
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """lazy 세션 — 닫혀 있거나 없으면 새로 만든다."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.request_timeout)
        return self._session

    async def close(self) -> None:
        """소유한 세션 명시적 종료. 테스트 / 셧다운 후크에서 호출."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> ComfyUIClient:
        await self._get_session()
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        await self.close()

    # ---- health -----------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """`/system_stats` 로 서버 살아있는지 확인."""
        stats = await self._request_json("GET", "/system_stats")
        system = stats.get("system", {}) if isinstance(stats, dict) else {}
        devices = stats.get("devices", []) if isinstance(stats, dict) else []
        return {
            "ok": True,
            "comfyui_version": system.get("comfyui_version"),
            "python_version": system.get("python_version"),
            "device_count": len(devices),
            "device_names": [d.get("name") for d in devices if isinstance(d, dict)],
        }

    async def queue_state(self) -> dict[str, Any]:
        return await self._request_json("GET", "/queue")

    # ---- submit + wait ----------------------------------------------------

    async def submit(self, api_json: dict[str, Any]) -> str:
        """워크플로우를 큐에 enqueue, prompt_id 반환.

        ComfyUI 가 노드 검증 단계에서 에러를 발견하면 200 응답 안의
        `node_errors` 가 비어있지 않다 — 이 경우 즉시 SDError 로 던진다.
        """
        body = {"prompt": api_json, "client_id": self.client_id}
        response = await self._request_json("POST", "/prompt", json_payload=body)
        if not isinstance(response, dict):
            raise SDError(f"unexpected /prompt response: {response!r}", code="sd_server_error")

        node_errors = response.get("node_errors") or {}
        if node_errors:
            raise SDError(
                f"ComfyUI node validation failed: {json.dumps(node_errors)[:1500]}",
                code="sd_client_error",
            )

        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise SDError(
                f"/prompt response missing prompt_id: {response!r}",
                code="sd_server_error",
            )
        return str(prompt_id)

    async def wait_for_history(
        self,
        prompt_id: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """`/history/{id}` 가 status.completed=true 가 될 때까지 폴링.

        P1.d — prompt_id 가 history 에 없는 상태가 ``prompt_lost_threshold`` 회
        연속이고 ``/queue`` 에도 (running 또는 pending) 안 보이면 즉시 fail-fast.
        ComfyUI 재시작 등으로 prompt 가 사라진 경우 10분 헛 대기 방지.
        """
        deadline = asyncio.get_event_loop().time() + (timeout or self.completion_timeout)
        not_in_history_streak = 0

        while True:
            now = asyncio.get_event_loop().time()
            if now > deadline:
                raise SDError(
                    f"ComfyUI prompt {prompt_id} 완료 대기 timeout",
                    code="timeout",
                )

            history = await self._request_json("GET", f"/history/{prompt_id}")
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if entry:
                not_in_history_streak = 0
                status = entry.get("status") or {}
                if status.get("completed"):
                    if status.get("status_str") == "error":
                        msgs = status.get("messages") or []
                        raise SDError(
                            f"ComfyUI prompt failed: {json.dumps(msgs)[:2000]}",
                            code="sd_server_error",
                        )
                    return entry
            else:
                not_in_history_streak += 1
                # 연속 N회 missing → /queue 확인 후 양쪽 다 없으면 lost
                if not_in_history_streak >= self.prompt_lost_threshold:
                    if not await self._is_prompt_in_queue(prompt_id):
                        raise SDError(
                            f"ComfyUI prompt {prompt_id} lost — history/queue 어디에도 "
                            "없음 (서버 재시작 등 추정)",
                            code="sd_server_error",
                        )
                    # queue 에 있으면 아직 실행 전 — 카운터 리셋, 계속 대기
                    not_in_history_streak = 0

            await asyncio.sleep(self.poll_interval)

    async def _is_prompt_in_queue(self, prompt_id: str) -> bool:
        """``/queue`` 의 running + pending 어딘가에 prompt_id 가 있는지."""
        try:
            queue = await self.queue_state()
        except SDError:
            # queue 조회 실패는 결정 불가 → 안전하게 True 로 (계속 대기)
            return True
        if not isinstance(queue, dict):
            return False
        for key in ("queue_running", "queue_pending"):
            for entry in queue.get(key) or []:
                # ComfyUI 응답 구조: [number, prompt_id, prompt_dict, ...]
                if isinstance(entry, list) and len(entry) >= 2 and str(entry[1]) == prompt_id:
                    return True
        return False

    async def fetch_image(
        self,
        filename: str,
        subfolder: str = "",
        type_: str = "output",
    ) -> bytes:
        """`/view` 로 결과 이미지 다운로드. P1.c — 공유 세션 사용."""
        last_err: BaseException | None = None
        backoff = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                session = await self._get_session()
                params = {"filename": filename, "subfolder": subfolder, "type": type_}
                async with session.get(f"{self.base_url}/view", params=params) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise SDError(
                            body[:1500] or f"HTTP {resp.status}",
                            code=_classify_comfy_failure(resp.status, body),
                            http_status=resp.status,
                        )
                    return await resp.read()
            except SDError:
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                last_err = exc
                if attempt == self.retries:
                    raise SDError(
                        str(exc) or "view request failed",
                        code="timeout" if isinstance(exc, asyncio.TimeoutError) else "unreachable",
                    ) from exc
                await asyncio.sleep(backoff)
                backoff *= 2
        # unreachable
        raise SDError("fetch_image 실패 (논리 오류)", code="unreachable") from last_err

    async def submit_and_wait(
        self,
        api_json: dict[str, Any],
        timeout: float | None = None,
    ) -> ComfyUIResult:
        """워크플로우 enqueue → 완료 대기 → 모든 SaveImage 결과 다운로드."""
        prompt_id = await self.submit(api_json)
        history = await self.wait_for_history(prompt_id, timeout=timeout)

        outputs_raw = history.get("outputs") or {}
        result = ComfyUIResult(prompt_id=prompt_id, raw_history=history)

        # outputs = {node_id: {"images": [{filename, subfolder, type}, ...]}}
        for node_id, payload in outputs_raw.items():
            if not isinstance(payload, dict):
                continue
            images = payload.get("images") or []
            for img_meta in images:
                if not isinstance(img_meta, dict):
                    continue
                filename = img_meta.get("filename")
                if not filename:
                    continue
                subfolder = img_meta.get("subfolder") or ""
                img_type = img_meta.get("type") or "output"
                image_bytes = await self.fetch_image(filename, subfolder, img_type)
                result.outputs.append(
                    ComfyUIOutputImage(
                        node_id=str(node_id),
                        filename=str(filename),
                        subfolder=str(subfolder),
                        type=str(img_type),
                        image_bytes=image_bytes,
                    )
                )

        return result

    # ---- 내부 ------------------------------------------------------------

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        json_payload: dict[str, Any] | None = None,
    ) -> Any:
        """SDClient 와 같은 패턴: 재시도 + 분류된 SDError. P1.c — 공유 세션 사용."""
        backoff = 2.0
        for attempt in range(1, self.retries + 1):
            try:
                session = await self._get_session()
                async with session.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    json=json_payload,
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise SDError(
                            body[:2000] or f"HTTP {resp.status}",
                            code=_classify_comfy_failure(resp.status, body),
                            http_status=resp.status,
                        )
                    return await resp.json()
            except SDError:
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                if attempt == self.retries:
                    code = "timeout" if isinstance(exc, asyncio.TimeoutError) else "unreachable"
                    raise SDError(str(exc) or "request failed", code=code) from exc
                await asyncio.sleep(backoff)
                backoff *= 2
        raise SDError("ComfyUI 요청 실패 (논리 오류)", code="unreachable")
