"""Generator module for Asset Factory - SD API 호출 + 저장."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp


class SDError(RuntimeError):
    """SD API 호출 실패(원인 분류 코드 포함)."""

    def __init__(self, message: str, *, code: str = "sd_error", http_status: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(f"[{code}] {message}")


def _classify_sd_failure(status: int, body: str) -> str:
    """HTTP 상태 및 응답 본문으로 실패 유형을 추정한다."""
    lower = body.lower()
    if status == 504 or "timeout" in lower:
        return "timeout"
    if "out of memory" in lower or "ran out of memory" in lower or "oom" in lower:
        return "oom"
    if status >= 500:
        return "sd_server_error"
    return "sd_client_error"


@dataclass(slots=True)
class GenerationResult:
    """SD 생성 결과."""

    image_bytes: bytes
    seed: int | None
    model: str | None
    prompt: str
    width: int
    height: int


class SDClient:
    """AUTOMATIC1111 WebUI API 클라이언트."""

    def __init__(
        self,
        host: str,
        timeout_seconds: float = 45.0,
        retries: int = 3,
    ) -> None:
        self.base_url = f"http://{host}"
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.retries = retries

    async def health_check(self) -> dict[str, Any]:
        """모델 목록 API로 서버 연결 상태를 점검한다."""
        response = await self._request_json("GET", "/sdapi/v1/sd-models")
        models = [item.get("model_name") for item in response if isinstance(item, dict)]
        return {
            "ok": True,
            "model_count": len(models),
            "models": [model for model in models if model][:10],
        }

    async def txt2img(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        model_name: str | None = None,
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg_scale: float = 7.0,
        sampler_name: str = "DPM++ 2M",
    ) -> GenerationResult:
        """텍스트 프롬프트로 이미지를 생성한다."""
        payload: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler_name,
            "n_iter": 1,
            "batch_size": 1,
        }
        if model_name:
            payload["override_settings"] = {"sd_model_checkpoint": model_name}

        response = await self._request_json("POST", "/sdapi/v1/txt2img", json_payload=payload)
        image_base64 = response["images"][0]
        image_bytes = base64.b64decode(image_base64)

        info: dict[str, Any] = {}
        if "info" in response and response["info"]:
            try:
                info = json.loads(response["info"])
            except (json.JSONDecodeError, TypeError):
                info = {}

        return GenerationResult(
            image_bytes=image_bytes,
            seed=info.get("seed"),
            model=model_name,
            prompt=prompt,
            width=width,
            height=height,
        )

    @staticmethod
    def choose_native_resolution(model_name: str | None, width: int | None, height: int | None) -> tuple[int, int]:
        """모델 계열(SDXL/SD1.5)에 맞게 기본 해상도를 선택한다."""
        if width and height:
            return width, height
        if model_name and "xl" in model_name.lower():
            return 1024, 1024
        return 512, 512

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        json_payload: dict[str, Any] | None = None,
    ) -> Any:
        """재시도/백오프를 포함한 HTTP 요청."""
        backoff_seconds = 2

        for attempt in range(1, self.retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.request(
                        method,
                        f"{self.base_url}{endpoint}",
                        json=json_payload,
                    ) as response:
                        if response.status >= 400:
                            body = await response.text()
                            code = _classify_sd_failure(response.status, body)
                            raise SDError(
                                body[:2000] or f"HTTP {response.status}",
                                code=code,
                                http_status=response.status,
                            )
                        return await response.json()
            except SDError:
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                code = "timeout" if isinstance(exc, asyncio.TimeoutError) else "unreachable"
                err = SDError(str(exc) or "request failed", code=code)
                if attempt == self.retries:
                    raise err from exc
                await asyncio.sleep(backoff_seconds)
                backoff_seconds *= 2

        raise SDError("SD API 요청 실패", code="unreachable")


def _safe_path_segment(value: str) -> str:
    """파일 경로 세그먼트로 사용할 수 없는 문자를 정리한다."""
    return value.replace("/", "_").replace("\\", "_").replace("..", "_")


def save_generated_image(
    image_bytes: bytes,
    output_root: Path,
    project: str,
    asset_key: str,
    job_id: str | None = None,
) -> Path:
    """생성 이미지를 후보 디렉토리에 저장한다.

    Job ID가 주어지면 generation 별 unique 경로에 저장하여 이전 결과 파일이
    덮어써지지 않도록 한다(asset_history의 image_path가 디스크에 유지됨)."""
    safe_project = _safe_path_segment(project)
    safe_key = _safe_path_segment(asset_key)
    project_dir = output_root / "candidates" / safe_project
    project_dir.mkdir(parents=True, exist_ok=True)
    if job_id:
        safe_job = _safe_path_segment(job_id)
        output_path = project_dir / f"{safe_key}__{safe_job}.png"
    else:
        output_path = project_dir / f"{safe_key}.png"
    output_path.write_bytes(image_bytes)
    return output_path


def save_candidate_slot_image(
    image_bytes: bytes,
    output_root: Path,
    project: str,
    asset_key: str,
    job_id: str,
    slot_index: int,
) -> Path:
    """다중 후보 생성 시 슬롯별 파일 경로에 저장한다."""
    safe_project = _safe_path_segment(project)
    safe_key = _safe_path_segment(asset_key)
    safe_job = _safe_path_segment(job_id)
    target_dir = output_root / "candidates" / safe_project / safe_key / safe_job
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / f"slot_{slot_index}.png"
    output_path.write_bytes(image_bytes)
    return output_path
