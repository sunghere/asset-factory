"""Asset Factory FastAPI 서버."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import shutil
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from io import BytesIO
from urllib.parse import quote

from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from candidate_gc import run_gc_candidates
from catalog import load_catalog_yaml, merge_loras, merge_models
from generator import (
    SDClient,
    SDError,
    save_candidate_slot_outputs,
    save_generated_outputs,
)
from generator_comfyui import ComfyUIClient
from lib import events as ev
from models import Database
from scanner import scan_directory
from sd_backend import (
    A1111Backend,
    BackendRegistry,
    ComfyUIBackend,
    GenerationOutcome,
)
from prompt_resolution import (
    PromptResolutionError,
    resolve_prompt,
)
from recommendations import recommend as recommend_variants
from recommendations import search as search_variants
from validator import validate_asset
from workflow_registry import WorkflowRegistry, WorkflowRegistryError

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("ASSET_FACTORY_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
DB_PATH = Path(os.getenv("ASSET_FACTORY_DB_PATH", str(DATA_DIR / "asset-factory.db"))).expanduser().resolve()
EXPORT_ROOT = Path(
    os.getenv("ASSET_FACTORY_EXPORT_ROOT", str(Path.home() / "workspace" / "assets"))
).expanduser().resolve()
CATALOG_YAML_PATH = Path(
    os.getenv("SD_CATALOG_PATH", str(BASE_DIR / "config" / "sd_catalog.yml"))
)


def _allowed_roots() -> list[Path]:
    """파일 시스템 접근을 허용할 루트 디렉토리 목록.

    `ASSET_FACTORY_ALLOWED_ROOTS` 환경변수(``os.pathsep`` 구분 — POSIX 는 ``:``,
    Windows 는 ``;``)가 있으면 그 값을 사용한다. Windows 경로는 ``C:\\...`` 처럼
    드라이브 문자에 콜론이 들어가므로 POSIX 와 동일하게 ``:`` 로 split 하면
    경로가 깨진다.
    기본값은 현재 ``DATA_DIR``과 사용자 워크스페이스 자산 디렉토리이다.
    호출 시점에 동적으로 계산하므로 테스트에서 ``server.DATA_DIR`` 등을
    monkeypatch 할 수 있다."""
    extra = os.getenv("ASSET_FACTORY_ALLOWED_ROOTS", "")
    roots: list[Path] = [DATA_DIR.resolve(), EXPORT_ROOT.resolve()]
    if extra:
        for chunk in extra.split(os.pathsep):
            cleaned = chunk.strip()
            if cleaned:
                roots.append(Path(cleaned).expanduser().resolve())
    seen: set[str] = set()
    deduped: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _is_path_within_allowed(target: Path) -> bool:
    """대상 경로가 허용된 루트 중 하나의 하위에 있는지 확인."""
    try:
        resolved = target.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for root in _allowed_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _ensure_path_allowed(target: Path) -> Path:
    """경로 허용 여부를 검사하고, 통과 시 resolve된 경로를 반환한다.

    이 함수는 사용자 입력에서 유래한 경로(``request.root_path``, DB의
    ``image_path``, 쿼리 파라미터 등)에 대한 *경로 정화 함수*(sanitizer)
    역할을 한다. 모든 파일 시스템 접근은 반드시 이 함수의 *반환 값*
    (``resolved``)을 사용해야 하며, 정화되지 않은 원본 경로를 직접 사용하면
    path traversal 위험이 있다. CodeQL의 ``py/path-injection`` 알람을 이
    함수 이후의 데이터 흐름에 대해서는 false positive로 간주한다.
    """
    resolved = target.expanduser().resolve()
    if not _is_path_within_allowed(resolved):
        raise HTTPException(
            status_code=403,
            detail=(
                "허용되지 않은 경로입니다. 환경변수 ASSET_FACTORY_ALLOWED_ROOTS에 "
                "허용 루트를 추가하세요."
            ),
        )
    return resolved


def _safe_segment(value: str) -> str:
    """파일 경로 세그먼트 안전화 (경로 구분자/상위 디렉토리 표기 제거)."""
    return value.replace("/", "_").replace("\\", "_").replace("..", "_")


_SAFE_SUBFOLDER_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
_SAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]")
_DEFAULT_INPUT_SUBFOLDER = "asset-factory"

# ComfyUI 동적 입력 업로드 상수 — /api/workflows/inputs* 가 사용.
# 환경변수 ``ASSET_FACTORY_MAX_INPUT_BYTES`` 로 운영에서 override 가능
# (큰 PoseExtract 입력 등 워크플로우별 요구가 다를 때).
MAX_INPUT_BYTES = int(
    os.getenv("ASSET_FACTORY_MAX_INPUT_BYTES", str(20 * 1024 * 1024))
)
_ALLOWED_INPUT_FORMATS = {"PNG", "JPEG", "WEBP"}
_ALLOWED_INPUT_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _safe_subfolder(value: str | None) -> str:
    """ComfyUI ``input/<subfolder>`` 검증.

    - 빈 문자열 / None → 디폴트 ``asset-factory``
    - ``..`` 포함, 절대경로, NUL byte, 64자 초과 → 디폴트로 정규화
    - 허용: ``[a-zA-Z0-9._-]{1,64}``
    """
    if not value:
        return _DEFAULT_INPUT_SUBFOLDER
    cleaned = value.strip().lstrip("/\\")
    if ".." in cleaned or not _SAFE_SUBFOLDER_RE.fullmatch(cleaned):
        return _DEFAULT_INPUT_SUBFOLDER
    return cleaned


def _safe_input_filename(original: str | None, content_bytes: bytes) -> str:
    """업로드 파일명 안정화 — ``<sha256[:12]>_<safe_original>.<ext>``.

    같은 bytes 재업로드 시 동일 이름이 나오도록 sha256 prefix 사용 (멱등 + 캐시 hit
    검출). 디스플레이 부분은 ``[a-zA-Z0-9._-]`` whitelist, 그 외 ``_`` 치환,
    ``..`` 추가 제거 + 양 끝 ``.`` strip (이렇게 하지 않으면 stem 끝 dot 과 ext
    separator dot 이 결합해 ``..`` 가 부활). path traversal 자체는
    ``_safe_subfolder`` 가 책임 — 본 함수는 디스플레이 안전성 + 추가 보강.
    """
    digest = hashlib.sha256(content_bytes).hexdigest()[:12]
    base = original or "input.png"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        # 확장자 없는 입력 → ``input.png`` 디폴트 분리. ``base`` 는 line 위에서
        # ``or`` 로 truthy 보장된 값이라 추가 fallback 불필요.
        stem, ext = base, "png"
    safe_stem = _SAFE_FILENAME_CHARS.sub("_", stem).replace("..", "_").strip(".") or "input"
    safe_ext = _SAFE_FILENAME_CHARS.sub("_", ext).replace("..", "_").strip(".") or "png"
    return f"{digest}_{safe_stem[:64]}.{safe_ext[:8]}"


def _decode_and_reencode_image(image_bytes: bytes) -> tuple[bytes, str]:
    """PIL 디코딩 + 같은 포맷 재인코딩 — 두 input endpoint 공통 정화 패스.

    공통 보안 동작:
    - PIL ``Image.load()`` 로 픽셀 디코드 — ``verify()`` 보다 강함 (verify 는
      PNG IEND 까지만 검증해 trailing payload 통과)
    - 같은 포맷으로 재인코딩 → trailing ZIP/PHP polyglot 자동 strip + EXIF/ICC
      메타 정화. JPEG 는 ``quality="keep"`` 으로 양자화 테이블 보존 (시각 손실 0)
    - ``DecompressionBombError`` 명시 캐치 — 픽셀폭탄 입력으로 HTTP 500 노출 차단

    Returns:
        ``(sanitized_bytes, format_name)`` — bytes 는 ComfyUI 로 forward 해도 안전
        한 정화본. format 은 ``"PNG"`` / ``"JPEG"`` / ``"WEBP"``.

    Raises:
        HTTPException(400): 디코딩 실패 / DecompressionBomb / 재인코딩 실패
        HTTPException(415): 포맷이 ``_ALLOWED_INPUT_FORMATS`` 외
    """
    try:
        src = Image.open(BytesIO(image_bytes))
        src.load()
        actual_format = src.format
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    if actual_format not in _ALLOWED_INPUT_FORMATS:
        src.close()
        raise HTTPException(
            status_code=415, detail=f"지원 안 되는 포맷: {actual_format}"
        )

    clean_buf = BytesIO()
    save_kwargs: dict[str, object] = {"format": actual_format}
    if actual_format == "JPEG":
        # quality="keep" 으로 양자화 테이블 보존 — 시각 손실 0
        save_kwargs["quality"] = "keep"
    elif actual_format == "WEBP":
        # 무손실 입력은 무손실로 보존, 그 외는 quality=100 high-fidelity.
        # 디폴트 (quality=80) 로 재인코딩하면 무손실 reference 가 silent lossy
        # 변환되어 ControlNet 입력 품질 저하 가능.
        # PIL ``src.info`` 는 WEBP 의 lossless 플래그를 노출하지 않아 RIFF chunk
        # fourcc 직접 검사 (``VP8L`` = lossless, ``VP8 `` = lossy, VP8X 확장 형식
        # 은 우리 endpoint 가 다루는 정적 이미지 범위 외).
        is_lossless = (
            len(image_bytes) >= 16 and image_bytes[12:16] == b"VP8L"
        )
        save_kwargs["lossless"] = is_lossless
        save_kwargs["quality"] = 100
    try:
        src.save(clean_buf, **save_kwargs)
    except (OSError, ValueError) as exc:
        src.close()
        raise HTTPException(
            status_code=400, detail=f"이미지 재인코딩 실패: {exc}"
        ) from exc
    src.close()
    return clean_buf.getvalue(), actual_format


def _validate_comfy_upload_response(result: object) -> dict[str, str]:
    """ComfyUI ``/upload/image`` 응답 shape 검증.

    응답이 ``{"name": "...", "subfolder": "...", "type": "input"}`` 형태여야
    후속 ``workflow_params.load_images.<label>`` 에 박아 쓸 수 있다.
    ``name`` 누락이면 ComfyUI 측 회귀라 502.
    """
    if not isinstance(result, dict) or "name" not in result:
        raise HTTPException(
            status_code=502,
            detail=f"ComfyUI /upload/image 응답이 예상 shape 아님: {result!r}",
        )
    return result


def _approved_dir(project: str) -> Path:
    """승격된 메인 이미지가 들어가는 디렉토리.

    ``DATA_DIR/candidates/`` 와 분리해야 한다 — ``run_gc_candidates`` 가
    candidates 트리를 mtime/용량 기준으로 정리하기 때문에, 같은 트리 안에
    승인본을 두면 GC가 primary asset 파일을 지워서 ``image_path`` 가
    dangling이 된다.
    """
    return DATA_DIR / "approved" / _safe_segment(project)


class GenerateRequest(BaseModel):
    """단일 생성 요청."""

    project: str = Field(..., examples=["cat-raising"])
    asset_key: str = Field(..., examples=["ksh_baby_idle"])
    category: str = Field(default="sprite")
    prompt: str
    negative_prompt: str | None = None
    model_name: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int = 20
    cfg: float = 7.0
    sampler: str = "DPM++ 2M"
    expected_size: int | None = 64
    max_colors: int = 32
    max_retries: int = 3


class BatchGenerateRequest(BaseModel):
    """스펙 기반 배치 생성 요청."""

    project: str | None = None
    spec: dict[str, Any] | None = None
    spec_id: str | None = None


class AssetStatusPatch(BaseModel):
    """에셋 상태 변경 요청."""

    status: str = Field(pattern="^(approved|rejected|pending)$")


class ScanRequest(BaseModel):
    """디렉토리 스캔 요청."""

    project: str
    root_path: str
    max_colors: int = 32


class ExportRequest(BaseModel):
    """승인 에셋 내보내기 요청."""

    project: str | None = None
    category: str | None = None
    since: str | None = None
    output_dir: str = str(EXPORT_ROOT)
    save_manifest: bool = True


class SelectCandidateRequest(BaseModel):
    """후보 슬롯 선택."""

    job_id: str
    slot_index: int = Field(ge=0)


class RestoreHistoryRequest(BaseModel):
    """AssetDetail 히스토리 복원 요청."""

    version: int = Field(ge=1)


class LoraSpec(BaseModel):
    """곱집합 한 칸을 차지할 LoRA 한 개."""

    name: str
    weight: float = Field(default=0.7, ge=-2.0, le=2.0)


class BatchCommonParams(BaseModel):
    """배치 모든 task에 공통 적용할 SD 파라미터."""

    steps: int = Field(default=28, ge=1, le=200)
    cfg: float = Field(default=7.0, ge=0.0, le=30.0)
    sampler: str = Field(default="DPM++ 2M")
    width: int | None = Field(default=None, ge=64, le=2048)
    height: int | None = Field(default=None, ge=64, le=2048)
    negative_prompt: str | None = None
    expected_size: int | None = None
    max_colors: int = Field(default=32, ge=1, le=256)
    max_retries: int = Field(default=3, ge=0, le=10)


class DesignBatchRequest(BaseModel):
    """에이전트 친화 batch 곱집합 spec.

    내부에서 prompts × models × loras × seeds 곱집합을 expand하여
    generation_tasks를 enqueue한다. spec은 client agent가 LLM 등으로
    먼저 풀어서 보내야 한다(AF는 LLM 호출 안 함)."""

    asset_key: str = Field(..., examples=["marine_v2_idle"])
    project: str = Field(default="default-project")
    category: str = Field(default="character")
    prompts: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    loras: list[list[LoraSpec]] = Field(default_factory=list)
    seeds: list[int] | None = None
    seeds_per_combo: int = Field(default=1, ge=1, le=64)
    common: BatchCommonParams = Field(default_factory=BatchCommonParams)


class ApproveFromCandidateRequest(BaseModel):
    """cherry-pick 1장 승인 요청."""

    candidate_id: int = Field(..., ge=1)
    asset_key: str | None = None
    project: str | None = None
    category: str | None = None
    set_status: str = Field(default="approved", pattern="^(approved|pending)$")


class WorkflowGenerateRequest(BaseModel):
    """ComfyUI 워크플로우 호출 요청.

    `workflow_category`/`workflow_variant` 는 `workflow_registry.WorkflowRegistry`
    에 등록된 변형. 추가 패치 인자는 `workflow_params` 딕트에 담는다 — 키는
    `workflow_patcher.patch_workflow` 가 인지하는 것 (`pose_image`,
    `controlnet_strength`, `lora_strengths`, `width`, `height` 등).

    `candidates_total > 1` 이면 같은 변형을 N번 실행 (시드만 다르게) — cherry-pick
    UI 흐름을 그대로 재사용한다. 변형이 multi-output (V38 full = 5장) 이면 각
    슬롯당 N장 저장됨.
    """

    project: str = Field(..., examples=["wooridul-factory"])
    asset_key: str = Field(..., examples=["warrior_idle"])
    category: str = Field(default="sprite")  # asset 카테고리 (DB의 category 컬럼)
    workflow_category: str = Field(..., examples=["sprite"])
    workflow_variant: str = Field(..., examples=["pixel_alpha"])
    # §1.B: prompt 가 default "" 로 변경 — subject 모드는 prompt 없이 호출 가능.
    # legacy 호출은 그대로 prompt 통째 입력.
    prompt: str = ""
    negative_prompt: str | None = None
    seed: int | None = None
    steps: int | None = Field(default=None, ge=1, le=200)
    cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    sampler: str | None = None
    candidates_total: int = Field(default=1, ge=1, le=16)
    workflow_params: dict[str, Any] = Field(default_factory=dict)
    expected_size: int | None = None
    max_colors: int = Field(default=32, ge=1, le=256)
    max_retries: int = Field(default=3, ge=0, le=10)
    # Bypass 모드 — 사람 cherry-pick 큐 우회. 'manual' (default) 또는 'bypass'.
    # bypass 후보는 cherry-pick UI 에 안 뜨고, export manifest 에서도 제외된다.
    # 임시 시뮬·sketch·체인 중간물 등 사람 검수 무의미한 케이스용.
    approval_mode: Literal["manual", "bypass"] = "manual"

    # §1.B subject-injection — 사용자가 캐릭터 묘사만 보내면 변형 yaml 의
    # prompt_template 으로 final prompt 합성. 자세한 모드 선택은
    # `prompt_resolution.resolve_prompt` 참조.
    subject: str | None = Field(
        default=None,
        description=(
            "subject 모드 명시 입력 — 캐릭터/객체 묘사만. 변형의 base_positive/"
            "base_negative 가 자동 합성. 미지정 시 prompt 가 user_input 으로 해석."
        ),
    )
    prompt_mode: Literal["auto", "legacy", "subject"] = Field(
        default="auto",
        description=(
            "auto: 자동 감지 (subject 명시 / prompt_template 부재 / 길이 휴리스틱). "
            "legacy: prompt 통째 입력 그대로. subject: 강제 합성 모드."
        ),
    )
    style_extra: str | None = Field(
        default=None,
        description="subject 모드의 base_positive 뒤에 추가될 사용자 prose (선택).",
    )


class EventBroker:
    """SSE 구독자에게 이벤트를 전달한다."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        # subscribe()/unsubscribe()와 동시에 호출될 수 있으므로 스냅샷을 사용한다.
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


