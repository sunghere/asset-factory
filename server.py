"""Asset Factory FastAPI 서버."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from candidate_gc import run_gc_candidates
from generator import SDClient, SDError, save_candidate_slot_image, save_generated_image
from models import Database
from scanner import scan_directory
from validator import validate_asset

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "asset-factory.db"


def _allowed_roots() -> list[Path]:
    """파일 시스템 접근을 허용할 루트 디렉토리 목록.

    `ASSET_FACTORY_ALLOWED_ROOTS` 환경변수(콜론 구분, ``:``)가 있으면 그 값을 사용한다.
    기본값은 현재 ``DATA_DIR``과 사용자 워크스페이스 자산 디렉토리이다.
    호출 시점에 동적으로 계산하므로 테스트에서 ``server.DATA_DIR`` 등을
    monkeypatch 할 수 있다."""
    extra = os.getenv("ASSET_FACTORY_ALLOWED_ROOTS", "")
    roots: list[Path] = [DATA_DIR.resolve(), (Path.home() / "workspace" / "assets").resolve()]
    if extra:
        for chunk in extra.split(":"):
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
    output_dir: str = str(Path.home() / "workspace" / "assets")
    save_manifest: bool = True


class SelectCandidateRequest(BaseModel):
    """후보 슬롯 선택."""

    job_id: str
    slot_index: int = Field(ge=0)


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
sd_client = SDClient(host=os.getenv("SD_HOST", "192.168.50.225:7860"))
api_key = os.getenv("API_KEY")
worker_task: asyncio.Task[Any] | None = None
gc_worker_task: asyncio.Task[Any] | None = None


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


def _check_disk_space(path: Path) -> None:
    """생성 전 디스크 여유 공간을 검사한다."""
    min_mb = int(os.getenv("MIN_FREE_DISK_MB", "50"))
    min_free = min_mb * 1024 * 1024
    usage = shutil.disk_usage(path)
    if usage.free < min_free:
        raise RuntimeError(
            f"디스크 여유 공간이 부족합니다. 남은 용량: {usage.free // (1024 * 1024)}MB (최소 {min_mb}MB 필요)"
        )


def _ensure_disk_space_for_enqueue() -> None:
    """enqueue API에서 즉시 507 응답으로 변환되는 디스크 가드."""
    try:
        _check_disk_space(DATA_DIR)
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
        task = await db.claim_next_task()
        if task is None:
            wait = await db.soonest_due_seconds(default=1.0)
            await asyncio.sleep(min(2.0, max(0.05, wait)))
            continue
        await handle_task(task)


async def handle_task(task: dict[str, Any]) -> None:
    """생성 태스크 처리."""
    try:
        _check_disk_space(DATA_DIR)
        width, height = sd_client.choose_native_resolution(
            model_name=task.get("model_name"),
            width=task.get("width"),
            height=task.get("height"),
        )
        generation = await sd_client.txt2img(
            prompt=task["prompt"],
            negative_prompt=task.get("negative_prompt"),
            model_name=task.get("model_name"),
            width=width,
            height=height,
            steps=int(task.get("steps", 20)),
            cfg_scale=float(task.get("cfg", 7.0)),
            sampler_name=task.get("sampler") or "DPM++ 2M",
        )
        candidates_total = int(task.get("candidates_total") or 1)
        candidate_slot = task.get("candidate_slot")

        if candidates_total > 1 and candidate_slot is not None:
            output_path = save_candidate_slot_image(
                image_bytes=generation.image_bytes,
                output_root=DATA_DIR,
                project=task["project"],
                asset_key=task["asset_key"],
                job_id=task["job_id"],
                slot_index=int(candidate_slot),
            )
        else:
            # Unique 경로에 저장 → 재생성 시 이전 파일이 덮어쓰여지지 않아
            # asset_history의 image_path가 디스크에 그대로 유지된다.
            output_path = save_generated_image(
                image_bytes=generation.image_bytes,
                output_root=DATA_DIR,
                project=task["project"],
                asset_key=task["asset_key"],
                job_id=task["job_id"],
            )
        validation = validate_asset(
            image_path=output_path,
            expected_size=task.get("expected_size"),
            max_colors=int(task.get("max_colors", 32)),
        )
        metadata_json = json.dumps(
            {
                "image_format": validation.image_format,
                "model": generation.model,
                "seed": generation.seed,
                "steps": int(task.get("steps", 20)),
                "cfg": float(task.get("cfg", 7.0)),
                "sampler": task.get("sampler") or "DPM++ 2M",
                "negative_prompt": task.get("negative_prompt"),
                "max_colors": int(task.get("max_colors", 32)),
                "max_retries": int(task.get("max_retries", 3)),
                "expected_size": task.get("expected_size"),
            },
            ensure_ascii=False,
        )

        if candidates_total > 1 and candidate_slot is not None:
            await db.insert_asset_candidate(
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
                generation_seed=generation.seed,
                generation_model=generation.model,
                generation_prompt=generation.prompt,
                metadata_json=metadata_json,
            )
            # slot 인덱스 대신 "기존 primary asset 존재 여부"로 승격 판단한다.
            # 따라서 slot 0이 실패해도 다음 성공 슬롯이 primary가 될 수 있다.
            already_promoted = await db.has_asset(task["project"], task["asset_key"])
            if not already_promoted:
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
                        "generation_seed": generation.seed,
                        "generation_model": generation.model,
                        "generation_prompt": generation.prompt,
                        "metadata_json": metadata_json,
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
                    "generation_seed": generation.seed,
                    "generation_model": generation.model,
                    "generation_prompt": generation.prompt,
                    "metadata_json": metadata_json,
                },
            )
        await event_broker.publish(
            {
                "type": "task_done",
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
        await event_broker.publish(
            {
                "type": "task_error",
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
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def root() -> FileResponse:
    """SPA 엔트리."""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """기본 헬스체크."""
    return {"ok": True, "service": "asset-factory"}


@app.get("/api/health/sd")
async def health_sd() -> dict[str, Any]:
    """SD 서버 연결 헬스체크."""
    try:
        return await sd_client.health_check()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"SD 서버 연결 실패: {exc}") from exc


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


@app.get("/api/projects")
async def list_projects() -> list[dict[str, str]]:
    """specs 디렉토리의 프로젝트 스펙 목록."""
    specs_dir = BASE_DIR / "specs"
    if not specs_dir.exists():
        return []
    results: list[dict[str, str]] = []
    for file_path in sorted(specs_dir.glob("*.json")):
        results.append({"id": file_path.stem, "path": str(file_path.relative_to(BASE_DIR))})
    return results


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
) -> list[dict[str, Any]]:
    """프로젝트별 에셋 목록 (스펙/클라이언트용 정식 엔드포인트)."""
    return await db.list_assets(
        project=project_id,
        status=status,
        category=category,
        validation_status=validation_status,
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
    await event_broker.publish({"type": "job_created", "job_id": job_id})
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
    await event_broker.publish({"type": "batch_job_created", "job_id": job_id, "task_count": len(tasks)})
    return {"job_id": job_id, "project": project, "task_count": len(tasks)}


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
        {"type": "scan_completed", "project": request.project, "count": inserted, "root_path": request.root_path}
    )
    return {"project": request.project, "scanned_count": inserted}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """작업 상태 조회."""
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
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
    }


@app.get("/api/assets")
async def list_assets(
    project: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    validation_status: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """에셋 목록 조회."""
    return await db.list_assets(
        project=project,
        status=status,
        category=category,
        validation_status=validation_status,
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


@app.get("/api/jobs/recent")
async def recent_jobs(limit: int = Query(default=10, ge=1, le=100)) -> list[dict[str, Any]]:
    """최근 작업 타임라인."""
    return await db.list_recent_jobs(limit=limit)


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


@app.get("/api/asset-candidates/image")
async def get_candidate_image_file(
    project: str = Query(..., description="프로젝트 id"),
    asset_key: str = Query(...),
    job_id: str = Query(...),
    slot_index: int = Query(..., ge=0),
) -> FileResponse:
    """후보 슬롯 이미지 파일 (DB 등록 경로만 허용)."""
    rows = await db.list_asset_candidates(project, asset_key, job_id)
    pick = next((r for r in rows if int(r["slot_index"]) == slot_index), None)
    if pick is None:
        raise HTTPException(status_code=404, detail="후보를 찾을 수 없습니다.")
    safe_path = _ensure_path_allowed(Path(pick["image_path"]))
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="후보 파일이 없습니다.")
    return FileResponse(safe_path)


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
    safe_project = _safe_segment(asset["project"])
    safe_key = _safe_segment(asset["asset_key"])
    safe_job = _safe_segment(body.job_id)
    dest_dir = DATA_DIR / "candidates" / safe_project
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
    await event_broker.publish(
        {
            "type": "asset_candidate_selected",
            "asset_id": asset_id,
            "job_id": body.job_id,
            "slot_index": body.slot_index,
        }
    )
    return {"ok": True, "asset_id": asset_id, "validation_status": "pass" if validation.passed else "fail"}


@app.patch("/api/assets/{asset_id}", dependencies=[Depends(require_api_key)])
async def patch_asset(asset_id: str, request: AssetStatusPatch) -> dict[str, bool]:
    """에셋 상태 변경."""
    updated = await db.update_asset_status(asset_id=asset_id, status=request.status)
    if not updated:
        raise HTTPException(status_code=404, detail="에셋을 찾을 수 없습니다.")
    await event_broker.publish({"type": "asset_status_changed", "asset_id": asset_id, "status": request.status})
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
            "type": "asset_regenerate_queued",
            "asset_id": asset_id,
            "job_id": job_id,
            "source_seed": metadata.get("seed"),
        }
    )
    return {"job_id": job_id}


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


@app.post("/api/validate/all", dependencies=[Depends(require_api_key)])
async def validate_all_assets(project: str | None = Query(default=None)) -> dict[str, Any]:
    """전체 에셋 재검증."""
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
        {"type": "batch_revalidate_failed_done", "count": updated, "project": project}
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
        {"type": "batch_regenerate_failed_queued", "jobs": len(job_ids), "project": project}
    )
    return {"queued_jobs": len(job_ids), "job_ids": job_ids, "project": project}


@app.post("/api/export", dependencies=[Depends(require_api_key)])
async def export_assets(request: ExportRequest) -> dict[str, Any]:
    """승인된 에셋을 대상 디렉토리로 복사한다."""
    approved = await db.list_approved_assets(project=request.project)
    if not approved:
        return {"exported_count": 0, "output_dir": request.output_dir}

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
        for asset in approved:
            image_path = Path(asset["image_path"])
            sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
            manifest_items.append(
                {
                    "project": asset["project"],
                    "asset_key": asset["asset_key"],
                    "category": asset["category"],
                    "path": str(output_root / asset["project"] / asset["category"] / f"{asset['asset_key']}.png"),
                    "width": asset["width"],
                    "height": asset["height"],
                    "sha256": sha256,
                }
            )
        manifest_data = {"count": len(manifest_items), "items": manifest_items}
        manifest_file = output_root / "asset-manifest.json"
        manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_path = str(manifest_file)

    await event_broker.publish(
        {"type": "export_completed", "count": exported_count, "output_dir": str(output_root), "manifest_path": manifest_path}
    )
    return {"exported_count": exported_count, "output_dir": str(output_root), "manifest_path": manifest_path}


@app.get("/api/export/manifest")
async def export_manifest(project: str | None = None) -> dict[str, Any]:
    """승인본 기준 manifest를 반환한다."""
    approved = await db.list_approved_assets(project=project)
    items: list[dict[str, Any]] = []
    for asset in approved:
        image_path = Path(asset["image_path"])
        sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path.exists() else None
        items.append(
            {
                "project": asset["project"],
                "asset_key": asset["asset_key"],
                "category": asset["category"],
                "path": asset["image_path"],
                "width": asset["width"],
                "height": asset["height"],
                "sha256": sha256,
            }
        )
    return {"count": len(items), "items": items}


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