db = Database(DB_PATH)
event_broker = EventBroker()

# SD 백엔드 — A1111 (legacy) + ComfyUI (Phase 2 도입)
# 환경변수 호환: 기존 SD_HOST 가 있으면 A1111 호스트로 본다.
sd_client = SDClient(
    host=os.getenv("SD_A1111_HOST", os.getenv("SD_HOST", "192.168.50.225:7860"))
)
comfyui_client = ComfyUIClient(host=os.getenv("SD_COMFYUI_HOST", "localhost:8188"))
workflow_registry = WorkflowRegistry(root=BASE_DIR / "workflows")
backends = BackendRegistry(
    {
        "a1111": A1111Backend(sd_client),
        "comfyui": ComfyUIBackend(comfyui_client, workflow_registry),
    }
)
api_key = os.getenv("API_KEY")
worker_task: asyncio.Task[Any] | None = None
gc_worker_task: asyncio.Task[Any] | None = None

# System.jsx Worker 블록용 런타임 상태. 프로세스 수명 내에서만 의미 있음.
_worker_state: dict[str, Any] = {
    "last_heartbeat_at": None,  # ISO8601 UTC — 워커 루프가 한 번 돌 때마다 갱신
    "current_task": None,  # 현재 claim 된 태스크 (id/batch_id/status)
    "last_task_id": None,
    "processed_count": 0,
}


def _worker_heartbeat(current_task: dict[str, Any] | None = None) -> None:
    """generation_worker 루프에서 1 tick 마다 호출."""
    _worker_state["last_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
    if current_task is not None:
        _worker_state["current_task"] = {
            "id": int(current_task["id"]),
            "batch_id": current_task.get("batch_id"),
            "asset_key": current_task.get("asset_key"),
            "status": current_task.get("status"),
        }
        _worker_state["last_task_id"] = int(current_task["id"])
    else:
        _worker_state["current_task"] = None


def _worker_tick_done() -> None:
    """handle_task 가 끝나면 호출해 current_task 를 비우고 카운터 증가."""
    _worker_state["current_task"] = None
    _worker_state["processed_count"] = int(_worker_state.get("processed_count", 0)) + 1
    _worker_state["last_heartbeat_at"] = datetime.now(timezone.utc).isoformat()


# System.jsx Logs 블록용 in-memory ring buffer.
# 파일 로그를 재해석 하기보다는, 서버 내부에서 발생한 error/warn 을 직접 수집한다.
_LOG_RING_MAX = 500

# ── SD/A1111 endpoint timeouts ─────────────────────────────────────────────
# sd_client 의 기본 ClientTimeout(45s) × retries(3) = 141s 가 단일 요청에
# 묶이면 동시 요청도 멈춘 듯 보인다. /system, /catalog 화면이 죽은 SD 서버를
# 기다리며 무한 로딩되는 사고를 막기 위해 endpoint 레벨에서 짧은 hard cap.
# - SD_HEALTH_TIMEOUT_SECONDS: /api/health/sd 백엔드별 health_check
# - SD_CATALOG_TIMEOUT_SECONDS: /api/sd/catalog/{models,loras} list 호출
# - COMFYUI_HEALTH_TIMEOUT_SECONDS: /api/comfyui/health (system_stats + queue)
_SD_HEALTH_TIMEOUT_SECONDS = float(os.getenv("SD_HEALTH_TIMEOUT_SECONDS", "5"))
_SD_CATALOG_TIMEOUT_SECONDS = float(os.getenv("SD_CATALOG_TIMEOUT_SECONDS", "5"))
_COMFYUI_HEALTH_TIMEOUT_SECONDS = float(os.getenv("COMFYUI_HEALTH_TIMEOUT_SECONDS", "5"))
_COMFYUI_CATALOG_TIMEOUT_SECONDS = float(os.getenv("COMFYUI_CATALOG_TIMEOUT_SECONDS", "10"))
_COMFYUI_QUEUE_TIMEOUT_SECONDS = float(os.getenv("COMFYUI_QUEUE_TIMEOUT_SECONDS", "5"))
_log_ring: list[dict[str, Any]] = []


def _push_log(level: str, message: str, *, context: dict[str, Any] | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "context": context or {},
    }
    _log_ring.append(entry)
    if len(_log_ring) > _LOG_RING_MAX:
        del _log_ring[: len(_log_ring) - _LOG_RING_MAX]


def _extract_tasks_from_spec(spec: dict[str, Any], project_override: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """스펙 JSON에서 생성 태스크 목록을 추출한다."""
    project = project_override or str(spec.get("project") or "default-project")
    generation_config = spec.get("generation_config", {})
    base_prompt = str(generation_config.get("base_prompt") or "pixel art sprite")
    negative_prompt = generation_config.get("negative_prompt")
    model_name = generation_config.get("model")
    steps = int(generation_config.get("steps", 20))
    cfg = float(generation_config.get("cfg", 7))
    sampler = str(generation_config.get("sampler", "DPM++ 2M"))
    max_colors = int(generation_config.get("max_colors", 32))
    max_retries = int(generation_config.get("max_retries", 3))

    tasks: list[dict[str, Any]] = []

    for character in spec.get("characters", []):
        char_id = str(character.get("id", "character"))
        char_prompt = str(character.get("character_prompt") or "")
        for stage in character.get("stages", []):
            stage_name = str(stage.get("stage", "stage"))
            expected_size = int(stage.get("output_size", 64))
            for action in stage.get("actions", []):
                action_name = str(action)
                asset_key = f"{char_id}_{stage_name}_{action_name}"
                prompt = f"{base_prompt}, {char_prompt}, {stage_name} stage, {action_name} action"
                tasks.append(
                    {
                        "project": project,
                        "asset_key": asset_key,
                        "category": "character",
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                        "model_name": model_name,
                        "width": None,
                        "height": None,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler": sampler,
                        "expected_size": expected_size,
                        "max_colors": max_colors,
                        "max_retries": max_retries,
                    }
                )

    for bucket, category in (("ui_assets", "ui"), ("backgrounds", "background"), ("items", "item")):
        for item in spec.get(bucket, []):
            item_id = str(item.get("id") or "asset")
            prompt_hint = str(item.get("prompt_hint") or "")
            size_value = item.get("size")
            expected_size = int(size_value) if isinstance(size_value, int) else 64
            prompt = f"{base_prompt}, {category}, {prompt_hint}".strip(", ")
            tasks.append(
                {
                    "project": project,
                    "asset_key": item_id,
                    "category": category,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "model_name": model_name,
                    "width": None,
                    "height": None,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler": sampler,
                    "expected_size": expected_size,
                    "max_colors": max_colors,
                    "max_retries": max_retries,
                }
            )

    candidates_per_asset = int(generation_config.get("candidates_per_asset", 1))
    if candidates_per_asset < 1:
        candidates_per_asset = 1
    if candidates_per_asset > 1:
        expanded: list[dict[str, Any]] = []
        for task_item in tasks:
            for slot in range(candidates_per_asset):
                expanded.append(
                    {
                        **task_item,
                        "candidate_slot": slot,
                        "candidates_total": candidates_per_asset,
                    }
                )
        tasks = expanded

    return project, tasks


def _format_lora_suffix(loras: list[LoraSpec] | list[dict[str, Any]]) -> str:
    """LoRA 스펙을 prompt에 붙일 ``<lora:name:weight>`` 토큰들로 직렬화한다."""
    parts: list[str] = []
    for lora in loras:
        if isinstance(lora, LoraSpec):
            name, weight = lora.name, lora.weight
        else:
            name, weight = str(lora["name"]), float(lora.get("weight", 0.7))
        if not name:
            continue
        parts.append(f"<lora:{name}:{weight:g}>")
    return (" " + " ".join(parts)) if parts else ""


def expand_design_batch(spec: DesignBatchRequest) -> list[dict[str, Any]]:
    """batch spec → generation_tasks dict 리스트로 expand 한다.

    곱집합: prompts × models × (loras 또는 [[]]) × seeds
    각 task의 prompt에는 LoRA 토큰이 자동으로 append된다.
    seeds가 비어있으면 ``seeds_per_combo`` 개의 무작위 시드를 생성한다.
    """
    if not spec.prompts:
        raise ValueError("prompts는 최소 1개 필요합니다.")
    models: list[str | None] = list(spec.models) if spec.models else [None]
    lora_combos: list[list[LoraSpec]] = list(spec.loras) if spec.loras else [[]]

    if spec.seeds:
        seeds: list[int | None] = [int(s) for s in spec.seeds]
    elif spec.seeds_per_combo > 0:
        seeds = [random.randint(0, 2**31 - 1) for _ in range(spec.seeds_per_combo)]
    else:
        seeds = [None]

    tasks: list[dict[str, Any]] = []
    for prompt in spec.prompts:
        for model in models:
            for lora_combo in lora_combos:
                lora_suffix = _format_lora_suffix(lora_combo)
                full_prompt = (prompt + lora_suffix).strip()
                lora_spec_serialized = json.dumps(
                    [
                        {"name": item.name, "weight": item.weight}
                        for item in lora_combo
                    ],
                    ensure_ascii=False,
                )
                for seed in seeds:
                    tasks.append(
                        {
                            "project": spec.project,
                            "asset_key": spec.asset_key,
                            "category": spec.category,
                            "prompt": full_prompt,
                            "negative_prompt": spec.common.negative_prompt,
                            "model_name": model,
                            "width": spec.common.width,
                            "height": spec.common.height,
                            "steps": spec.common.steps,
                            "cfg": spec.common.cfg,
                            "sampler": spec.common.sampler,
                            "expected_size": spec.common.expected_size,
                            "max_colors": spec.common.max_colors,
                            "max_retries": spec.common.max_retries,
                            "lora_spec_json": lora_spec_serialized,
                            "seed": seed,
                        }
                    )
    return tasks


def _check_disk_space(path: Path, required_mb: int | None = None) -> None:
    """생성 전 디스크 여유 공간을 검사한다.

    ``required_mb`` 명시되면 그 값을 최소 요구로 사용 (override). 미명시 시
    환경변수 ``MIN_FREE_DISK_MB`` (기본 50MB).
    """
    min_mb = required_mb if required_mb is not None else int(os.getenv("MIN_FREE_DISK_MB", "50"))
    min_free = min_mb * 1024 * 1024
    usage = shutil.disk_usage(path)
    if usage.free < min_free:
        raise RuntimeError(
            f"디스크 여유 공간이 부족합니다. 남은 용량: {usage.free // (1024 * 1024)}MB (최소 {min_mb}MB 필요)"
        )


def _ensure_disk_space_for_enqueue(expected_files: int = 1) -> None:
    """enqueue API에서 즉시 507 응답으로 변환되는 디스크 가드.

    P2.3 — ComfyUI 변형은 한 task 가 N장 출력 (V38 full = 5장) → candidates_total 곱
    하면 디스크 요구가 N배. ``expected_files`` 로 baseline + 파일당 추가 MB 를 더해
    상향. 기본 1 (단일 출력 = 기존 동작 호환).
    """
    base_mb = int(os.getenv("MIN_FREE_DISK_MB", "50"))
    per_file_mb = int(os.getenv("MIN_FREE_DISK_PER_FILE_MB", "5"))
    required_mb = base_mb + per_file_mb * max(0, expected_files - 1)
    try:
        _check_disk_space(DATA_DIR, required_mb=required_mb)
    except RuntimeError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


# 자동 재시도하지 않을 SD 실패 코드(클라이언트 오류, 메모리 부족 등).
NON_RETRYABLE_SD_CODES: frozenset[str] = frozenset({"sd_client_error", "oom"})


def _compute_next_attempt_at(retries: int) -> str:
    """재시도까지 대기할 시각(UTC ISO8601)을 계산한다.

    지수 백오프(2^n)에 25% 지터를 더하고 ``TASK_BACKOFF_MAX_SEC``으로 캡한다.
    ``retries``는 현재까지의 시도 횟수(0-indexed before increment)이다.
    """
    base = float(os.getenv("TASK_BACKOFF_BASE_SEC", "2"))
    cap = float(os.getenv("TASK_BACKOFF_MAX_SEC", "60"))
    delay = min(cap, base * (2 ** max(0, retries)))
    jitter = random.uniform(0.0, delay * 0.25)
    due = datetime.now(timezone.utc) + timedelta(seconds=delay + jitter)
    return due.isoformat()


def _should_retry_sd_failure(exc: BaseException) -> bool:
    """SDError 코드 기반 재시도 여부 판정. 비-SDError는 기본적으로 재시도한다."""
    if isinstance(exc, SDError):
        return exc.code not in NON_RETRYABLE_SD_CODES
    return True


def _format_task_error(exc: BaseException) -> str:
    """generation_tasks.last_error에 저장할 사람이 읽을 메시지."""
    if isinstance(exc, SDError):
        return f"code:{exc.code} {exc}"
    return str(exc) or exc.__class__.__name__


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """변경 API에 대한 최소 인증."""
    if not api_key:
        return
    if x_api_key != api_key:
        raise HTTPException(status_code=401, detail="유효하지 않은 API 키입니다.")


async def generation_worker() -> None:
    """DB 큐를 polling 하며 태스크를 처리한다.

    queued 태스크가 모두 백오프 대기 중이면 가장 이른 due 시각까지(최대 2초)
    잠들었다가 다시 시도하여 불필요한 폴링을 줄인다.
    """
    while True:
        _worker_heartbeat(None)
        task = await db.claim_next_task()
        if task is None:
            wait = await db.soonest_due_seconds(default=1.0)
            await asyncio.sleep(min(2.0, max(0.05, wait)))
            continue
        _worker_heartbeat(task)
        try:
            await handle_task(task)
        finally:
            _worker_tick_done()


async def handle_task(task: dict[str, Any]) -> None:
    """생성 태스크 처리.

    task['backend'] 에 따라 A1111 (단일 이미지) 또는 ComfyUI (N개 이미지) 백엔드로
    디스패치한다. 결과는 GenerationOutcome 으로 통일되어 이하 흐름이 동일하다.
    """
    try:
        _check_disk_space(DATA_DIR)
        backend = backends.get(task.get("backend"))
        outcome: GenerationOutcome = await backend.generate(task)
        candidates_total = int(task.get("candidates_total") or 1)
        candidate_slot = task.get("candidate_slot")
        batch_id = task.get("batch_id")
        # design batch (batch_id != None) 는 항상 cherry-pick 모드로 동작:
        # task.id 를 slot_index 로 써서 모든 시도가 후보로 누적된다.
        # 자동 primary 승격은 하지 않는다 — 사람이 cherry-pick UI에서 고른다.
        if batch_id and candidate_slot is None:
            candidate_slot = int(task["id"])
            candidates_total = max(candidates_total, 2)

        # primary + extras 를 한 번에 저장. A1111 (1개) / ComfyUI (N개) 모두 같은 함수.
        outputs_to_save: list[tuple[str, bytes]] = [
            (o.label, o.image_bytes) for o in outcome.outputs
        ]
        if candidates_total > 1 and candidate_slot is not None:
            saved_paths = save_candidate_slot_outputs(
                outputs=outputs_to_save,
                output_root=DATA_DIR,
                project=task["project"],
                asset_key=task["asset_key"],
                job_id=task["job_id"],
                slot_index=int(candidate_slot),
            )
        else:
            # Unique 경로에 저장 → 재생성 시 이전 파일이 덮어쓰여지지 않아
            # asset_history의 image_path가 디스크에 그대로 유지된다.
            saved_paths = save_generated_outputs(
                outputs=outputs_to_save,
                output_root=DATA_DIR,
                project=task["project"],
                asset_key=task["asset_key"],
                job_id=task["job_id"],
            )
        primary_label = outcome.primary.label
        output_path = saved_paths[primary_label]
        extra_paths = {
            label: str(p)
            for label, p in saved_paths.items()
            if label != primary_label
        }
        validation = validate_asset(
            image_path=output_path,
            expected_size=task.get("expected_size"),
            max_colors=int(task.get("max_colors", 32)),
        )
        metadata_json = json.dumps(
            {
                "image_format": validation.image_format,
                "model": outcome.model,
                "seed": outcome.seed,
                "steps": int(task.get("steps", 20)),
                "cfg": float(task.get("cfg", 7.0)),
                "sampler": task.get("sampler") or "DPM++ 2M",
                "negative_prompt": task.get("negative_prompt"),
                "max_colors": int(task.get("max_colors", 32)),
                "max_retries": int(task.get("max_retries", 3)),
                "expected_size": task.get("expected_size"),
                "backend": outcome.backend,
                # ComfyUI 변형의 경우 stage1/hires/rembg_alpha 등 부가 출력 파일 경로
                "extra_outputs": extra_paths,
                # ComfyUI prompt_id, patch_report — 디버깅/추적용
                "raw": outcome.raw,
            },
            ensure_ascii=False,
        )

        if candidates_total > 1 and candidate_slot is not None:
            candidate_id = await db.insert_asset_candidate(
                project=task["project"],
                asset_key=task["asset_key"],
                slot_index=int(candidate_slot),
                job_id=task["job_id"],
                image_path=str(output_path),
                width=validation.width,
                height=validation.height,
                color_count=validation.color_count,
                validation_status="pass" if validation.passed else "fail",
                validation_message=validation.message,
                generation_seed=outcome.seed,
                generation_model=outcome.model,
                generation_prompt=task["prompt"],
                metadata_json=metadata_json,
                batch_id=batch_id,
                approval_mode=task.get("approval_mode") or "manual",
            )
            if batch_id is not None:
                await event_broker.publish(
                    {
                        "type": ev.EVT_CANDIDATE_ADDED,
                        "batch_id": batch_id,
                        "candidate_id": int(candidate_id),
                        "slot_index": int(candidate_slot),
                        "validation_status": "pass" if validation.passed else "fail",
                    }
                )
            # design batch (batch_id != None) 는 사람이 cherry-pick UI에서 고를 때까지
            # primary로 승격하지 않는다. 기존 spec-batch 흐름(batch_id 없음)은 종전대로
            # 첫 성공 슬롯을 자동 승격한다.
            already_promoted = await db.has_asset(task["project"], task["asset_key"])
            if not already_promoted and batch_id is None:
                asset_id = str(uuid.uuid4())
                await db.finish_task_success(
                    task_id=int(task["id"]),
                    job_id=task["job_id"],
                    asset={
                        "id": asset_id,
                        "job_id": task["job_id"],
                        "project": task["project"],
                        "asset_key": task["asset_key"],
                        "category": task["category"],
                        "status": "pending",
                        "image_path": str(output_path),
                        "width": validation.width,
                        "height": validation.height,
                        "color_count": validation.color_count,
                        "has_alpha": validation.has_alpha,
                        "validation_status": "pass" if validation.passed else "fail",
                        "validation_message": validation.message,
                        "generation_seed": outcome.seed,
                        "generation_model": outcome.model,
                        "generation_prompt": task["prompt"],
                        "metadata_json": metadata_json,
                        "approval_mode": task.get("approval_mode") or "manual",
                    },
                )
            else:
                await db.finish_generation_task_only(int(task["id"]), task["job_id"])
        else:
            asset_id = str(uuid.uuid4())
            await db.finish_task_success(
                task_id=int(task["id"]),
                job_id=task["job_id"],
                asset={
                    "id": asset_id,
                    "job_id": task["job_id"],
                    "project": task["project"],
                    "asset_key": task["asset_key"],
                    "category": task["category"],
                    "status": "pending",
                    "image_path": str(output_path),
                    "width": validation.width,
                    "height": validation.height,
                    "color_count": validation.color_count,
                    "has_alpha": validation.has_alpha,
                    "validation_status": "pass" if validation.passed else "fail",
                    "validation_message": validation.message,
                    "generation_seed": outcome.seed,
                    "generation_model": outcome.model,
                    "generation_prompt": task["prompt"],
                    "metadata_json": metadata_json,
                    "approval_mode": task.get("approval_mode") or "manual",
                },
            )
        await event_broker.publish(
            {
                "type": ev.EVT_TASK_DONE,
                "job_id": task["job_id"],
                "asset_key": task["asset_key"],
                "validation_status": "pass" if validation.passed else "fail",
            }
        )
    except Exception as exc:  # noqa: BLE001
        force_fail = not _should_retry_sd_failure(exc)
        message = _format_task_error(exc)
        next_attempt_at: str | None = None
        if not force_fail:
            next_attempt_at = _compute_next_attempt_at(int(task.get("retries", 0)))
        await db.retry_or_fail_task(
            task,
            message,
            force_fail=force_fail,
            next_attempt_at=next_attempt_at,
        )
        _push_log(
            "error",
            f"task#{task.get('id')} {message}",
            context={
                "task_id": task.get("id"),
                "batch_id": task.get("batch_id"),
                "asset_key": task.get("asset_key"),
                "retry": not force_fail,
            },
        )
        await event_broker.publish(
            {
                "type": ev.EVT_TASK_ERROR,
                "job_id": task["job_id"],
                "asset_key": task["asset_key"],
                "error": message,
                "code": exc.code if isinstance(exc, SDError) else None,
                "retry": not force_fail,
                "next_attempt_at": next_attempt_at,
            }
        )


# 운영/디버깅용 GC 상태 스냅샷. ``/api/system/gc/status``로 노출된다.
_gc_state: dict[str, Any] = {
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
    "run_count": 0,
}


def _record_gc_run(
    result: dict[str, int | float] | None, error: str | None
) -> None:
    """GC 실행 결과/에러를 모듈 상태에 기록한다."""
    _gc_state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _gc_state["last_result"] = result
    _gc_state["last_error"] = error
    _gc_state["run_count"] = int(_gc_state.get("run_count", 0)) + 1


async def _gc_loop() -> None:
    """주기적으로 후보 이미지 GC를 실행한다."""
    interval = int(os.getenv("CANDIDATE_GC_INTERVAL_SEC", "3600"))
    await asyncio.sleep(5)
    while True:
        try:
            result = run_gc_candidates(DATA_DIR)
            _record_gc_run(result, None)
        except Exception as exc:  # noqa: BLE001 — GC 실패는 서버를 죽이지 않음
            _record_gc_run(None, f"{exc.__class__.__name__}: {exc}")
        await asyncio.sleep(max(60, interval))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """앱 수명 주기 관리."""
    global worker_task, gc_worker_task
    await db.init()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if os.getenv("ASSET_FACTORY_MOCK_MODE") == "1":
        print(f"[MOCK MODE] DATA_DIR={DATA_DIR} DB_PATH={DB_PATH} EXPORT_ROOT={EXPORT_ROOT}")
    # 이전 실행 중 'processing' 상태로 멈춘 태스크를 큐로 복귀시킨다.
    await db.recover_orphan_tasks()
    worker_task = asyncio.create_task(generation_worker())
    gc_worker_task = asyncio.create_task(_gc_loop())
    try:
        yield
    finally:
        if gc_worker_task:
            gc_worker_task.cancel()
            try:
                await gc_worker_task
            except asyncio.CancelledError:
                pass
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Asset Factory", version="0.1.0", lifespan=lifespan)


class _CachedStaticFiles(StaticFiles):
    """``StaticFiles`` + short-TTL Cache-Control.

    SPA 번들은 CDN 없이 /static 에서 바로 서빙되므로, 매 탐색마다 jsx 를
    재다운로드하면 체감이 느리다. 그렇다고 immutable 로 두면 hot-reload 가
    망가진다 → 1분 TTL + ``must-revalidate`` 로 타협한다.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault(
                "Cache-Control", "public, max-age=60, must-revalidate"
            )
        return response


app.mount(
    "/static",
    _CachedStaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


@app.get("/")
async def root() -> RedirectResponse:
    """모든 사용자 진입점은 신규 SPA(/app/)로 수렴한다."""
    return RedirectResponse(url="/app/", status_code=302)


# CodeQL: URL redirection from remote source (py/url-redirection).
# ``batch_id`` 는 사용자 입력 쿼리이므로 그대로 redirect URL 에 끼우면
# open redirect 가 된다. batch_id 가 우리 ID 포맷(영문/숫자/-/_ 1–64자)을
#만족할 때만 deep-link 하고, 아니면 안전 기본 경로로 떨어뜨린다.
_SAFE_BATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@app.get("/cherry-pick")
async def cherry_pick_redirect(batch_id: str | None = Query(default=None)) -> RedirectResponse:
    """옛 ``/cherry-pick`` 북마크 호환.

    - ``/cherry-pick?batch_id=<safe_id>`` → ``/app/cherry-pick/<safe_id>``
    - ``/cherry-pick`` · 잘못된 batch_id  → ``/app/queue``

    구 HTML/JS (``static/index.html`` · ``static/cherry-pick.html`` · ``static/app.js`` ·
    ``static/style.css``) 는 2026-04-20 삭제. 회귀 시 git 히스토리에서 복원.
    """
    if batch_id and _SAFE_BATCH_ID.fullmatch(batch_id):
        safe_batch_id = quote(batch_id, safe="")
        return RedirectResponse(url=f"/app/cherry-pick/{safe_batch_id}", status_code=302)
    return RedirectResponse(url="/app/queue", status_code=302)


@app.get("/app")
@app.get("/app/")
async def app_redesign_root() -> FileResponse:
    """신규 SPA 엔트리 (React + Babel CDN)."""
    return FileResponse(BASE_DIR / "static" / "app" / "index.html")


@app.get("/app/{path:path}")
async def app_redesign_catchall(path: str) -> FileResponse:
    """Client-side router 의 deep-link (예: /app/cherry-pick/btc_xxx) 를
    같은 SPA 셸로 서빙. ``path`` 는 라우터가 ``window.location.pathname`` 에서
    스스로 파싱하므로 백엔드는 무시한다."""
    del path  # 라우팅은 클라이언트가 담당
    return FileResponse(BASE_DIR / "static" / "app" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """기본 헬스체크 + 운영 설정값 노출.

    ``bypass_retention_days`` 는 approval_mode='bypass' 후보의 자동 청소 기준
    (env ``AF_BYPASS_RETENTION_DAYS``, 기본 7).
    """
    from candidate_gc import get_bypass_retention_days
    return {
        "ok": True,
        "service": "asset-factory",
        "bypass_retention_days": get_bypass_retention_days(),
    }


@app.get("/api/health/sd")
async def health_sd() -> dict[str, Any]:
    """SD 서버 연결 헬스체크.

    A1111 + ComfyUI 양 백엔드를 동시에 점검한다. 한쪽만 살아있어도 200 으로
    응답하고, 두 쪽 모두 죽었을 때만 503. 응답 본문에 backend 별 ok/에러를
    모두 담아 운영자가 어느 쪽이 죽었는지 식별 가능하게 한다.

    각 백엔드 health_check 는 ``SD_HEALTH_TIMEOUT_SECONDS`` (기본 5초) 안에
    응답하지 않으면 timeout 으로 처리한다. /system, /catalog 화면이 죽은 SD 를
    기다리며 무한 로딩되는 것을 방지하기 위함 (기본 client timeout 45s × 3 retry
    = 141s 가 단일 요청에 걸리면 다른 동시 요청도 사실상 멈춘 듯 보임).
    """
    results: dict[str, Any] = {}
    any_ok = False
    for name in backends.names:
        try:
            async with asyncio.timeout(_SD_HEALTH_TIMEOUT_SECONDS):
                results[name] = await backends.get(name).health_check()
            any_ok = True
        except asyncio.TimeoutError:
            results[name] = {
                "ok": False,
                "error": f"timeout: backend did not respond within {_SD_HEALTH_TIMEOUT_SECONDS}s",
            }
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    if not any_ok:
        raise HTTPException(status_code=503, detail={"sd_backends": results})
    # PLAN_comfyui_catalog.md Task 4 — A1111 deprecated, ComfyUI primary.
    # 기존 backends 구조는 호환성 위해 유지. 프론트는 primary 만 보면 충분.
    return {
        "backends": results,
        "primary": "comfyui",
        "deprecated_backends": ["a1111"],
    }


@app.get("/api/comfyui/health")
async def comfyui_health() -> dict[str, Any]:
    """ComfyUI 백엔드 단독 헬스 + 큐 상태.

    `/api/health/sd` 가 a1111+comfyui 합본인 반면, 본 endpoint 는 ComfyUI 만 본다.
    A1111 deprecated 후 Catalog/System 화면이 1차 데이터 소스로 사용한다.

    실패해도 항상 200 + ``ok=false`` — 프론트가 status code 분기를 안 타고 단일
    응답으로 판단하도록 한다 (SD health 가 200/503 다 내서 배너 로직이 복잡해진
    선례를 답습하지 않음).

    ``COMFYUI_HEALTH_TIMEOUT_SECONDS`` (기본 5초) 안에 응답이 없으면 timeout 으로
    마감한다 — system_stats + queue 두 호출을 합쳐도 수백 ms 안 걸리는 게 정상.
    """
    payload: dict[str, Any] = {
        "ok": False,
        "host": comfyui_client.base_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with asyncio.timeout(_COMFYUI_HEALTH_TIMEOUT_SECONDS):
            stats = await comfyui_client.health_check()
            queue = await comfyui_client.queue_state()
    except asyncio.TimeoutError:
        payload["error"] = (
            f"timeout: ComfyUI did not respond within "
            f"{_COMFYUI_HEALTH_TIMEOUT_SECONDS}s"
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        payload["error"] = f"{exc.__class__.__name__}: {exc}"
        return payload

    if not isinstance(stats, dict):
        payload["error"] = f"unexpected /system_stats response type: {type(stats).__name__}"
        return payload
    if not isinstance(queue, dict):
        payload["error"] = f"unexpected /queue response type: {type(queue).__name__}"
        return payload

    payload["ok"] = True
    payload["comfyui_version"] = stats.get("comfyui_version")
    payload["python_version"] = stats.get("python_version")
    payload["device_count"] = stats.get("device_count")
    payload["device_names"] = stats.get("device_names", [])
    payload["queue"] = {
        "running": len(queue.get("queue_running", []) or []),
        "pending": len(queue.get("queue_pending", []) or []),
    }
    payload["workflows_available"] = len(workflow_registry.available_variants())
    return payload


# ── /api/comfyui/catalog ───────────────────────────────────────────────────
# PLAN_comfyui_catalog.md §3.1.2.
#
# /object_info (≈1.94MB) + WorkflowRegistry cross-ref → Catalog 화면 데이터.
# 60s in-memory 캐시 (asyncio lock) — ComfyUI 재시작이 잦지 않은 환경에서 충분.
# 실패해도 항상 200 — `ok=false` + `error` 또는 `stale=true` + 마지막 성공 페이로드.

from lib import comfyui_catalog as _catalog_lib  # noqa: E402

_comfyui_catalog_cache: dict[str, Any] = {
    "payload": None,        # 마지막 성공 페이로드 (stale fallback 용)
    "fetched_at": None,     # 캐시 시각 (epoch seconds)
    "ttl_seconds": float(os.getenv("COMFYUI_CATALOG_TTL_SECONDS", "60")),
}
_comfyui_catalog_lock = asyncio.Lock()


def _comfyui_catalog_cache_clear() -> None:
    """테스트/관리용 캐시 클리어."""
    _comfyui_catalog_cache["payload"] = None
    _comfyui_catalog_cache["fetched_at"] = None


@app.get("/api/comfyui/catalog")
async def comfyui_catalog_endpoint() -> dict[str, Any]:
    """ComfyUI 기반 모델 / LoRA / VAE / ControlNet / Workflow 카탈로그.

    응답 schema (PLAN §3.1.2):
        {
          fetched_at: ISO8601,
          stale: bool,
          checkpoints: [{name, family, used_by_workflows}],
          loras: [{name, used_by_workflows}],
          vaes: [...], controlnets: [...], upscalers: [...],
          workflows: [{id, category, label, variants, uses_models, uses_loras}]
        }

    실패 시 (캐시 없음): ``{ok: false, error, host, fetched_at}``.
    실패 시 (캐시 있음): 마지막 성공 페이로드 + ``stale=true``.

    ``COMFYUI_CATALOG_TIMEOUT_SECONDS`` (기본 10초) 안에 ``/object_info`` 가
    응답해야 한다 — 1.94MB / 200ms 가 정상이지만 LAN 지연을 감안.
    """
    import time as _time

    now = _time.time()
    ttl = float(_comfyui_catalog_cache["ttl_seconds"])
    cached_at = _comfyui_catalog_cache["fetched_at"]
    cached_payload = _comfyui_catalog_cache["payload"]

    # 캐시 hit 판단 (lock 밖에서 빠르게)
    if cached_payload is not None and cached_at is not None and (now - cached_at) < ttl:
        return cached_payload

    async with _comfyui_catalog_lock:
        # double-check (다른 코루틴이 채웠을 수 있음)
        cached_at = _comfyui_catalog_cache["fetched_at"]
        cached_payload = _comfyui_catalog_cache["payload"]
        if cached_payload is not None and cached_at is not None and (now - cached_at) < ttl:
            return cached_payload

        fetched_at_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with asyncio.timeout(_COMFYUI_CATALOG_TIMEOUT_SECONDS):
                object_info = await comfyui_client.object_info()
        except asyncio.TimeoutError:
            error = (
                f"timeout: ComfyUI did not respond within "
                f"{_COMFYUI_CATALOG_TIMEOUT_SECONDS}s"
            )
            if cached_payload is not None:
                stale_payload = dict(cached_payload)
                stale_payload["stale"] = True
                stale_payload["error"] = error
                return stale_payload
            return {
                "ok": False,
                "error": error,
                "host": comfyui_client.base_url,
                "fetched_at": fetched_at_iso,
            }
        except Exception as exc:  # noqa: BLE001
            print(
                "[comfyui_catalog] unexpected error while fetching object info: "
                f"{exc.__class__.__name__}: {exc}"
            )
            error = "internal error while fetching ComfyUI catalog"
            if cached_payload is not None:
                stale_payload = dict(cached_payload)
                stale_payload["stale"] = True
                stale_payload["error"] = error
                return stale_payload
            return {
                "ok": False,
                "error": error,
                "host": comfyui_client.base_url,
                "fetched_at": fetched_at_iso,
            }

        payload = _catalog_lib.build_full_payload(
            object_info=object_info,
            registry=workflow_registry,
            fetched_at=fetched_at_iso,
            stale=False,
        )
        _comfyui_catalog_cache["payload"] = payload
        _comfyui_catalog_cache["fetched_at"] = _time.time()
        return payload


# ── /api/comfyui/queue ─────────────────────────────────────────────────────
# PLAN §3.1.3 — System 화면 부가 정보. 5초 timeout, 항상 200.


def _normalize_queue_running(raw: Any) -> list[dict[str, Any]]:
    """ComfyUI ``/queue`` 의 ``queue_running`` 을 정규화.

    ComfyUI 응답 구조: ``[number, prompt_id, prompt_dict, extra_data, outputs]``.
    프론트가 dict 로 다루기 쉽게 ``{prompt_id, number}`` 형태로 변환.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, list) and len(entry) >= 2:
            out.append({"number": entry[0], "prompt_id": str(entry[1])})
        elif isinstance(entry, dict) and "prompt_id" in entry:
            out.append({"prompt_id": str(entry["prompt_id"]), "number": entry.get("number")})
    return out


@app.get("/api/comfyui/queue")
async def comfyui_queue_endpoint() -> dict[str, Any]:
    """ComfyUI 큐 상태 — running list + pending count.

    응답 schema:
        {ok: bool, running: [{prompt_id, number}, ...], pending: int, fetched_at, host, error?}

    실패해도 항상 200 (PLAN §3.1.1 정책 준수).
    """
    payload: dict[str, Any] = {
        "ok": False,
        "running": [],
        "pending": 0,
        "host": comfyui_client.base_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with asyncio.timeout(_COMFYUI_QUEUE_TIMEOUT_SECONDS):
            queue = await comfyui_client.queue_state()
    except asyncio.TimeoutError:
        payload["error"] = (
            f"timeout: ComfyUI queue did not respond within "
            f"{_COMFYUI_QUEUE_TIMEOUT_SECONDS}s"
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        payload["error"] = f"{exc.__class__.__name__}: {exc}"
        return payload

    if not isinstance(queue, dict):
        payload["error"] = f"unexpected /queue response type: {type(queue).__name__}"
        return payload

    payload["ok"] = True
    payload["running"] = _normalize_queue_running(queue.get("queue_running"))
    pending = queue.get("queue_pending") or []
    payload["pending"] = len(pending) if isinstance(pending, list) else 0
    return payload


@app.get("/api/system/gc/status")
async def gc_status() -> dict[str, Any]:
    """후보 이미지 GC의 마지막 실행 메트릭/시각을 노출한다.

    - ``last_run_at``: ISO8601 UTC, GC가 한 번도 돌지 않았으면 ``null``.
    - ``last_result``: ``run_gc_candidates``가 반환한 dict (deleted_files,
      freed_bytes, scanned_files) 또는 ``null``.
    - ``last_error``: 마지막 GC 실행이 예외로 끝났을 때의 메시지 (없으면 ``null``).
    - ``run_count``: 누적 실행 횟수(성공/실패 모두 포함).
    """
    return dict(_gc_state)


@app.post("/api/system/gc/run", dependencies=[Depends(require_api_key)])
async def gc_run() -> dict[str, Any]:
    """후보 이미지 GC를 즉시 1회 실행하고 결과를 반환한다 (운영 디버깅용)."""
    try:
        result = run_gc_candidates(DATA_DIR)
        _record_gc_run(result, None)
        return {"status": "ok", "result": result, "state": dict(_gc_state)}
    except Exception as exc:  # noqa: BLE001
        message = f"{exc.__class__.__name__}: {exc}"
        _record_gc_run(None, message)
        raise HTTPException(status_code=500, detail=message) from exc


@app.get("/api/system/db")
async def system_db() -> dict[str, Any]:
    """System.jsx DB 블록 소스.

    응답: ``path`` (sqlite 절대경로), ``size_bytes`` (파일 존재 시),
    ``tables`` (jobs/generation_tasks/asset_candidates/assets row count) +
    큐 관련 집계.
    """
    stats = await db.system_stats()
    try:
        size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    except OSError:
        size = 0
    return {
        "path": str(DB_PATH),
        "exists": DB_PATH.exists(),
        "size_bytes": int(size),
        "tables": {
            "jobs": stats.get("jobs", 0),
            "generation_tasks": stats.get("generation_tasks", 0),
            "asset_candidates": stats.get("asset_candidates", 0),
            "assets": stats.get("assets", 0),
        },
        "queue": {
            "queued_total": stats.get("queued_total", 0),
            "queued_due": stats.get("queued_due", 0),
            "processing": stats.get("processing", 0),
            "failed": stats.get("failed", 0),
        },
    }


@app.get("/api/system/worker")
async def system_worker() -> dict[str, Any]:
    """System.jsx Worker 블록 소스.

    - ``alive``: worker_task 가 살아있는지 (lifespan 이 걸어둠).
    - ``last_heartbeat_at``: generation_worker 루프의 최신 tick 시각.
    - ``current_task``: 현재 claim 된 태스크 요약 (없으면 null).
    - ``processed_count``: 프로세스 시작 이후 완료한 태스크 수.
    - ``queue_depth``: ``/api/system/db`` 의 queued_total 과 동일.
    """
    stats = await db.system_stats()
    return {
        "alive": worker_task is not None and not worker_task.done(),
        "last_heartbeat_at": _worker_state.get("last_heartbeat_at"),
        "current_task": _worker_state.get("current_task"),
        "last_task_id": _worker_state.get("last_task_id"),
        "processed_count": int(_worker_state.get("processed_count", 0)),
        "queue_depth": int(stats.get("queued_total", 0)),
        "queue_due": int(stats.get("queued_due", 0)),
        "processing": int(stats.get("processing", 0)),
        "failed": int(stats.get("failed", 0)),
    }


@app.get("/api/system/logs/recent")
async def system_logs_recent(
    level: str | None = Query(
        default=None,
        description="'error' 이면 error 레벨만, 미지정이면 전부",
    ),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """System.jsx Logs 블록 소스.

    프로세스 시작 후 발생한 에러/경고 기록을 ring buffer 에 쌓아 뒤에서부터 ``limit`` 개를 돌려준다.
    파일 로깅과 별개로, 운영 중 관측만 목적으로 한다.
    """
    allowed = ("error", "warn", "info") if level is None else (level,)
    items = [e for e in _log_ring if e["level"] in allowed]
    return {"count": len(items[-limit:]), "items": list(reversed(items[-limit:]))}


# ── A1111 catalog endpoints — deprecated (Task 8) ──────────────────────────
# 다음 메이저에서 410 Gone 으로 전환. 그 전까지: 응답 헤더 + 본문에 마커, 로그 1회.

import logging as _logging  # noqa: E402

_a1111_deprecation_logger = _logging.getLogger("asset_factory.a1111_deprecated")
_a1111_deprecation_warned = {"flag": False}


def _a1111_deprecation_sunset_date() -> str:
    """현재 시각 + 90일 (RFC 8594 권장 형식 — 단순 RFC 1123 HTTP-date)."""
    from email.utils import format_datetime
    return format_datetime(datetime.now(timezone.utc) + timedelta(days=90))


def _mark_a1111_deprecated(response: Response, endpoint_path: str) -> dict[str, str]:
    """응답 헤더 (Deprecation/Sunset/Link) 추가 + 모듈 레벨 1회 로그.

    헤더 dict 를 반환 — HTTPException 에도 동일 헤더를 실어 503 등 오류 응답에서도
    deprecation 메타데이터가 클라이언트에 전달되도록 한다.
    """
    headers = {
        "Deprecation": "true",
        "Sunset": _a1111_deprecation_sunset_date(),
        "Link": '</api/comfyui/catalog>; rel="successor-version"',
    }
    response.headers.update(headers)
    if not _a1111_deprecation_warned["flag"]:
        _a1111_deprecation_logger.warning(
            "A1111 catalog endpoint accessed — deprecated, will be removed in next major. "
            "Path=%s · Use /api/comfyui/catalog instead.",
            endpoint_path,
        )
        _a1111_deprecation_warned["flag"] = True
    return headers


@app.get("/api/sd/catalog/models")
async def sd_catalog_models(response: Response) -> dict[str, Any]:
    """A1111 모델 목록 + ``config/sd_catalog.yml`` 메타데이터 병합 반환.

    .. deprecated::
        ComfyUI primary 전환 후 deprecated. ``/api/comfyui/catalog`` 사용 권장.
        다음 메이저(v0.4.0)에서 410 Gone 전환 예정. PLAN_comfyui_catalog.md §4 Task 8.

    SD 서버 미연결 시 503, YAML 누락 시 메타데이터 비어있는 채로 200을 반환한다.
    ``SD_CATALOG_TIMEOUT_SECONDS`` (기본 5초) 안에 응답하지 않으면 timeout 으로
    503 — sd_client 자체 retries(45s×3=141s) 를 기다리지 않아 화면이 빠르게 실패.
    """
    dep_headers = _mark_a1111_deprecated(response, "/api/sd/catalog/models")
    try:
        async with asyncio.timeout(_SD_CATALOG_TIMEOUT_SECONDS):
            sd_models = await sd_client.list_models()
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"SD 모델 목록 조회 timeout ({_SD_CATALOG_TIMEOUT_SECONDS}s)",
            headers=dep_headers,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"SD 모델 목록 조회 실패: {exc}",
            headers=dep_headers,
        ) from exc
    catalog = load_catalog_yaml(CATALOG_YAML_PATH)
    merged = merge_models(sd_models, catalog)
    return {
        "count": len(merged),
        "items": merged,
        "catalog_path": str(CATALOG_YAML_PATH),
        "catalog_present": CATALOG_YAML_PATH.exists(),
        "deprecated": True,
    }


@app.get("/api/sd/catalog/loras")
async def sd_catalog_loras(response: Response) -> dict[str, Any]:
    """A1111 LoRA 목록 + ``config/sd_catalog.yml`` 메타데이터 병합 반환.

    .. deprecated::
        ``/api/comfyui/catalog`` 사용 권장. 다음 메이저에서 제거.

    SD 서버 미연결/timeout 시 503 (``SD_CATALOG_TIMEOUT_SECONDS`` 기준).
    """
    dep_headers = _mark_a1111_deprecated(response, "/api/sd/catalog/loras")
    try:
        async with asyncio.timeout(_SD_CATALOG_TIMEOUT_SECONDS):
            sd_loras = await sd_client.list_loras()
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"SD LoRA 목록 조회 timeout ({_SD_CATALOG_TIMEOUT_SECONDS}s)",
            headers=dep_headers,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"SD LoRA 목록 조회 실패: {exc}",
            headers=dep_headers,
        ) from exc
    catalog = load_catalog_yaml(CATALOG_YAML_PATH)
    merged = merge_loras(sd_loras, catalog)
    return {
        "count": len(merged),
        "items": merged,
        "catalog_path": str(CATALOG_YAML_PATH),
        "catalog_present": CATALOG_YAML_PATH.exists(),
        "deprecated": True,
    }


@app.get("/api/workflows/catalog")
async def workflows_catalog() -> dict[str, Any]:
    """ComfyUI 워크플로우 레지스트리 카탈로그.

    `workflows/registry.yml` 의 카테고리·변형·출력·기본값을 그대로 노출.
    클라이언트가 변형 선택 UI 를 그릴 때 사용한다. SD 서버 미연결과 무관하게
    항상 200 (레지스트리는 로컬 파일이므로).
    """
    return workflow_registry.to_catalog()


# ── §1.C 변형 의도 기반 인덱스 / 자연어 검색 ─────────────────────────────────
# /recommend 는 자연어 query → 룰 점수 ranking, /search 는 tag 정확 매칭 필터.
# 두 endpoint 모두 catalog 의 메타 (intent / use_cases / tags / not_for) 를 인덱싱.


class WorkflowRecommendRequest(BaseModel):
    """spec §1.C.3 — 자연어 query 기반 변형 추천."""

    query: str = Field(..., examples=["RPG 캐릭터 정면/측면/뒷면 픽셀 스프라이트"])
    top: int = Field(default=3, ge=1, le=50)
    include_unavailable: bool = Field(
        default=False,
        description="status=needs_api_conversion 변형도 후보에 포함할지.",
    )


@app.post("/api/workflows/recommend")
async def workflows_recommend(request: WorkflowRecommendRequest) -> dict[str, Any]:
    """자연어 query → top-N 변형 후보 (룰 기반 weighted score).

    스코어링 (spec §C.4):
    - intent 매칭 +0.4
    - use_cases 매칭마다 +0.15 (max +0.45)
    - tags 매칭마다 +0.1 (max +0.3)
    - not_for 매칭마다 -0.5 (페널티)
    - 0.0–1.0 클램프

    응답의 `not_for_warnings` 가 비어있지 않으면 클라이언트가 사용자에게 경고
    표시 권장 (점수 높아도 오선택 가능성).

    Phase 2 (변형 30+) 임베딩 매칭은 별도 PR — 이 응답의 ``scoring_method``
    필드로 식별 (현재 ``"rule"``).
    """
    candidates = recommend_variants(
        workflow_registry,
        query=request.query,
        top=request.top,
        include_unavailable=request.include_unavailable,
    )
    return {
        "query": request.query,
        "candidates": [c.to_dict() for c in candidates],
        "scoring_method": "rule",
    }


@app.get("/api/workflows/search")
async def workflows_search(
    tag: list[str] = Query(default_factory=list, description="포함해야 할 태그 (반복 가능, AND)"),  # noqa: B008
    not_: list[str] = Query(  # noqa: B008
        default_factory=list,
        alias="not",
        description="제외할 태그 (반복 가능, AND-NOT)",
    ),
    include_unavailable: bool = Query(default=False),
) -> dict[str, Any]:
    """tag 정확 매칭 필터.

    예: ``/api/workflows/search?tag=pose-sheet&tag=transparent-bg&not=scenery``
    → ``pose-sheet`` AND ``transparent-bg`` 둘 다 포함하고 ``scenery`` 미포함
    하는 변형 매칭.

    `recommend` 가 자연어 + 점수 매칭이라면, `search` 는 사용자가 정확한 tag
    이름을 알고 있을 때의 빠른 색인 — 점수 없음.
    """
    matches = search_variants(
        workflow_registry,
        must_tags=tag,
        must_not_tags=not_,
        include_unavailable=include_unavailable,
    )
    return {
        "filters": {"tag": list(tag), "not": list(not_)},
        "matches": [m.to_dict() for m in matches],
    }


# ── ComfyUI 동적 입력 업로드 ──────────────────────────────────────────────
# LoadImage 노드가 참조할 임의 이미지를 ComfyUI ``input/<subfolder>/`` 에 올린다.
# 이후 /api/workflows/generate 의 ``workflow_params.load_images`` 에 응답의 ``name`` 을
# 박아 사용. 헬퍼 (_decode_and_reencode_image / _validate_comfy_upload_response)
# 와 상수 (MAX_INPUT_BYTES 등) 는 모듈 상단의 _safe_subfolder 영역에 응집.
# 자세한 흐름은 [workflows/README.md](../workflows/README.md) 참조.


@app.post("/api/workflows/inputs", dependencies=[Depends(require_api_key)])
async def upload_workflow_input(
    file: UploadFile,
    subfolder: str = Form(default=""),
) -> dict[str, str]:
    """multipart 로 받은 이미지를 ComfyUI ``input/<subfolder>/`` 에 업로드.

    응답의 ``name`` 을 후속 ``POST /api/workflows/generate`` 의
    ``workflow_params.load_images.<label>`` 에 사용한다.

    방어:
    - ``content-type`` whitelist (PNG/JPEG/WEBP) — 1차 cheap 거부
    - ``MAX_INPUT_BYTES`` 상한 (env-var ``ASSET_FACTORY_MAX_INPUT_BYTES`` override) — 413
    - ``_decode_and_reencode_image`` — polyglot trailing strip + 메타 정화 +
      DecompressionBomb 캐치 (자세한 동작은 helper docstring)
    - ``_safe_subfolder`` / ``_safe_input_filename`` 으로 path traversal /
      비-whitelist 문자 정규화

    참고: ``subfolder`` 가 비었거나 위반이면 ``asset-factory`` 로 정규화.
    ComfyUI 로 forward 되는 bytes 는 재인코딩된 *정화본* 이라 원본 sha256 과는
    다를 수 있다.
    """
    if file.content_type not in _ALLOWED_INPUT_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"지원 안 되는 content-type: {file.content_type}",
        )
    bytes_ = await file.read()
    if len(bytes_) == 0:
        raise HTTPException(status_code=400, detail="빈 파일")
    if len(bytes_) > MAX_INPUT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"이미지 너무 큼: {len(bytes_)} > {MAX_INPUT_BYTES}",
        )

    sanitized_bytes, _format = _decode_and_reencode_image(bytes_)

    try:
        result = await comfyui_client.upload_input_image(
            image_bytes=sanitized_bytes,
            filename=_safe_input_filename(file.filename, sanitized_bytes),
            subfolder=_safe_subfolder(subfolder),
        )
    except SDError as exc:
        # ComfyUI 도달 실패 / 서버 에러 — 외부 의존성이라 5xx 으로 매핑
        status = 502 if exc.code in {"unreachable", "timeout"} else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _validate_comfy_upload_response(result)


class WorkflowInputFromAssetRequest(BaseModel):
    """기존 asset 의 image_path 를 ComfyUI input/ 으로 복사 업로드."""

    asset_id: str = Field(..., examples=["asset-uuid-123"])
    subfolder: str = Field(default="")


@app.post("/api/workflows/inputs/from-asset", dependencies=[Depends(require_api_key)])
async def upload_workflow_input_from_asset(
    request: WorkflowInputFromAssetRequest,
) -> dict[str, str]:
    """이전에 생성된 asset 의 이미지를 ComfyUI ``input/`` 에 다시 업로드.

    PoseExtract 결과 → 다른 워크플로우의 ControlNet 입력 등 task chain 시나리오.
    1차에선 chain 자동화 안 함 — 사용자가 명시적으로 asset_id 전달.

    방어:
    - ``_ensure_path_allowed`` 로 ``image_path`` traversal 방어 (allowlist 외부면 403)
    - ``_decode_and_reencode_image`` — ``upsert_scanned_asset`` 로 임의 사용자
      디렉토리 스캔 결과가 ``image_path`` 로 등록될 수 있어 자체 생성 이미지라는
      가정이 약함. 동일 정화 패스 거쳐 polyglot/메타/픽셀폭탄 차단
    - ``_safe_subfolder`` / ``_safe_input_filename`` 재사용

    TOCTOU 메모: ``_ensure_path_allowed`` resolve 와 ``read_bytes`` 사이에
    symlink swap 가능성. 단일 사용자 데스크톱 / LAN 배포 가정이라 실 위험 0 —
    multi-tenant 로 전환 시 fd 기반 atomic open 으로 강화 필요.
    """
    asset = await db.get_asset(request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail=f"asset not found: {request.asset_id}")

    image_path = asset.get("image_path")
    if not image_path:
        raise HTTPException(
            status_code=500, detail=f"asset {request.asset_id} 의 image_path 가 비었음"
        )

    resolved = _ensure_path_allowed(Path(image_path))
    try:
        bytes_ = resolved.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"asset 파일 없음 (image_path={image_path})",
        ) from exc
    except IsADirectoryError as exc:
        # 디렉토리가 image_path 로 잘못 등록된 경우 — DB 손상 추정
        raise HTTPException(
            status_code=500,
            detail=f"image_path 가 디렉토리: {image_path}",
        ) from exc
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"파일 읽기 실패: {exc}") from exc

    sanitized_bytes, _format = _decode_and_reencode_image(bytes_)

    try:
        result = await comfyui_client.upload_input_image(
            image_bytes=sanitized_bytes,
            filename=_safe_input_filename(resolved.name, sanitized_bytes),
            subfolder=_safe_subfolder(request.subfolder),
        )
    except SDError as exc:
        status = 502 if exc.code in {"unreachable", "timeout"} else 500
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return _validate_comfy_upload_response(result)


@app.post("/api/workflows/generate", dependencies=[Depends(require_api_key)])
async def workflows_generate(request: WorkflowGenerateRequest) -> dict[str, Any]:
    """ComfyUI 백엔드로 워크플로우 변형 1회 또는 N회 (cherry-pick) 호출.

    body 가 가리키는 변형이 ``status=needs_api_conversion`` 또는 미존재이면 4xx.
    그 외는 task 를 큐에 넣고 ``job_id`` 반환 — 진행은 ``GET /api/jobs/{id}`` 로
    polling, 완료된 candidate 는 cherry-pick UI (``/cherry-pick?batch=...``) 또는
    기존 ``/api/assets`` 흐름으로 본다.
    """
    try:
        variant = workflow_registry.variant(
            request.workflow_category, request.workflow_variant
        )
    except WorkflowRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not variant.available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"variant {request.workflow_category}/{request.workflow_variant} 는 "
                f"호출 불가 (status={variant.status})"
            ),
        )

    # §1.B prompt 합성. legacy 모드면 그대로 통과, subject 모드면 base_positive +
    # injection_rule 합성. Validation 위반 시 PromptResolutionError → HTTP 400.
    try:
        resolution = resolve_prompt(
            variant,
            subject=request.subject,
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            prompt_mode=request.prompt_mode,
            style_extra=request.style_extra,
        )
    except PromptResolutionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    # P2.3 — 변형의 출력 수 × candidates_total 만큼 디스크 가드 상향
    outputs_per_task = max(1, len(variant.outputs))
    _ensure_disk_space_for_enqueue(
        expected_files=outputs_per_task * max(1, int(request.candidates_total))
    )

    job_id = str(uuid.uuid4())
    job_type = "workflow_single" if request.candidates_total == 1 else "workflow_design"
    await db.create_job(job_id=job_id, job_type=job_type, payload=request.model_dump())

    workflow_params_json = (
        json.dumps(request.workflow_params, ensure_ascii=False)
        if request.workflow_params
        else None
    )
    prompt_resolution_json = json.dumps(resolution.to_dict(), ensure_ascii=False)
    candidates_total = int(request.candidates_total)
    base_seed = request.seed

    # generation_tasks 의 steps/cfg/sampler 컬럼은 NOT NULL — variant 기본값으로 채움.
    steps_value = int(
        request.steps if request.steps is not None
        else variant.defaults.get("steps", 20)
    )
    cfg_value = float(
        request.cfg if request.cfg is not None
        else variant.defaults.get("cfg", 7.0)
    )
    sampler_value = str(
        request.sampler if request.sampler is not None
        else variant.defaults.get("sampler", "DPM++ 2M")
    )

    for slot_index in range(candidates_total):
        slot_seed = (base_seed + slot_index) if base_seed is not None else None
        await db.enqueue_generation_task(
            {
                "job_id": job_id,
                "project": request.project,
                "asset_key": request.asset_key,
                "category": request.category,
                # §1.B: ComfyUI 로 디스패치되는 실제 final_positive/final_negative.
                # legacy 모드면 request.prompt 그대로 (resolution.final_positive 와 동일).
                "prompt": resolution.final_positive,
                "negative_prompt": resolution.final_negative or None,
                "model_name": None,
                "width": None,
                "height": None,
                "steps": steps_value,
                "cfg": cfg_value,
                "sampler": sampler_value,
                "expected_size": request.expected_size,
                "max_colors": request.max_colors,
                "max_retries": request.max_retries,
                "candidate_slot": slot_index if candidates_total > 1 else None,
                "candidates_total": candidates_total,
                "seed": slot_seed,
                "backend": "comfyui",
                "workflow_category": request.workflow_category,
                "workflow_variant": request.workflow_variant,
                "workflow_params_json": workflow_params_json,
                "approval_mode": request.approval_mode,
                "prompt_resolution_json": prompt_resolution_json,
            }
        )
    await db.mark_job_running(job_id)
    await event_broker.publish({"type": ev.EVT_JOB_CREATED, "job_id": job_id})
    return {
        "job_id": job_id,
        "workflow_category": request.workflow_category,
        "workflow_variant": request.workflow_variant,
        "candidates_total": candidates_total,
        "primary_output": (
            variant.primary_output.label if variant.primary_output else None
        ),
        "approval_mode": request.approval_mode,
        "prompt_resolution": resolution.to_dict(),
    }


@app.get("/api/sd/catalog/usage")
async def sd_catalog_usage() -> dict[str, Any]:
    """Catalog 카드에 노출할 model/LoRA 사용 역참조.

    Frontend 는 이 엔드포인트 결과를 ``/api/sd/catalog/models`` · ``/loras`` 와
    join 하여 "N batches · 마지막 사용 X분 전" 형태로 표시한다.
    """
    usage = await db.aggregate_catalog_usage()
    return usage


@app.get("/api/sd/catalog/usage/batches")
async def sd_catalog_usage_batches(
    model: str | None = Query(default=None),
    lora: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """특정 model 혹은 LoRA 를 사용한 최근 batch 목록.

    Catalog 상세 패널의 "최근 배치" 리스트 소스. ``model`` 과 ``lora`` 중
    하나만 보내도 되고, 둘 다 보내면 AND 로 좁혀진다.
    """
    items = await db.list_batches_using_catalog(
        model_name=model, lora_name=lora, limit=limit
    )
    return {"count": len(items), "items": items}


@app.get("/api/projects")
async def list_projects() -> dict[str, Any]:
    """specs 디렉토리의 프로젝트 스펙 목록.

    v0.2 스펙 §4 의 list-endpoint 규약에 맞춰 ``{"items": [...]}`` 래퍼로
    반환한다. 각 항목은 ``{id, name, path}``. ``name`` 은 spec.json 내부의
    ``name`` / ``project`` 필드 → 파일명(stem) 순으로 폴백한다.
    """
    specs_dir = BASE_DIR / "specs"
    items: list[dict[str, str]] = []
    if specs_dir.exists():
        for file_path in sorted(specs_dir.glob("*.json")):
            display = file_path.stem
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                display = str(data.get("name") or data.get("project") or file_path.stem)
            except (json.JSONDecodeError, OSError):
                pass
            items.append(
                {
                    "id": file_path.stem,
                    "name": display,
                    "path": str(file_path.relative_to(BASE_DIR)),
                }
            )
    return {"items": items}


@app.get("/api/projects/{project_id}/spec")
async def get_project_spec(project_id: str) -> dict[str, Any]:
    """프로젝트 스펙 파일을 반환한다."""
    spec_path = BASE_DIR / "specs" / f"{project_id}.json"
    if not spec_path.exists():
        raise HTTPException(status_code=404, detail="프로젝트 스펙을 찾을 수 없습니다.")
    return json.loads(spec_path.read_text(encoding="utf-8"))


@app.get("/api/projects/{project_id}/assets")
async def list_project_assets(
    project_id: str,
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    validation_status: str | None = Query(default=None),
    include_bypassed: bool = Query(default=False),
) -> list[dict[str, Any]]:
    """프로젝트별 에셋 목록 (스펙/클라이언트용 정식 엔드포인트).

    ``include_bypassed=true`` 일 때만 bypass 모드 자산이 포함된다.
    기본은 일반 검수 큐에 노출되지 않게 제외.
    """
    return await db.list_assets(
        project=project_id,
        status=status,
        category=category,
        validation_status=validation_status,
        include_bypassed=include_bypassed,
    )


@app.post("/api/generate", dependencies=[Depends(require_api_key)])
async def generate_asset(request: GenerateRequest) -> dict[str, str]:
    """단일 에셋 생성 작업 등록."""
    _ensure_disk_space_for_enqueue()
    job_id = str(uuid.uuid4())
    await db.create_job(job_id=job_id, job_type="generate_single", payload=request.model_dump())
    await db.enqueue_generation_task(
        {
            "job_id": job_id,
            "project": request.project,
            "asset_key": request.asset_key,
            "category": request.category,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "model_name": request.model_name,
            "width": request.width,
            "height": request.height,
            "steps": request.steps,
            "cfg": request.cfg,
            "sampler": request.sampler,
            "expected_size": request.expected_size,
            "max_colors": request.max_colors,
            "max_retries": request.max_retries,
        }
    )
    await db.mark_job_running(job_id)
    await event_broker.publish({"type": ev.EVT_JOB_CREATED, "job_id": job_id})
    return {"job_id": job_id}


@app.post("/api/generate/batch", dependencies=[Depends(require_api_key)])
async def generate_batch(request: BatchGenerateRequest) -> dict[str, Any]:
    """스펙 기반 배치 생성 작업 등록."""
    _ensure_disk_space_for_enqueue()
    spec = request.spec
    if spec is None:
        if not request.spec_id:
            raise HTTPException(status_code=400, detail="spec 또는 spec_id 중 하나는 필요합니다.")
        spec_path = BASE_DIR / "specs" / f"{request.spec_id}.json"
        if not spec_path.exists():
            raise HTTPException(status_code=404, detail="요청한 spec_id 파일이 없습니다.")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))

    project, tasks = _extract_tasks_from_spec(spec, request.project)
    if not tasks:
        raise HTTPException(status_code=400, detail="생성 가능한 태스크가 없습니다. spec을 확인하세요.")

    job_id = str(uuid.uuid4())
    await db.create_job(
        job_id=job_id,
        job_type="generate_batch",
        payload={"project": project, "task_count": len(tasks)},
    )
    for task in tasks:
        await db.enqueue_generation_task({"job_id": job_id, **task})
    await db.mark_job_running(job_id)
    await event_broker.publish({"type": ev.EVT_BATCH_JOB_CREATED, "job_id": job_id, "task_count": len(tasks)})
    return {"job_id": job_id, "project": project, "task_count": len(tasks)}


async def _enqueue_design_batch(spec: DesignBatchRequest) -> dict[str, Any]:
    """``POST /api/batches`` 와 ``POST /api/mcp/design_asset`` 의 공통 로직.

    batch_id를 발급하고 expand된 task들을 generation_tasks에 enqueue한다.
    예상 ETA는 task당 6초의 거친 추정치이다."""
    _ensure_disk_space_for_enqueue()
    try:
        tasks = expand_design_batch(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not tasks:
        raise HTTPException(status_code=400, detail="expand 결과가 비어있습니다. spec을 확인하세요.")

    batch_id = f"btc_{uuid.uuid4().hex[:16]}"
    job_id = str(uuid.uuid4())
    await db.create_job(
        job_id=job_id,
        job_type="design_batch",
        payload={
            "batch_id": batch_id,
            "asset_key": spec.asset_key,
            "project": spec.project,
            "expanded_count": len(tasks),
        },
    )
    for task in tasks:
        await db.enqueue_generation_task(
            {"job_id": job_id, "batch_id": batch_id, **task}
        )
    await db.mark_job_running(job_id)
    await event_broker.publish(
        {
            "type": ev.EVT_DESIGN_BATCH_CREATED,
            "batch_id": batch_id,
            "job_id": job_id,
            "asset_key": spec.asset_key,
            "expanded_count": len(tasks),
        }
    )
    return {
        "batch_id": batch_id,
        "job_id": job_id,
        "expanded_count": len(tasks),
        "estimated_eta_seconds": len(tasks) * 6,
    }


@app.post("/api/batches", dependencies=[Depends(require_api_key)])
async def create_design_batch(spec: DesignBatchRequest) -> dict[str, Any]:
    """에이전트가 보낸 곱집합 spec을 받아 batch로 enqueue.

    응답: ``batch_id`` (cherry-pick UI가 이 값으로 후보를 묶어 보여줌),
    ``expanded_count``, 거친 ``estimated_eta_seconds``.
    """
    return await _enqueue_design_batch(spec)


@app.post("/api/mcp/design_asset", dependencies=[Depends(require_api_key)])
async def mcp_design_asset(spec: DesignBatchRequest) -> dict[str, Any]:
    """MCP tool 친화 응답을 가진 batch enqueue.

    HTTP와 동일 로직이지만 응답 본문은 MCP가 받기 좋은 ``content``/``isError`` 형식.
    Cursor 등에서 MCP wrapper를 두면 이 엔드포인트로 호출해 친화적 텍스트 결과를
    얻을 수 있다.
    """
    result = await _enqueue_design_batch(spec)
    summary = (
        f"batch_id={result['batch_id']} expanded={result['expanded_count']} "
        f"asset={spec.asset_key} eta~{result['estimated_eta_seconds']}s"
    )
    return {
        "isError": False,
        "content": [{"type": "text", "text": summary}],
        "structuredContent": result,
    }


@app.get("/api/batches")
async def list_batches(
    since: str | None = Query(default=None, description="ISO8601 UTC, 이 시각 이후 생성된 batch만"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """최근 design batch 목록 (cherry-pick UI 진입점).

    각 항목: batch_id, asset_key, project, total/done/failed/active task 수,
    candidate_total, rejected_count, first_created_at, last_updated_at.
    """
    rows = await db.list_recent_batches(since=since, limit=limit)
    return {"count": len(rows), "items": rows}


@app.get("/api/batches/{batch_id}")
async def get_batch_detail(batch_id: str) -> dict[str, Any]:
    """단일 design batch 상세 + spec 재조립.

    응답: ``batch_id``, ``project``, ``asset_key``, ``category``, ``job_id``,
    ``first_created_at``, ``last_updated_at``, ``tasks`` (status 집계),
    ``candidates`` (validation/rejected/picked 집계), ``spec`` (seeds/models/
    prompts/loras distinct + common steps/cfg/sampler/max_colors).

    체리픽 UI(`/app/batches/{id}`) 의 SpecView 가 "샘플 후보 1장" 대신 배치 전체
    스펙을 보여주기 위해 사용.
    """
    detail = await db.get_batch_detail(batch_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id}")
    return detail


@app.get("/api/batches/{batch_id}/tasks")
async def list_batch_tasks_api(batch_id: str) -> dict[str, Any]:
    """BatchDetail Tasks 탭 소스.

    ``generation_tasks`` 의 ``id / model_name / seed / status / retries /
    max_retries / last_error / next_attempt_at`` 컬럼을 그대로 반환한다.
    실패/진행 중/대기/완료 순으로 정렬한다.
    """
    if not await db.get_batch_detail(batch_id):
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id}")
    rows = await db.list_batch_tasks(batch_id)
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": int(row["id"]),
                "model": row.get("model_name"),
                "seed": row.get("seed"),
                "status": row.get("status"),
                "attempts": int(row.get("retries") or 0),
                "max_retries": int(row.get("max_retries") or 0),
                "last_error": row.get("last_error"),
                "next_attempt_at": row.get("next_attempt_at"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    failed = sum(1 for it in items if it["status"] == "failed")
    return {
        "batch_id": batch_id,
        "count": len(items),
        "failed_count": failed,
        "items": items,
    }


@app.post(
    "/api/batches/{batch_id}/retry-failed",
    dependencies=[Depends(require_api_key)],
)
async def retry_failed_batch_tasks_api(batch_id: str) -> dict[str, Any]:
    """배치 내 ``failed`` 태스크만 골라 큐로 되돌린다."""
    if not await db.get_batch_detail(batch_id):
        raise HTTPException(status_code=404, detail=f"batch not found: {batch_id}")
    retried = await db.retry_failed_batch_tasks(batch_id)
    if retried:
        await event_broker.publish(
            {
                "type": ev.EVT_BATCH_RETRY_FAILED,
                "batch_id": batch_id,
                "retried_count": len(retried),
            }
        )
    return {"batch_id": batch_id, "retried_count": len(retried), "task_ids": retried}


@app.get("/api/batches/{batch_id}/candidates")
async def list_batch_candidates(batch_id: str) -> dict[str, Any]:
    """한 batch에 속한 모든 후보 (cherry-pick UI 본 화면).

    rejected가 뒤로 정렬된다. 각 항목은 메타데이터(LoRA spec, seed, model 등)와
    이미지 URL을 포함한다."""
    rows = await db.list_batch_candidates(batch_id)
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                **row,
                "image_url": (
                    "/api/asset-candidates/image?"
                    + f"project={row['project']}&asset_key={row['asset_key']}"
                    + f"&job_id={row['job_id']}&slot_index={int(row['slot_index'])}"
                ),
            }
        )
    return {"batch_id": batch_id, "count": len(items), "items": items}


@app.post(
    "/api/batches/{batch_id}/candidates/{candidate_id}/reject",
    dependencies=[Depends(require_api_key)],
)
async def reject_batch_candidate(batch_id: str, candidate_id: int) -> dict[str, Any]:
    """후보를 reject 마킹 (GC 우선 대상). 디스크 파일은 즉시 지우지 않는다."""
    candidate = await db.get_candidate_by_id(candidate_id)
    if candidate is None or candidate.get("batch_id") != batch_id:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")
    ok = await db.reject_candidate(candidate_id)
    if not ok:
        raise HTTPException(status_code=500, detail="reject 처리 실패")
    await event_broker.publish(
        {
            "type": ev.EVT_CANDIDATE_REJECTED,
            "batch_id": batch_id,
            "candidate_id": candidate_id,
        }
    )
    return {"ok": True, "candidate_id": candidate_id, "is_rejected": True}


@app.post(
    "/api/batches/{batch_id}/candidates/{candidate_id}/unreject",
    dependencies=[Depends(require_api_key)],
)
async def unreject_batch_candidate(batch_id: str, candidate_id: int) -> dict[str, Any]:
    """reject 마킹을 되돌린다 (cherry-pick UI의 5초 undo toast 용)."""
    candidate = await db.get_candidate_by_id(candidate_id)
    if candidate is None or candidate.get("batch_id") != batch_id:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")
    ok = await db.unreject_candidate(candidate_id)
    if not ok:
        raise HTTPException(status_code=500, detail="unreject 처리 실패")
    await event_broker.publish(
        {
            "type": ev.EVT_CANDIDATE_UNREJECTED,
            "batch_id": batch_id,
            "candidate_id": candidate_id,
        }
    )
    return {"ok": True, "candidate_id": candidate_id, "is_rejected": False}


@app.get("/api/cherry-pick/queue")
async def get_cherry_pick_queue(
    since: str | None = Query(default=None, description="ISO8601 UTC. 미지정 시 오늘 KST 00:00."),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    """오늘의 cherry-pick 큐 (헤더 표시용).

    - 각 batch: ``batch_id``, ``project``, ``asset_key``, ``total``, ``remaining``,
      ``approved`` (이미 메인 asset 픽이 끝났는지), ``first_created_at``.
    - ``total_remaining``: 모든 batch의 ``remaining`` 합. (rejected 제외, 미픽 후보 수)
    - ``total_batches`` / ``pending_batches``: 전체/미완료 batch 수.
    """
    if since is None:
        kst = timezone(timedelta(hours=9))
        today_kst_midnight = datetime.now(kst).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = today_kst_midnight.astimezone(timezone.utc).isoformat()
    rows = await db.list_today_batches(since, limit=limit)
    pending = [r for r in rows if not r.get("approved")]
    total_remaining = sum(int(r.get("remaining", 0)) for r in pending)
    return {
        "since": since,
        "total_batches": len(rows),
        "pending_batches": len(pending),
        "total_remaining": total_remaining,
        "items": rows,
    }


@app.post(
    "/api/assets/{asset_id}/undo-approve",
    dependencies=[Depends(require_api_key)],
)
async def undo_approve(asset_id: str) -> dict[str, Any]:
    """가장 최근 approve를 되돌린다 (cherry-pick UI의 5초 undo toast 용).

    - 이전 history가 있으면: 해당 history를 primary로 복원하고 history 행 삭제.
    - history가 없으면 (이번 approve로 신규 생성된 asset): asset 행 자체를 삭제.
    - 이번 approve가 디스크에 복사한 새 primary 파일은 삭제한다 (history image_path는 보존).
    """
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")

    primary_path_str = asset.get("image_path")
    history = await db.get_latest_asset_history(asset_id)
    restored_from = None
    if history:
        await db.restore_asset_from_history(asset_id, history)
        await db.delete_asset_history(int(history["id"]))
        restored_from = history.get("image_path")
        new_status = "ok-restored"
    else:
        await db.delete_asset(asset_id)
        new_status = "ok-deleted"

    # 이 approve가 candidate-기반(approve-from-candidate / select-candidate)이었다면
    # 해당 candidate의 picked_at/picked_asset_id 마킹을 풀어서 batch가 다시
    # cherry-pick 큐에 pending으로 돌아오게 한다. 풀지 않으면 batch가 영구히
    # approved로 남아 큐에서 사라진 채 복구 안 됨.
    await db.unmark_candidates_picked_for_asset(asset_id)

    if primary_path_str and primary_path_str != restored_from:
        try:
            primary_path = _ensure_path_allowed(Path(primary_path_str))
            if primary_path.exists():
                primary_path.unlink()
        except HTTPException as exc:
            # allowlist 밖 경로는 건드리지 않는다(undo 동작은 계속 진행).
            _push_log(
                "warn",
                "undo_approve skipped deleting primary outside allowed roots",
                context={"asset_id": asset_id, "reason": str(exc.detail)},
            )
        except OSError as exc:
            # 파일 삭제 실패는 비치명적이며, undo의 DB 복원은 유지한다.
            _push_log(
                "warn",
                "undo_approve failed to delete primary file",
                context={"asset_id": asset_id, "error": str(exc)},
            )

    await event_broker.publish(
        {
            "type": ev.EVT_ASSET_APPROVE_UNDONE,
            "asset_id": asset_id,
            "restored_from_history": restored_from is not None,
        }
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "result": new_status,
        "restored_from": restored_from,
    }


@app.post("/api/assets/approve-from-candidate", dependencies=[Depends(require_api_key)])
async def approve_from_candidate(body: ApproveFromCandidateRequest) -> dict[str, Any]:
    """cherry-pick UI에서 1장 선택 → 메인 asset으로 승격.

    - 기존 메인이 없으면 새 ``assets`` 행을 만든다.
    - 기존 메인이 있으면 ``replace_asset_primary_image`` 로 교체하면서
      이전 메인을 ``asset_history`` 에 스냅샷으로 남긴다.
    - 후보 파일은 unique 경로로 복사해 history image_path가 디스크에 보존되게 한다.
    """
    candidate = await db.get_candidate_by_id(body.candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")

    project = body.project or candidate["project"]
    asset_key = body.asset_key or candidate["asset_key"]
    category = body.category or "character"

    src_path = _ensure_path_allowed(Path(candidate["image_path"]))
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="후보 파일이 디스크에 없습니다.")

    safe_key = _safe_segment(asset_key)
    safe_job = _safe_segment(str(candidate.get("job_id") or "nojob"))
    dest_dir = _approved_dir(project)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (
        f"{safe_key}__primary__{safe_job}__cand{int(body.candidate_id)}.png"
    )
    shutil.copy2(src_path, dest)

    meta: dict[str, Any] = {}
    if candidate.get("metadata_json"):
        try:
            meta = json.loads(candidate["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            meta = {}

    validation = validate_asset(
        image_path=dest,
        expected_size=meta.get("expected_size"),
        max_colors=int(meta.get("max_colors", 32)),
    )

    metadata_out = candidate.get("metadata_json") or json.dumps(meta, ensure_ascii=False)
    # candidate 의 approval_mode 를 새 asset 으로 전파 — bypass candidate 가
    # 사람 cherry-pick 으로 promote 돼도 자산은 여전히 bypass 격리 유지.
    candidate_approval_mode = candidate.get("approval_mode") or "manual"

    existing = await db.get_asset_by_key(project, asset_key)
    if existing:
        asset_id = existing["id"]
        ok = await db.replace_asset_primary_image(
            asset_id,
            image_path=str(dest),
            width=validation.width,
            height=validation.height,
            color_count=validation.color_count,
            has_alpha=validation.has_alpha,
            validation_status="pass" if validation.passed else "fail",
            validation_message=validation.message,
            generation_seed=candidate.get("generation_seed"),
            generation_model=candidate.get("generation_model"),
            generation_prompt=candidate.get("generation_prompt"),
            metadata_json=metadata_out,
            approval_mode=candidate_approval_mode,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="에셋 갱신에 실패했습니다.")
    else:
        await db.upsert_scanned_asset(
            project=project,
            asset_key=asset_key,
            category=category,
            image_path=str(dest),
            width=validation.width,
            height=validation.height,
            color_count=validation.color_count,
            has_alpha=validation.has_alpha,
            validation_status="pass" if validation.passed else "fail",
            validation_message=validation.message,
        )
        row = await db.get_asset_by_key(project, asset_key)
        if not row:
            raise HTTPException(status_code=500, detail="에셋 등록에 실패했습니다.")
        asset_id = row["id"]
        # upsert_scanned_asset은 generation_* 필드를 비워둔다. 첫 승인일 때도
        # candidate가 들고 있던 seed/model/prompt/metadata를 보존해서
        # /api/assets/{id}/regenerate 가 디폴트로 폴백하지 않게 한다.
        await db.set_asset_provenance(
            asset_id,
            generation_seed=candidate.get("generation_seed"),
            generation_model=candidate.get("generation_model"),
            generation_prompt=candidate.get("generation_prompt"),
            metadata_json=metadata_out,
            approval_mode=candidate_approval_mode,
        )
    if body.set_status != "pending":
        await db.update_asset_status(asset_id=asset_id, status=body.set_status)

    # batch 완료 추적: 이 batch에서 한 장 골랐다는 표시. inline 키 편집으로
    # asset_key가 달라져도 candidate.batch_id는 그대로라 원본 batch가 done.
    # asset_id를 같이 박아둬서 undo-approve가 역추적해 풀 수 있게 한다.
    await db.mark_candidate_picked(int(body.candidate_id), asset_id)

    await event_broker.publish(
        {
            "type": ev.EVT_ASSET_APPROVED_FROM_CANDIDATE,
            "asset_id": asset_id,
            "candidate_id": body.candidate_id,
            "batch_id": candidate.get("batch_id"),
            "status": body.set_status,
        }
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "image_path": str(dest),
        "validation_status": "pass" if validation.passed else "fail",
        "status": body.set_status,
    }


@app.post("/api/projects/scan", dependencies=[Depends(require_api_key)])
async def scan_project_assets(request: ScanRequest) -> dict[str, Any]:
    """기존 디렉토리를 스캔해 에셋 DB를 동기화한다."""
    root = _ensure_path_allowed(Path(request.root_path))
    try:
        scanned = scan_directory(root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    inserted = 0
    for item in scanned:
        # 스캐너가 반환한 경로도 한 번 더 sanitizer를 거친다(심볼릭 링크 방어).
        try:
            safe_image_path = _ensure_path_allowed(Path(item["image_path"]))
        except HTTPException:
            continue
        result = validate_asset(image_path=safe_image_path, expected_size=None, max_colors=request.max_colors)
        await db.upsert_scanned_asset(
            project=request.project,
            asset_key=item["asset_key"],
            category=item["category"],
            image_path=str(safe_image_path),
            width=result.width,
            height=result.height,
            color_count=result.color_count,
            has_alpha=result.has_alpha,
            validation_status="pass" if result.passed else "fail",
            validation_message=result.message,
        )
        inserted += 1

    await event_broker.publish(
        {"type": ev.EVT_SCAN_COMPLETED, "project": request.project, "count": inserted, "root_path": request.root_path}
    )
    return {"project": request.project, "scanned_count": inserted}


@app.get("/api/jobs/recent")
async def recent_jobs(limit: int = Query(default=10, ge=1, le=100)) -> list[dict[str, Any]]:
    """최근 작업 타임라인.

    NOTE: ``/api/jobs/recent`` 는 반드시 ``/api/jobs/{job_id}`` 보다 먼저 선언되어야
    한다. FastAPI 는 라우트를 선언 순서대로 매칭하므로, 순서가 뒤바뀌면 ``recent``
    가 ``job_id`` 로 빠져 404 가 난다 (실제로 발생했던 버그).
    """
    return await db.list_recent_jobs(limit=limit)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """작업 상태 조회.

    §1.B — workflow 호출 job 이면 ``prompt_resolution`` 필드에 첫 task 의 prompt
    합성 결과 노출 (재현·디버깅용). legacy DB row 또는 workflow 외 job 은 None.
    """
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    prompt_resolution = await db.get_first_task_prompt_resolution(job_id)
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "total_count": job.total_count,
        "completed_count": job.completed_count,
        "failed_count": job.failed_count,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "prompt_resolution": prompt_resolution,
    }


@app.get("/api/assets")
async def list_assets(
    project: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    validation_status: str | None = Query(default=None),
    include_bypassed: bool = Query(default=False),
) -> list[dict[str, Any]]:
    """에셋 목록 조회.

    ``include_bypassed=true`` 일 때만 bypass 모드 자산이 포함된다.
    """
    return await db.list_assets(
        project=project,
        status=status,
        category=category,
        validation_status=validation_status,
        include_bypassed=include_bypassed,
    )


@app.get("/api/assets/summary")
async def asset_summary(project: str | None = Query(default=None)) -> dict[str, Any]:
    """에셋 집계 요약."""
    summary = await db.get_asset_summary(project=project)
    return {"project": project, **summary}


@app.get("/api/assets/{asset_id}/detail")
async def get_asset_detail(asset_id: str) -> dict[str, Any]:
    """에셋 단건 메타데이터."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    return asset


@app.get("/api/assets/{asset_id}/image")
async def get_asset_image(asset_id: str) -> FileResponse:
    """에셋 원본 이미지 반환."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    safe_path = _ensure_path_allowed(Path(asset["image_path"]))
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="이미지 파일이 존재하지 않습니다.")
    return FileResponse(safe_path)


@app.get("/api/assets/{asset_id}/history")
async def get_asset_history(asset_id: str) -> list[dict[str, Any]]:
    """에셋 교체/재생성 이전 버전 이력."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    return await db.list_asset_history(asset_id)


@app.get("/api/assets/{asset_id}/candidates")
async def get_asset_candidates(
    asset_id: str,
    job_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """배치 후보 슬롯 목록 (job_id 없으면 최근 후보 전체)."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    return await db.list_asset_candidates(asset["project"], asset["asset_key"], job_id)


_THUMB_ALLOWED_SIZES: frozenset[int] = frozenset({128, 192, 256, 384, 512})
_THUMB_CACHE_DIR = DATA_DIR / "thumbs"


def _thumb_cache_path(safe_path: Path, size: int) -> Path:
    """썸네일 캐시 파일 경로 (원본 path + size 의 sha1 기반)."""
    key = hashlib.sha1(f"{safe_path}|{size}".encode("utf-8")).hexdigest()
    return _THUMB_CACHE_DIR / f"{key[:2]}" / f"{key}.webp"


def _ensure_thumb(safe_path: Path, size: int) -> Path:
    """원본 PNG 에서 ``size x size`` 이하의 webp 썸네일을 생성(캐시)해서
    경로를 돌려준다.

    - 원본 mtime 이 캐시 파일보다 최신이면 재생성.
    - Pillow 가 여기서만 호출되므로 썸네일 요청이 없는 경로에는 영향 없음.
    """
    from PIL import Image  # local import: 이미지 요청 핫패스 외부 영향 없음

    cache_path = _thumb_cache_path(safe_path, size)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if cache_path.exists() and cache_path.stat().st_mtime >= safe_path.stat().st_mtime:
            return cache_path
    except OSError:
        # stat 실패 시 캐시 freshness 판단을 건너뛰고 생성 경로로 진행.
        pass
    with Image.open(safe_path) as im:
        im.thumbnail((size, size), Image.LANCZOS)
        tmp = cache_path.with_suffix(".tmp.webp")
        im.save(tmp, format="WEBP", quality=85, method=4)
        tmp.replace(cache_path)
    return cache_path


@app.get("/api/asset-candidates/image")
async def get_candidate_image_file(
    request: Request,
    project: str = Query(..., description="프로젝트 id"),
    asset_key: str = Query(...),
    job_id: str = Query(...),
    slot_index: int = Query(..., ge=0),
    size: int | None = Query(
        default=None,
        description="썸네일 variant 요청 크기 (128/192/256/384/512). 미지정 시 원본 PNG.",
    ),
) -> Response:
    """후보 슬롯 이미지 파일 (DB 등록 경로만 허용).

    ``size`` 가 지정되면 ``data/thumbs/`` 아래에 webp 썸네일을 캐시해 반환하고
    ``ETag`` / ``Cache-Control: public, max-age=31536000, immutable`` 을 건다.
    원본 요청은 변동 가능성이 있으므로 weak cache 만 붙인다.
    """
    rows = await db.list_asset_candidates(project, asset_key, job_id)
    pick = next((r for r in rows if int(r["slot_index"]) == slot_index), None)
    if pick is None:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")
    safe_path = _ensure_path_allowed(Path(pick["image_path"]))
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="후보 파일이 없습니다.")

    if size is None:
        return FileResponse(
            safe_path,
            headers={"Cache-Control": "public, max-age=300"},
        )

    if size not in _THUMB_ALLOWED_SIZES:
        raise HTTPException(
            status_code=400,
            detail=f"size 는 {sorted(_THUMB_ALLOWED_SIZES)} 중 하나여야 합니다.",
        )
    try:
        thumb_path = await asyncio.to_thread(_ensure_thumb, safe_path, size)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"썸네일 생성 실패: {exc}") from exc

    try:
        stat = thumb_path.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"썸네일 stat 실패: {exc}") from exc
    etag = f'W/"{int(stat.st_mtime)}-{stat.st_size}-{size}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
    return FileResponse(
        thumb_path,
        media_type="image/webp",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@app.post("/api/assets/{asset_id}/select-candidate", dependencies=[Depends(require_api_key)])
async def select_asset_candidate(asset_id: str, body: SelectCandidateRequest) -> dict[str, Any]:
    """후보 슬롯을 메인 에셋으로 확정(이전 메인은 asset_history에 스냅샷)."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    rows = await db.list_asset_candidates(asset["project"], asset["asset_key"], body.job_id)
    pick = next((r for r in rows if int(r["slot_index"]) == body.slot_index), None)
    if pick is None:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")
    safe_src = _ensure_path_allowed(Path(pick["image_path"]))
    if not safe_src.exists():
        raise HTTPException(status_code=404, detail="후보 파일이 없습니다.")

    # 이전 메인 이미지를 덮어쓰지 않도록 새 unique 경로에 복사한다.
    # 이렇게 하면 asset_history에 기록된 기존 image_path가 디스크에 그대로 보존된다.
    # candidates/ 트리는 GC가 정리하므로 approved/ 트리에 둔다.
    safe_key = _safe_segment(asset["asset_key"])
    safe_job = _safe_segment(body.job_id)
    dest_dir = _approved_dir(asset["project"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_key}__primary__{safe_job}__slot{body.slot_index}.png"
    shutil.copy2(safe_src, dest)

    meta: dict[str, Any] = {}
    if pick.get("metadata_json"):
        try:
            meta = json.loads(pick["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            meta = {}
    max_colors = int(meta.get("max_colors", 32))
    expected = meta.get("expected_size")
    if expected is None:
        expected = asset.get("width")

    validation = validate_asset(
        image_path=dest,
        expected_size=int(expected) if expected is not None else None,
        max_colors=max_colors,
    )
    metadata_out = pick.get("metadata_json")
    if not metadata_out:
        metadata_out = json.dumps(meta, ensure_ascii=False) if meta else None

    ok = await db.replace_asset_primary_image(
        asset_id,
        image_path=str(dest),
        width=validation.width,
        height=validation.height,
        color_count=validation.color_count,
        has_alpha=validation.has_alpha,
        validation_status="pass" if validation.passed else "fail",
        validation_message=validation.message,
        generation_seed=pick.get("generation_seed"),
        generation_model=pick.get("generation_model"),
        generation_prompt=pick.get("generation_prompt"),
        metadata_json=metadata_out,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="에셋 갱신에 실패했습니다.")
    await db.mark_candidate_picked(int(pick["id"]), asset_id)
    await event_broker.publish(
        {
            "type": ev.EVT_ASSET_CANDIDATE_SELECTED,
            "asset_id": asset_id,
            "job_id": body.job_id,
            "slot_index": body.slot_index,
        }
    )
    return {"ok": True, "asset_id": asset_id, "validation_status": "pass" if validation.passed else "fail"}


@app.post("/api/assets/{asset_id}/restore-history", dependencies=[Depends(require_api_key)])
async def restore_asset_history(asset_id: str, body: RestoreHistoryRequest) -> dict[str, Any]:
    """AssetDetail의 이전 버전(history 스냅샷)을 다시 메인으로 되돌린다.

    현재 메인은 자동으로 새 history 행으로 밀려난다
    (``replace_asset_primary_image`` 내부 로직). 따라서 복원은 idempotent하지
    않고 히스토리가 한 버전씩 쌓인다.
    """
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    history_rows = await db.list_asset_history(asset_id)
    target = next((h for h in history_rows if int(h["version"]) == body.version), None)
    if target is None:
        raise HTTPException(status_code=404, detail="해당 버전의 history가 없습니다.")

    safe_src = _ensure_path_allowed(Path(target["image_path"]))
    if not safe_src.exists():
        raise HTTPException(status_code=410, detail="해당 버전의 파일이 사라졌습니다. 복원할 수 없습니다.")

    # history.image_path 가 approved/ 트리에 남아있지 않은 레거시 케이스(예: scan 으로
    # 추가된 에셋)를 위해, 복원본을 새 고유 경로로 복사해 primary 로 지정한다.
    safe_key = _safe_segment(asset["asset_key"])
    safe_job = _safe_segment(str(target.get("job_id") or "restore"))
    dest_dir = _approved_dir(asset["project"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_key}__restore__v{int(target['version'])}__{safe_job}.png"
    shutil.copy2(safe_src, dest)

    meta: dict[str, Any] = {}
    if target.get("metadata_json"):
        try:
            meta = json.loads(target["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            meta = {}
    max_colors = int(meta.get("max_colors", 32))
    expected = meta.get("expected_size")
    if expected is None:
        expected = target.get("width") or asset.get("width")

    validation = validate_asset(
        image_path=dest,
        expected_size=int(expected) if expected is not None else None,
        max_colors=max_colors,
    )
    metadata_out = target.get("metadata_json")
    if not metadata_out and meta:
        metadata_out = json.dumps(meta, ensure_ascii=False)

    ok = await db.replace_asset_primary_image(
        asset_id,
        image_path=str(dest),
        width=validation.width,
        height=validation.height,
        color_count=validation.color_count,
        has_alpha=validation.has_alpha,
        validation_status="pass" if validation.passed else "fail",
        validation_message=validation.message,
        generation_seed=target.get("generation_seed"),
        generation_model=target.get("generation_model"),
        generation_prompt=target.get("generation_prompt"),
        metadata_json=metadata_out,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="에셋 갱신에 실패했습니다.")

    # 복원 직후 가장 최신 history version 을 리턴하면 UI 가 rehydrate 하기 쉽다.
    new_rows = await db.list_asset_history(asset_id)
    new_version = int(new_rows[0]["version"]) if new_rows else None

    await event_broker.publish(
        {
            "type": ev.EVT_ASSET_HISTORY_RESTORED,
            "asset_id": asset_id,
            "version": body.version,
            "new_version": new_version,
        }
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "restored_from_version": body.version,
        "new_history_version": new_version,
        "validation_status": "pass" if validation.passed else "fail",
    }


@app.patch("/api/assets/{asset_id}", dependencies=[Depends(require_api_key)])
async def patch_asset(asset_id: str, request: AssetStatusPatch) -> dict[str, bool]:
    """에셋 상태 변경."""
    updated = await db.update_asset_status(asset_id=asset_id, status=request.status)
    if not updated:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    await event_broker.publish({"type": ev.EVT_ASSET_STATUS_CHANGED, "asset_id": asset_id, "status": request.status})
    return {"ok": True}


@app.post("/api/assets/{asset_id}/regenerate", dependencies=[Depends(require_api_key)])
async def regenerate_asset(asset_id: str) -> dict[str, str]:
    """기존 에셋 파라미터를 기반으로 재생성 작업을 등록한다."""
    _ensure_disk_space_for_enqueue()
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")

    job_id = str(uuid.uuid4())
    prompt = asset.get("generation_prompt")
    if not prompt:
        prompt = f"pixel art {asset.get('category', 'asset')} {asset.get('asset_key', '')}".strip()

    metadata: dict[str, Any] = {}
    if asset.get("metadata_json"):
        try:
            metadata = json.loads(asset["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            metadata = {}

    steps = int(metadata.get("steps", 20))
    cfg = float(metadata.get("cfg", 7.0))
    sampler = str(metadata.get("sampler", "DPM++ 2M"))
    negative_prompt = metadata.get("negative_prompt")
    max_colors = int(metadata.get("max_colors", 32))
    max_retries = int(metadata.get("max_retries", 3))
    expected_size = metadata.get("expected_size")
    if expected_size is None:
        expected_size = asset.get("width")

    await db.create_job(
        job_id=job_id,
        job_type="regenerate",
        payload={"source_asset_id": asset_id},
    )
    await db.enqueue_generation_task(
        {
            "job_id": job_id,
            "project": asset["project"],
            "asset_key": asset["asset_key"],
            "category": asset["category"],
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model_name": asset.get("generation_model"),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "steps": steps,
            "cfg": cfg,
            "sampler": sampler,
            "expected_size": expected_size,
            "max_colors": max_colors,
            "max_retries": max_retries,
        }
    )
    await db.mark_job_running(job_id)
    await event_broker.publish(
        {
            "type": ev.EVT_ASSET_REGENERATE_QUEUED,
            "asset_id": asset_id,
            "job_id": job_id,
            "source_seed": metadata.get("seed"),
        }
    )
    return {"job_id": job_id}


@app.post("/api/validate/all", dependencies=[Depends(require_api_key)])
async def validate_all_assets(project: str | None = Query(default=None)) -> dict[str, Any]:
    """전체 에셋 재검증.

    NOTE: 반드시 ``/api/validate/{asset_id}`` 보다 먼저 선언되어야 한다. FastAPI
    는 라우트를 선언 순서대로 매칭하므로, 순서가 뒤바뀌면 ``all`` 이 ``asset_id``
    로 빠져 404 가 난다 (실제로 발생했던 버그).
    """
    assets = await db.list_assets(project=project)
    checked = 0
    failed = 0
    for asset in assets:
        result = validate_asset(image_path=Path(asset["image_path"]))
        await db.update_asset_validation(
            asset["id"],
            width=result.width,
            height=result.height,
            color_count=result.color_count,
            has_alpha=result.has_alpha,
            validation_status="pass" if result.passed else "fail",
            validation_message=result.message,
        )
        if not result.passed:
            failed += 1
        checked += 1
    return {"checked": checked, "failed": failed, "project": project}


@app.post("/api/validate/{asset_id}", dependencies=[Depends(require_api_key)])
async def validate_asset_endpoint(asset_id: str) -> dict[str, Any]:
    """단일 에셋 재검증."""
    asset = await db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    result = validate_asset(image_path=Path(asset["image_path"]))
    await db.update_asset_validation(
        asset_id,
        width=result.width,
        height=result.height,
        color_count=result.color_count,
        has_alpha=result.has_alpha,
        validation_status="pass" if result.passed else "fail",
        validation_message=result.message,
    )
    return {
        "asset_id": asset_id,
        "passed": result.passed,
        "message": result.message,
        "width": result.width,
        "height": result.height,
        "color_count": result.color_count,
        "has_alpha": result.has_alpha,
    }


@app.post("/api/batch/revalidate-failed", dependencies=[Depends(require_api_key)])
async def batch_revalidate_failed(
    project: str | None = Query(default=None),
) -> dict[str, Any]:
    """검증 FAIL 에셋만 일괄 재검증."""
    assets = await db.list_assets(project=project, validation_status="fail")
    updated = 0
    still_fail = 0
    for asset in assets:
        result = validate_asset(image_path=Path(asset["image_path"]))
        await db.update_asset_validation(
            asset["id"],
            width=result.width,
            height=result.height,
            color_count=result.color_count,
            has_alpha=result.has_alpha,
            validation_status="pass" if result.passed else "fail",
            validation_message=result.message,
        )
        updated += 1
        if not result.passed:
            still_fail += 1
    await event_broker.publish(
        {"type": ev.EVT_BATCH_REVALIDATE_FAILED_DONE, "count": updated, "project": project}
    )
    return {"revalidated": updated, "still_fail": still_fail, "project": project}


@app.post("/api/batch/regenerate-failed", dependencies=[Depends(require_api_key)])
async def batch_regenerate_failed(
    project: str | None = Query(default=None),
) -> dict[str, Any]:
    """검증 FAIL 에셋에 대해 재생성 작업을 일괄 등록한다."""
    _ensure_disk_space_for_enqueue()
    assets = await db.list_assets(project=project, validation_status="fail")
    job_ids: list[str] = []
    for asset in assets:
        asset_row = await db.get_asset(asset["id"])
        if asset_row is None:
            continue
        job_id = str(uuid.uuid4())
        prompt = asset_row.get("generation_prompt")
        if not prompt:
            prompt = f"pixel art {asset_row.get('category', 'asset')} {asset_row.get('asset_key', '')}".strip()
        metadata: dict[str, Any] = {}
        if asset_row.get("metadata_json"):
            try:
                metadata = json.loads(asset_row["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        steps = int(metadata.get("steps", 20))
        cfg = float(metadata.get("cfg", 7.0))
        sampler = str(metadata.get("sampler", "DPM++ 2M"))
        negative_prompt = metadata.get("negative_prompt")
        max_colors = int(metadata.get("max_colors", 32))
        max_retries = int(metadata.get("max_retries", 3))
        expected_size = metadata.get("expected_size")
        if expected_size is None:
            expected_size = asset_row.get("width")
        await db.create_job(
            job_id=job_id,
            job_type="regenerate",
            payload={"source_asset_id": asset_row["id"], "batch": "regenerate_failed"},
        )
        await db.enqueue_generation_task(
            {
                "job_id": job_id,
                "project": asset_row["project"],
                "asset_key": asset_row["asset_key"],
                "category": asset_row["category"],
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "model_name": asset_row.get("generation_model"),
                "width": asset_row.get("width"),
                "height": asset_row.get("height"),
                "steps": steps,
                "cfg": cfg,
                "sampler": sampler,
                "expected_size": expected_size,
                "max_colors": max_colors,
                "max_retries": max_retries,
            }
        )
        await db.mark_job_running(job_id)
        job_ids.append(job_id)
    await event_broker.publish(
        {"type": ev.EVT_BATCH_REGENERATE_FAILED_QUEUED, "jobs": len(job_ids), "project": project}
    )
    return {"queued_jobs": len(job_ids), "job_ids": job_ids, "project": project}


@app.post("/api/export", dependencies=[Depends(require_api_key)])
async def export_assets(request: ExportRequest) -> dict[str, Any]:
    """승인된 에셋을 대상 디렉토리로 복사한다.

    ``approval_mode='bypass'`` 자산은 의도된 임시물이라 export 와 manifest 양쪽
    에서 모두 제외된다. 응답의 ``excluded_bypassed`` 로 제외된 수를 알린다.
    """
    approved = await db.list_approved_assets(
        project=request.project,
        category=request.category,
        since=request.since,
    )
    approved_with_bypass = await db.list_approved_assets(
        project=request.project,
        category=request.category,
        since=request.since,
        include_bypassed=True,
    )
    excluded_bypassed = len(approved_with_bypass) - len(approved)
    if not approved:
        return {
            "exported_count": 0,
            "output_dir": request.output_dir,
            "excluded_bypassed": excluded_bypassed,
        }

    # 사용자 입력 output_dir을 allowlist 내부로 제한한다(없는 디렉토리는 미리 만든다).
    raw_root = Path(request.output_dir).expanduser()
    raw_root.mkdir(parents=True, exist_ok=True)
    output_root = _ensure_path_allowed(raw_root)
    try:
        _check_disk_space(output_root)
    except RuntimeError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    exported_count = 0
    for asset in approved:
        project = _safe_segment(asset["project"])
        category = _safe_segment(asset["category"])
        asset_key = _safe_segment(asset["asset_key"])
        # 정제된 세그먼트만 사용 → 결과 경로는 항상 output_root 하위에 머문다.
        target_dir = output_root / project / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{asset_key}.png"
        src_image = _ensure_path_allowed(Path(asset["image_path"]))
        shutil.copy2(src_image, target_path)
        exported_count += 1

    manifest_path: str | None = None
    if request.save_manifest:
        manifest_items: list[dict[str, Any]] = []
        total_bytes = 0
        for asset in approved:
            image_path = Path(asset["image_path"])
            sha256: str | None = None
            size_bytes: int | None = None
            if image_path.exists():
                try:
                    data = image_path.read_bytes()
                    sha256 = hashlib.sha256(data).hexdigest()
                    size_bytes = len(data)
                    total_bytes += size_bytes
                except OSError:
                    sha256 = None
                    size_bytes = None
            manifest_items.append(
                {
                    "project": asset["project"],
                    "asset_key": asset["asset_key"],
                    "category": asset["category"],
                    "path": str(output_root / asset["project"] / asset["category"] / f"{asset['asset_key']}.png"),
                    "width": asset["width"],
                    "height": asset["height"],
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                }
            )
        manifest_data = {
            "count": len(manifest_items),
            "total_bytes": total_bytes,
            "items": manifest_items,
        }
        manifest_file = output_root / "asset-manifest.json"
        manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_path = str(manifest_file)

    await event_broker.publish(
        {"type": ev.EVT_EXPORT_COMPLETED, "count": exported_count, "output_dir": str(output_root), "manifest_path": manifest_path}
    )
    return {
        "exported_count": exported_count,
        "output_dir": str(output_root),
        "manifest_path": manifest_path,
        "excluded_bypassed": excluded_bypassed,
    }


@app.get("/api/export/manifest")
async def export_manifest(
    project: str | None = None,
    category: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """승인본 기준 manifest를 반환한다.

    Export 화면 미리보기 + 실 export 의 공용 소스. 각 항목은 파일 바이트 크기
    (``size_bytes``) 를 포함하여 Export 화면에서 총 용량(MB)을 라이브로 계산할
    수 있다. sha256 은 bytes 를 읽어 계산하므로 큰 프로젝트에서는 비용이 있으나
    export 후보 = 승인본 = 수십~수백 장 수준이라 실 운영 범위에서는 무시 가능.
    """
    approved = await db.list_approved_assets(
        project=project, category=category, since=since
    )
    approved_with_bypass = await db.list_approved_assets(
        project=project, category=category, since=since, include_bypassed=True
    )
    excluded_bypassed = len(approved_with_bypass) - len(approved)
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for asset in approved:
        image_path = Path(asset["image_path"])
        sha256: str | None = None
        size_bytes: int | None = None
        if image_path.exists():
            try:
                data = image_path.read_bytes()
                sha256 = hashlib.sha256(data).hexdigest()
                size_bytes = len(data)
                total_bytes += size_bytes
            except OSError:
                sha256 = None
                size_bytes = None
        items.append(
            {
                "project": asset["project"],
                "asset_key": asset["asset_key"],
                "category": asset["category"],
                "path": asset["image_path"],
                "width": asset["width"],
                "height": asset["height"],
                "sha256": sha256,
                "size_bytes": size_bytes,
                "updated_at": asset.get("updated_at") or asset.get("created_at"),
            }
        )
    return {
        "count": len(items),
        "total_bytes": total_bytes,
        "items": items,
        "excluded_bypassed": excluded_bypassed,
    }


async def sse_event_generator(
    broker: EventBroker, keepalive_seconds: float
) -> AsyncIterator[str]:
    """SSE 본문 프레임을 yield 하는 async generator.

    이벤트가 ``keepalive_seconds`` 동안 없으면 ``: keep-alive`` 코멘트 프레임을
    보내 프록시/브라우저의 idle timeout을 방지한다. (모듈 외부에서 단위 테스트
    가능하도록 endpoint 핸들러와 분리하여 정의.)
    """
    async with broker.subscribe() as queue:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except asyncio.TimeoutError:
                # SSE 사양: ":"로 시작하는 줄은 코멘트로 무시되며 연결 유지에 사용된다.
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/events")
async def stream_events() -> StreamingResponse:
    """SSE 이벤트 스트림."""
    keepalive = float(os.getenv("SSE_KEEPALIVE_SEC", "15"))
    return StreamingResponse(
        sse_event_generator(event_broker, keepalive),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
