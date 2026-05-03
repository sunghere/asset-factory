"""Asset Factory SQLite 모델/쿼리 레이어."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def utc_now() -> str:
    """UTC ISO8601 문자열 반환."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_asset_row(row: dict[str, Any]) -> dict[str, Any]:
    """assets 테이블 SELECT 결과 dict 의 응답용 보강.

    ``approval_mode`` 컬럼이 dict 에 없거나 NULL 이면 'manual' 로 채운다 —
    마이그레이션이 아직 안 돈 환경 (예: 머지 직후 첫 startup 전) 에서도 클라이
    언트가 키를 안전하게 읽을 수 있게.
    """
    if "approval_mode" not in row or row["approval_mode"] is None:
        row["approval_mode"] = "manual"
    return row


@dataclass(slots=True)
class JobRecord:
    """작업 레코드."""

    id: str
    job_type: str
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    error_message: str | None
    payload_json: str | None
    created_at: str
    updated_at: str


class Database:
    """SQLite 접근을 담당하는 얇은 저장소 클래스."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """필요 테이블을 생성한다."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    project TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    image_path TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    color_count INTEGER NOT NULL,
                    has_alpha INTEGER NOT NULL DEFAULT 0,
                    validation_status TEXT NOT NULL DEFAULT 'pending',
                    validation_message TEXT,
                    generation_seed INTEGER,
                    generation_model TEXT,
                    generation_prompt TEXT,
                    metadata_json TEXT,
                    approval_mode TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project, asset_key)
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT,
                    model_name TEXT,
                    width INTEGER,
                    height INTEGER,
                    steps INTEGER NOT NULL DEFAULT 20,
                    cfg REAL NOT NULL DEFAULT 7.0,
                    sampler TEXT NOT NULL DEFAULT 'DPM++ 2M',
                    status TEXT NOT NULL DEFAULT 'queued',
                    retries INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    last_error TEXT,
                    expected_size INTEGER,
                    max_colors INTEGER NOT NULL DEFAULT 32,
                    candidate_slot INTEGER,
                    candidates_total INTEGER,
                    next_attempt_at TEXT,
                    batch_id TEXT,
                    lora_spec_json TEXT,
                    seed INTEGER,
                    backend TEXT NOT NULL DEFAULT 'a1111',
                    workflow_category TEXT,
                    workflow_variant TEXT,
                    workflow_params_json TEXT,
                    approval_mode TEXT NOT NULL DEFAULT 'manual',
                    -- §1.B subject-injection: PromptResolution.to_dict() JSON.
                    -- legacy 모드 task 도 mode='legacy' + final_positive=prompt 형태로 저장
                    -- (호출 시 항상 채움). NULL 은 legacy DB row 만 — /api/jobs/{id} 응답에서
                    -- 재구성하지 않고 None 으로 노출.
                    prompt_resolution_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )
            await conn.commit()
        # 인덱스 생성은 컬럼이 보장된 뒤에 한다 (구 DB 마이그레이션 호환).
        await self._migrate_legacy_schema()

    async def _migrate_legacy_schema(self) -> None:
        """기존 DB에 누락된 컬럼/테이블을 추가한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("PRAGMA table_info(generation_tasks)")
            rows = await cursor.fetchall()
            col_names = {row[1] for row in rows}
            if "candidate_slot" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN candidate_slot INTEGER")
            if "candidates_total" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN candidates_total INTEGER")
            if "next_attempt_at" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN next_attempt_at TEXT")
            if "batch_id" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN batch_id TEXT")
            if "lora_spec_json" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN lora_spec_json TEXT")
            if "seed" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN seed INTEGER")
            # ComfyUI 백엔드 도입 (Phase 2). 기존 행은 backend='a1111' 로 간주됨
            # (claim_next_task 가 NULL 도 a1111 로 디스패치).
            if "backend" not in col_names:
                await conn.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN backend TEXT NOT NULL DEFAULT 'a1111'"
                )
            if "workflow_category" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN workflow_category TEXT")
            if "workflow_variant" not in col_names:
                await conn.execute("ALTER TABLE generation_tasks ADD COLUMN workflow_variant TEXT")
            if "workflow_params_json" not in col_names:
                await conn.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN workflow_params_json TEXT"
                )
            # Bypass(승인 우회) 모드. 'manual' (default) 또는 'bypass'.
            # bypass 행은 cherry-pick UI 에 노출되지 않고 export manifest 에서도 제외.
            if "approval_mode" not in col_names:
                await conn.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'manual'"
                )
            # §1.B subject-injection — PromptResolution JSON. NULL 은 legacy DB row.
            if "prompt_resolution_json" not in col_names:
                await conn.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN prompt_resolution_json TEXT"
                )
            if "started_at" not in col_names:
                await conn.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN started_at TEXT"
                )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_batch ON generation_tasks(batch_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_backend ON generation_tasks(backend)"
            )

            # assets 테이블에 approval_mode 추가 (bypass 격리용)
            asset_cursor = await conn.execute("PRAGMA table_info(assets)")
            asset_rows = await asset_cursor.fetchall()
            asset_cols = {row[1] for row in asset_rows}
            if "approval_mode" not in asset_cols:
                await conn.execute(
                    "ALTER TABLE assets ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'manual'"
                )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_assets_approval ON assets(approval_mode, project)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    job_id TEXT,
                    image_path TEXT NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    color_count INTEGER,
                    has_alpha INTEGER,
                    validation_status TEXT,
                    validation_message TEXT,
                    generation_seed INTEGER,
                    generation_model TEXT,
                    generation_prompt TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    job_id TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    color_count INTEGER NOT NULL,
                    validation_status TEXT NOT NULL,
                    validation_message TEXT,
                    generation_seed INTEGER,
                    generation_model TEXT,
                    generation_prompt TEXT,
                    metadata_json TEXT,
                    batch_id TEXT,
                    is_rejected INTEGER NOT NULL DEFAULT 0,
                    picked_at TEXT,
                    picked_asset_id TEXT,
                    approval_mode TEXT NOT NULL DEFAULT 'manual',
                    created_at TEXT NOT NULL,
                    UNIQUE(project, asset_key, slot_index, job_id)
                )
                """
            )
            cand_cursor = await conn.execute("PRAGMA table_info(asset_candidates)")
            cand_rows = await cand_cursor.fetchall()
            cand_cols = {row[1] for row in cand_rows}
            if "batch_id" not in cand_cols:
                await conn.execute("ALTER TABLE asset_candidates ADD COLUMN batch_id TEXT")
            if "is_rejected" not in cand_cols:
                await conn.execute(
                    "ALTER TABLE asset_candidates ADD COLUMN is_rejected INTEGER NOT NULL DEFAULT 0"
                )
            if "picked_at" not in cand_cols:
                await conn.execute(
                    "ALTER TABLE asset_candidates ADD COLUMN picked_at TEXT"
                )
            if "picked_asset_id" not in cand_cols:
                await conn.execute(
                    "ALTER TABLE asset_candidates ADD COLUMN picked_asset_id TEXT"
                )
            if "approval_mode" not in cand_cols:
                await conn.execute(
                    "ALTER TABLE asset_candidates ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'manual'"
                )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_batch ON asset_candidates(batch_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_picked_asset ON asset_candidates(picked_asset_id)"
            )
            await conn.commit()

    async def create_job(
        self,
        job_id: str,
        job_type: str,
        payload: dict[str, Any] | None,
    ) -> None:
        """새 작업을 생성한다."""
        now = utc_now()
        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO jobs (
                    id, job_type, status, total_count, completed_count, failed_count,
                    error_message, payload_json, created_at, updated_at
                )
                VALUES (?, ?, 'queued', 0, 0, 0, NULL, ?, ?, ?)
                """,
                (job_id, job_type, payload_json, now, now),
            )
            await conn.commit()

    async def enqueue_generation_task(self, task: dict[str, Any]) -> int:
        """생성 태스크를 큐에 추가한다."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO generation_tasks (
                    job_id, project, asset_key, category, prompt, negative_prompt,
                    model_name, width, height, steps, cfg, sampler, status,
                    retries, max_retries, last_error, expected_size, max_colors,
                    candidate_slot, candidates_total,
                    batch_id, lora_spec_json, seed,
                    backend, workflow_category, workflow_variant, workflow_params_json,
                    approval_mode, prompt_resolution_json,
                    created_at, updated_at
                )
                VALUES (
                    :job_id, :project, :asset_key, :category, :prompt, :negative_prompt,
                    :model_name, :width, :height, :steps, :cfg, :sampler, 'queued',
                    0, :max_retries, NULL, :expected_size, :max_colors,
                    :candidate_slot, :candidates_total,
                    :batch_id, :lora_spec_json, :seed,
                    :backend, :workflow_category, :workflow_variant, :workflow_params_json,
                    :approval_mode, :prompt_resolution_json,
                    :created_at, :updated_at
                )
                """,
                {
                    **task,
                    "candidate_slot": task.get("candidate_slot"),
                    "candidates_total": task.get("candidates_total"),
                    "batch_id": task.get("batch_id"),
                    "lora_spec_json": task.get("lora_spec_json"),
                    "seed": task.get("seed"),
                    # backend 컬럼은 NOT NULL DEFAULT 'a1111' — task dict 가 안 줘도 디폴트.
                    "backend": task.get("backend") or "a1111",
                    "workflow_category": task.get("workflow_category"),
                    "workflow_variant": task.get("workflow_variant"),
                    "workflow_params_json": task.get("workflow_params_json"),
                    "approval_mode": task.get("approval_mode") or "manual",
                    # §1.B prompt_resolution JSON. legacy DB row 호환 위해 None 허용.
                    "prompt_resolution_json": task.get("prompt_resolution_json"),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await conn.execute(
                """
                UPDATE jobs
                SET total_count = total_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, task["job_id"]),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def mark_job_running(self, job_id: str) -> None:
        """작업 상태를 running으로 변경."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE jobs SET status='running', updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
            await conn.commit()

    async def claim_next_task(self) -> dict[str, Any] | None:
        """다음 queued 태스크를 processing으로 점유한다.

        ``next_attempt_at``이 미래인 태스크는 백오프 대기 중이므로 건너뛴다.
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM generation_tasks
                WHERE status='queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (now,),
            )
            row = await cursor.fetchone()
            if row is None:
                await conn.rollback()
                return None

            await conn.execute(
                """
                UPDATE generation_tasks
                SET status='processing', updated_at=?, started_at=?
                WHERE id=?
                """,
                (utc_now(), utc_now(), row["id"]),
            )
            await conn.execute(
                """
                UPDATE jobs
                SET status='running', updated_at=?
                WHERE id=?
                """,
                (utc_now(), row["job_id"]),
            )
            await conn.commit()
            return dict(row)

    async def find_and_requeue_stuck_tasks(self, timeout_seconds: int) -> list[dict[str, Any]]:
        """processing 상태로 timeout_seconds 이상 멈춘 태스크를 재큐잉/실패 처리."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        now = utc_now()
        result: list[dict[str, Any]] = []

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM generation_tasks
                WHERE status='processing'
                  AND started_at IS NOT NULL
                  AND started_at < ?
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                task = dict(row)
                retries = int(task.get("retries") or 0)
                max_retries = int(task.get("max_retries") or 3)
                if retries < max_retries:
                    await conn.execute(
                        """
                        UPDATE generation_tasks
                        SET status='queued', retries=retries+1, updated_at=?,
                            next_attempt_at=?, started_at=NULL
                        WHERE id=?
                        """,
                        (now, now, task["id"]),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE generation_tasks
                        SET status='failed', last_error='stuck: completion_timeout exceeded',
                            updated_at=?
                        WHERE id=?
                        """,
                        (now, task["id"]),
                    )
                result.append(task)
            await conn.commit()
        return result

    async def finish_task_success(
        self,
        task_id: int,
        job_id: str,
        asset: dict[str, Any],
    ) -> None:
        """태스크 성공 처리 및 결과 에셋 저장."""
        now = utc_now()
        payload = {
            **asset,
            "has_alpha": 1 if asset["has_alpha"] else 0,
            "created_at": now,
            "updated_at": now,
        }
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM assets WHERE project=? AND asset_key=?",
                (asset["project"], asset["asset_key"]),
            )
            existing = await cur.fetchone()
            if existing:
                ex = dict(existing)
                ver_cur = await conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM asset_history WHERE asset_id=?",
                    (ex["id"],),
                )
                ver_row = await ver_cur.fetchone()
                next_version = int(ver_row[0] if ver_row else 0) + 1
                await conn.execute(
                    """
                    INSERT INTO asset_history (
                        asset_id, version, job_id, image_path, width, height,
                        color_count, has_alpha, validation_status, validation_message,
                        generation_seed, generation_model, generation_prompt,
                        metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ex["id"],
                        next_version,
                        ex.get("job_id"),
                        ex["image_path"],
                        ex["width"],
                        ex["height"],
                        ex["color_count"],
                        ex["has_alpha"],
                        ex["validation_status"],
                        ex.get("validation_message"),
                        ex.get("generation_seed"),
                        ex.get("generation_model"),
                        ex.get("generation_prompt"),
                        ex.get("metadata_json"),
                        now,
                    ),
                )
                payload["id"] = ex["id"]
                payload["created_at"] = ex["created_at"]

            await conn.execute(
                """
                UPDATE generation_tasks
                SET status='done', updated_at=?
                WHERE id=?
                """,
                (now, task_id),
            )
            await conn.execute(
                """
                UPDATE jobs
                SET completed_count = completed_count + 1, updated_at=?
                WHERE id=?
                """,
                (now, job_id),
            )
            payload.setdefault("approval_mode", "manual")
            await conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status, image_path, width, height,
                    color_count, has_alpha, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt,
                    metadata_json, approval_mode, created_at, updated_at
                )
                VALUES (
                    :id, :job_id, :project, :asset_key, :category, :status, :image_path, :width, :height,
                    :color_count, :has_alpha, :validation_status, :validation_message,
                    :generation_seed, :generation_model, :generation_prompt,
                    :metadata_json, :approval_mode, :created_at, :updated_at
                )
                ON CONFLICT(project, asset_key) DO UPDATE SET
                    job_id=excluded.job_id,
                    status=excluded.status,
                    image_path=excluded.image_path,
                    width=excluded.width,
                    height=excluded.height,
                    color_count=excluded.color_count,
                    has_alpha=excluded.has_alpha,
                    validation_status=excluded.validation_status,
                    validation_message=excluded.validation_message,
                    generation_seed=excluded.generation_seed,
                    generation_model=excluded.generation_model,
                    generation_prompt=excluded.generation_prompt,
                    metadata_json=excluded.metadata_json,
                    approval_mode=excluded.approval_mode,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
            await conn.commit()
        await self.refresh_job_status(job_id)

    async def finish_generation_task_only(self, task_id: int, job_id: str) -> None:
        """생성 태스크만 완료 처리(에셋 테이블은 건드리지 않음)."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                UPDATE generation_tasks
                SET status='done', updated_at=?
                WHERE id=?
                """,
                (now, task_id),
            )
            await conn.execute(
                """
                UPDATE jobs
                SET completed_count = completed_count + 1, updated_at=?
                WHERE id=?
                """,
                (now, job_id),
            )
            await conn.commit()
        await self.refresh_job_status(job_id)

    async def retry_or_fail_task(
        self,
        task: dict[str, Any],
        error_message: str,
        *,
        force_fail: bool = False,
        next_attempt_at: str | None = None,
    ) -> None:
        """실패 태스크를 재시도하거나 최종 실패 처리.

        ``force_fail=True``이면 남은 retry가 있어도 즉시 failed로 처리한다.
        ``next_attempt_at``이 주어지면 해당 시각 전까지는 worker가 점유하지 않는다."""
        now = utc_now()
        retries = int(task["retries"])
        max_retries = int(task["max_retries"])
        async with aiosqlite.connect(self.db_path) as conn:
            if not force_fail and retries + 1 < max_retries:
                await conn.execute(
                    """
                    UPDATE generation_tasks
                    SET status='queued', retries=?, last_error=?, updated_at=?, next_attempt_at=?
                    WHERE id=?
                    """,
                    (retries + 1, error_message, now, next_attempt_at, task["id"]),
                )
            else:
                await conn.execute(
                    """
                    UPDATE generation_tasks
                    SET status='failed', retries=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (retries + 1, error_message, now, task["id"]),
                )
                await conn.execute(
                    """
                    UPDATE jobs
                    SET failed_count = failed_count + 1, updated_at=?
                    WHERE id=?
                    """,
                    (now, task["job_id"]),
                )
            await conn.commit()
        await self.refresh_job_status(task["job_id"])

    async def refresh_job_status(self, job_id: str) -> None:
        """태스크 집계를 기준으로 작업 상태를 정리한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_count,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN status IN ('queued', 'processing') THEN 1 ELSE 0 END) AS active_count
                FROM generation_tasks
                WHERE job_id=?
                """,
                (job_id,),
            )
            counts = await cursor.fetchone()
            if counts is None:
                return

            total = int(counts["total"] or 0)
            done_count = int(counts["done_count"] or 0)
            failed_count = int(counts["failed_count"] or 0)
            active_count = int(counts["active_count"] or 0)

            if total > 0 and active_count == 0:
                status = "completed" if failed_count == 0 else "completed_with_errors"
            elif active_count > 0:
                status = "running"
            else:
                status = "queued"

            await conn.execute(
                """
                UPDATE jobs
                SET status=?, total_count=?, completed_count=?, failed_count=?, updated_at=?
                WHERE id=?
                """,
                (status, total, done_count, failed_count, utc_now(), job_id),
            )
            await conn.commit()

    async def get_job(self, job_id: str) -> JobRecord | None:
        """작업 단건 조회."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return JobRecord(**dict(row))

    async def get_first_task_prompt_resolution(
        self, job_id: str
    ) -> dict[str, Any] | None:
        """job 의 첫 task 의 prompt_resolution_json 을 dict 로 반환.

        §1.B — `/api/jobs/{id}` 응답에 `prompt_resolution` 노출용. 모든 task 가
        같은 resolution 을 공유 (cherry-pick N 후보도 prompt 는 동일, seed 만
        다름). NULL 또는 legacy DB row 면 None.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT prompt_resolution_json FROM generation_tasks "
                "WHERE job_id=? ORDER BY id ASC LIMIT 1",
                (job_id,),
            )
            row = await cursor.fetchone()
            if row is None or row[0] is None:
                return None
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return None

    async def list_assets(
        self,
        project: str | None = None,
        status: str | None = None,
        category: str | None = None,
        validation_status: str | None = None,
        include_bypassed: bool = False,
    ) -> list[dict[str, Any]]:
        """필터 기반 에셋 목록 조회.

        ``include_bypassed=False`` (기본) 면 ``approval_mode='bypass'`` 자산은 제외 —
        bypass 는 의도된 임시물이라 일반 list/UI 에서 안 보이게.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if project:
            conditions.append("project=?")
            params.append(project)
        if status:
            conditions.append("status=?")
            params.append(status)
        if category:
            conditions.append("category=?")
            params.append(category)
        if validation_status:
            conditions.append("validation_status=?")
            params.append(validation_status)
        if not include_bypassed:
            conditions.append("approval_mode != 'bypass'")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT *
            FROM assets
            {where_clause}
            ORDER BY updated_at DESC
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [_normalize_asset_row(dict(row)) for row in rows]

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        """에셋 단건 조회."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,))
            row = await cursor.fetchone()
            return _normalize_asset_row(dict(row)) if row else None

    async def has_asset(self, project: str, asset_key: str) -> bool:
        """동일 (project, asset_key) 에셋이 이미 존재하는지 확인."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM assets WHERE project=? AND asset_key=? LIMIT 1",
                (project, asset_key),
            )
            row = await cursor.fetchone()
            return row is not None

    async def get_asset_by_key(self, project: str, asset_key: str) -> dict[str, Any] | None:
        """``(project, asset_key)`` 로 에셋 한 건 조회."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM assets WHERE project=? AND asset_key=? LIMIT 1",
                (project, asset_key),
            )
            row = await cursor.fetchone()
            return _normalize_asset_row(dict(row)) if row else None

    async def soonest_due_seconds(self, *, default: float = 1.0) -> float:
        """다음 실행 가능한 queued 태스크까지 남은 초.

        - 즉시 실행 가능한 태스크가 있으면 0.0
        - queued 태스크가 없으면 ``default``
        - 모두 미래에 due이면 (가장 이른 due - now) 초를 양수로 반환
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT MIN(
                    CASE WHEN next_attempt_at IS NULL THEN '0000-00-00'
                         ELSE next_attempt_at END
                ) AS soonest
                FROM generation_tasks
                WHERE status='queued'
                """
            )
            row = await cursor.fetchone()
            if row is None or row["soonest"] is None:
                return default
            soonest = str(row["soonest"])
            if soonest == "0000-00-00":
                return 0.0
            try:
                due_at = datetime.fromisoformat(soonest)
            except ValueError:
                return default
            now = datetime.now(timezone.utc)
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            delta = (due_at - now).total_seconds()
            return max(0.0, delta)

    async def recover_orphan_tasks(self) -> int:
        """이전 실행 중 'processing' 상태로 멈춘 태스크를 다시 큐로 되돌린다."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE generation_tasks
                SET status='queued', updated_at=?
                WHERE status='processing'
                """,
                (now,),
            )
            await conn.commit()
            return int(cursor.rowcount or 0)

    async def update_asset_validation(
        self,
        asset_id: str,
        *,
        width: int,
        height: int,
        color_count: int,
        has_alpha: bool,
        validation_status: str,
        validation_message: str | None,
    ) -> bool:
        """재검증 결과로 에셋 메타/검증 필드만 갱신한다."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE assets SET
                    width=?,
                    height=?,
                    color_count=?,
                    has_alpha=?,
                    validation_status=?,
                    validation_message=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    width,
                    height,
                    color_count,
                    1 if has_alpha else 0,
                    validation_status,
                    validation_message,
                    now,
                    asset_id,
                ),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def update_asset_status(self, asset_id: str, status: str) -> bool:
        """에셋 상태 업데이트."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE assets
                SET status=?, updated_at=?
                WHERE id=?
                """,
                (status, utc_now(), asset_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def upsert_scanned_asset(
        self,
        *,
        project: str,
        asset_key: str,
        category: str,
        image_path: str,
        width: int,
        height: int,
        color_count: int,
        has_alpha: bool,
        validation_status: str,
        validation_message: str,
    ) -> None:
        """스캔된 에셋을 upsert한다."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status, image_path, width, height,
                    color_count, has_alpha, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt, metadata_json,
                    created_at, updated_at
                )
                VALUES (?, NULL, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                ON CONFLICT(project, asset_key) DO UPDATE SET
                    category=excluded.category,
                    image_path=excluded.image_path,
                    width=excluded.width,
                    height=excluded.height,
                    color_count=excluded.color_count,
                    has_alpha=excluded.has_alpha,
                    validation_status=excluded.validation_status,
                    validation_message=excluded.validation_message,
                    updated_at=excluded.updated_at
                """,
                (
                    f"{project}:{asset_key}",
                    project,
                    asset_key,
                    category,
                    image_path,
                    width,
                    height,
                    color_count,
                    1 if has_alpha else 0,
                    validation_status,
                    validation_message,
                    now,
                    now,
                ),
            )
            await conn.commit()

    async def list_approved_assets(
        self,
        project: str | None = None,
        category: str | None = None,
        since: str | None = None,
        include_bypassed: bool = False,
    ) -> list[dict[str, Any]]:
        """승인된 에셋 목록을 반환한다.

        ``project`` · ``category`` 는 단순 equality, ``since`` 는 ISO8601 문자열로
        ``updated_at >= since`` (없으면 ``created_at``) 비교한다.

        ``include_bypassed=False`` (기본) 면 ``approval_mode='bypass'`` 자산은 제외 —
        export manifest 에서 임시물이 빠지게 하는 게 의도.
        """
        query = "SELECT * FROM assets WHERE status='approved'"
        params: list[Any] = []
        if project:
            query += " AND project=?"
            params.append(project)
        if category:
            query += " AND category=?"
            params.append(category)
        if since:
            query += " AND COALESCE(updated_at, created_at) >= ?"
            params.append(since)
        if not include_bypassed:
            query += " AND approval_mode != 'bypass'"
        query += " ORDER BY asset_key ASC"

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [_normalize_asset_row(dict(row)) for row in rows]

    async def get_asset_summary(self, project: str | None = None) -> dict[str, Any]:
        """에셋 상태/검증/카테고리 집계를 반환한다."""
        conditions: list[str] = []
        params: list[Any] = []
        if project:
            conditions.append("project=?")
            params.append(project)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

            total_cursor = await conn.execute(
                f"SELECT COUNT(*) AS total FROM assets {where_clause}",
                params,
            )
            total_row = await total_cursor.fetchone()

            status_cursor = await conn.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM assets
                {where_clause}
                GROUP BY status
                """,
                params,
            )
            status_rows = await status_cursor.fetchall()

            validation_cursor = await conn.execute(
                f"""
                SELECT validation_status, COUNT(*) AS count
                FROM assets
                {where_clause}
                GROUP BY validation_status
                """,
                params,
            )
            validation_rows = await validation_cursor.fetchall()

            category_cursor = await conn.execute(
                f"""
                SELECT category, COUNT(*) AS count
                FROM assets
                {where_clause}
                GROUP BY category
                ORDER BY count DESC
                """,
                params,
            )
            category_rows = await category_cursor.fetchall()

        return {
            "total": int((total_row or {"total": 0})["total"] or 0),
            "by_status": {str(row["status"]): int(row["count"]) for row in status_rows},
            "by_validation": {str(row["validation_status"]): int(row["count"]) for row in validation_rows},
            "by_category": {str(row["category"]): int(row["count"]) for row in category_rows},
        }

    async def list_recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        """최근 작업 목록을 반환한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT id, job_type, status, total_count, completed_count, failed_count, created_at, updated_at
                FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_asset_history(self, asset_id: str) -> list[dict[str, Any]]:
        """에셋 교체 이력(이전 버전 스냅샷)을 버전 내림차순으로 반환한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM asset_history
                WHERE asset_id=?
                ORDER BY version DESC
                """,
                (asset_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def insert_asset_candidate(
        self,
        *,
        project: str,
        asset_key: str,
        slot_index: int,
        job_id: str,
        image_path: str,
        width: int,
        height: int,
        color_count: int,
        validation_status: str,
        validation_message: str | None,
        generation_seed: int | None,
        generation_model: str | None,
        generation_prompt: str | None,
        metadata_json: str | None,
        batch_id: str | None = None,
        approval_mode: str = "manual",
    ) -> int:
        """후보 이미지 한 슬롯을 저장한다. INSERT OR REPLACE."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO asset_candidates (
                    project, asset_key, slot_index, job_id, image_path,
                    width, height, color_count, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt, metadata_json,
                    batch_id, is_rejected, approval_mode, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(project, asset_key, slot_index, job_id) DO UPDATE SET
                    image_path=excluded.image_path,
                    width=excluded.width,
                    height=excluded.height,
                    color_count=excluded.color_count,
                    validation_status=excluded.validation_status,
                    validation_message=excluded.validation_message,
                    generation_seed=excluded.generation_seed,
                    generation_model=excluded.generation_model,
                    generation_prompt=excluded.generation_prompt,
                    metadata_json=excluded.metadata_json,
                    batch_id=excluded.batch_id,
                    approval_mode=excluded.approval_mode,
                    created_at=excluded.created_at
                """,
                (
                    project,
                    asset_key,
                    slot_index,
                    job_id,
                    image_path,
                    width,
                    height,
                    color_count,
                    validation_status,
                    validation_message,
                    generation_seed,
                    generation_model,
                    generation_prompt,
                    metadata_json,
                    batch_id,
                    approval_mode,
                    now,
                ),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def list_batch_candidates(self, batch_id: str) -> list[dict[str, Any]]:
        """한 batch에 속한 모든 후보를 시간순으로 반환한다.

        ``approval_mode='bypass'`` 후보는 cherry-pick UI 에 노출되지 않게 항상 제외.
        bypass batch 결과는 별도로 `list_bypass_candidates(batch_id)` 로 조회.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM asset_candidates
                WHERE batch_id=? AND approval_mode != 'bypass'
                ORDER BY is_rejected ASC, created_at ASC, slot_index ASC
                """,
                (batch_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_batch_detail(self, batch_id: str) -> dict[str, Any] | None:
        """단일 design batch 상세 + spec 재구성.

        배치의 원 스펙(seeds × models × prompts × loras)은 enqueue 시점에 태스크
        별로 쪼개져 저장됐으므로, ``generation_tasks`` 의 distinct 값으로
        재조립한다. candidate 쪽 통계(rejected / picked / validation)는 별도
        쿼리로 붙인다. 존재하지 않는 batch 는 ``None``.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            # 태스크 행(모든 컬럼) — 여기서 spec 재조립
            cur = await conn.execute(
                """
                SELECT project, asset_key, category, job_id, prompt, negative_prompt,
                       model_name, lora_spec_json, seed, steps, cfg, sampler,
                       width, height, expected_size, max_colors, max_retries,
                       status, created_at, updated_at
                FROM generation_tasks
                WHERE batch_id=?
                """,
                (batch_id,),
            )
            task_rows = [dict(r) for r in await cur.fetchall()]
            if not task_rows:
                return None
            cur = await conn.execute(
                """
                SELECT validation_status, is_rejected, picked_at
                FROM asset_candidates
                WHERE batch_id=?
                """,
                (batch_id,),
            )
            cand_rows = [dict(r) for r in await cur.fetchall()]

        # Task-level aggregates
        tasks = {
            "total": len(task_rows),
            "done": sum(1 for t in task_rows if t["status"] == "done"),
            "failed": sum(1 for t in task_rows if t["status"] == "failed"),
            "active": sum(1 for t in task_rows if t["status"] in ("queued", "processing")),
        }

        # Candidate-level aggregates
        validation: dict[str, int] = {"pass": 0, "fail": 0, "pending": 0}
        for c in cand_rows:
            v = c.get("validation_status") or "pending"
            validation[v] = validation.get(v, 0) + 1
        candidates = {
            "total": len(cand_rows),
            "rejected": sum(1 for c in cand_rows if c.get("is_rejected")),
            "picked": sum(1 for c in cand_rows if c.get("picked_at")),
            "validation": validation,
        }

        # Spec reconstruction — distinct values across all tasks
        def _distinct(key: str) -> list[Any]:
            seen: list[Any] = []
            for t in task_rows:
                v = t.get(key)
                if v is None:
                    # None 은 한 번만 대표로 보여줌 (seeds 에 None 이 섞인 건 랜덤)
                    if None not in seen:
                        seen.append(None)
                    continue
                if v not in seen:
                    seen.append(v)
            return seen

        # metadata_json 안에 섞여있는 기타 값 대신 컬럼에서 직접 꺼낸다.
        # cfg/steps/sampler/max_colors 등은 배치 내에서 보통 uniform 이므로
        # "common" 섹션에 대표 1값 + 비균일 flag 를 둔다.
        def _common(key: str) -> dict[str, Any]:
            vals = _distinct(key)
            return {"value": vals[0] if vals else None, "uniform": len(vals) <= 1}

        first = task_rows[0]
        spec = {
            "seeds": _distinct("seed"),
            "models": _distinct("model_name"),
            "prompts": _distinct("prompt"),
            "negative_prompts": _distinct("negative_prompt"),
            "loras": _distinct("lora_spec_json"),
            "common": {
                "steps": _common("steps"),
                "cfg": _common("cfg"),
                "sampler": _common("sampler"),
                "width": _common("width"),
                "height": _common("height"),
                "expected_size": _common("expected_size"),
                "max_colors": _common("max_colors"),
                "max_retries": _common("max_retries"),
            },
            # 실제 expand 결과 (task 수). 이론상 len(seeds)*len(models)*len(prompts)*len(loras)
            # 와 같아야 하지만 enqueue 시 중복이 생겼을 수 있으므로 관측값을 그대로 보고한다.
            "task_count": len(task_rows),
        }

        first_created = min((t["created_at"] for t in task_rows), default=None)
        last_updated = max((t["updated_at"] for t in task_rows), default=None)

        return {
            "batch_id": batch_id,
            "project": first["project"],
            "asset_key": first["asset_key"],
            "category": first["category"],
            "job_id": first["job_id"],
            "first_created_at": first_created,
            "last_updated_at": last_updated,
            "tasks": tasks,
            "candidates": candidates,
            "spec": spec,
        }

    async def system_stats(self) -> dict[str, Any]:
        """System.jsx DB 블록용 row count / sqlite 파일 메타.

        파일 크기/경로는 server.py 측에서 주입하고, 여기서는 순수 DB 쿼리만 한다.
        """
        out: dict[str, Any] = {}
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            for table in ("jobs", "generation_tasks", "asset_candidates", "assets"):
                cur = await conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
                row = await cur.fetchone()
                out[table] = int(row["n"] or 0) if row else 0
            # Worker 가 현재 붙잡고 있을 수 있는 태스크 / 큐 깊이 등
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM generation_tasks "
                "WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)",
                (utc_now(),),
            )
            row = await cur.fetchone()
            out["queued_due"] = int(row["n"] or 0) if row else 0
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM generation_tasks WHERE status='queued'"
            )
            row = await cur.fetchone()
            out["queued_total"] = int(row["n"] or 0) if row else 0
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM generation_tasks WHERE status='processing'"
            )
            row = await cur.fetchone()
            out["processing"] = int(row["n"] or 0) if row else 0
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM generation_tasks WHERE status='failed'"
            )
            row = await cur.fetchone()
            out["failed"] = int(row["n"] or 0) if row else 0
        return out

    async def aggregate_catalog_usage(self) -> dict[str, Any]:
        """Catalog 카드에 노출할 모델/LoRA 사용 통계.

        ``generation_tasks`` 전체를 스캔해서 다음을 집계한다:

        * ``models`` — ``model_name`` 별 ``task_count`` · ``batch_count`` ·
          ``last_used_at`` (가장 최근 ``updated_at``).
        * ``loras`` — ``lora_spec_json`` 을 파싱해 각 ``name`` 별 동일 통계.

        집계는 프로세스 메모리에서 이뤄지므로 수십만 row 까지는 단순 집계로 버틴다
        (운영 체감 · v0.2 규모). 그 이상이면 인덱스/마테리얼라이즈 고려.
        """
        models: dict[str, dict[str, Any]] = {}
        loras: dict[str, dict[str, Any]] = {}

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT model_name, lora_spec_json, batch_id, updated_at
                FROM generation_tasks
                """
            )
            rows = await cur.fetchall()

        def _bump(bucket: dict[str, dict[str, Any]], name: str, batch_id: str | None, updated_at: str | None) -> None:
            slot = bucket.setdefault(
                name, {"task_count": 0, "batch_ids": set(), "last_used_at": None}
            )
            slot["task_count"] += 1
            if batch_id:
                slot["batch_ids"].add(batch_id)
            if updated_at and (slot["last_used_at"] is None or updated_at > slot["last_used_at"]):
                slot["last_used_at"] = updated_at

        for r in rows:
            name = r["model_name"]
            if name:
                _bump(models, str(name), r["batch_id"], r["updated_at"])
            raw = r["lora_spec_json"]
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, list):
                continue
            # 고유 lora 이름만 집계 (같은 task 가 동일 lora 를 중복 언급해도 1회).
            seen_names: set[str] = set()
            for spec in data:
                if isinstance(spec, dict):
                    nm = spec.get("name")
                elif isinstance(spec, str):
                    nm = spec
                else:
                    nm = None
                if isinstance(nm, str) and nm and nm not in seen_names:
                    seen_names.add(nm)
                    _bump(loras, nm, r["batch_id"], r["updated_at"])

        def _finalize(bucket: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for k, v in bucket.items():
                out[k] = {
                    "task_count": v["task_count"],
                    "batch_count": len(v["batch_ids"]),
                    "last_used_at": v["last_used_at"],
                }
            return out

        return {"models": _finalize(models), "loras": _finalize(loras)}

    async def list_batches_using_catalog(
        self,
        *,
        model_name: str | None = None,
        lora_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """특정 model/LoRA 를 사용한 최근 batch 역참조 목록.

        Catalog 상세 패널의 "최근 배치" 리스트에서 사용. ``model_name`` 은
        ``generation_tasks.model_name`` 과 정확 일치, ``lora_name`` 은
        ``lora_spec_json`` 을 JSON 파싱해서 ``name`` 필드를 비교한다.

        과거에는 문자열 LIKE(``"name": "foo"``)에 의존했는데, 이 방식은
        compact JSON(``{"name":"foo"}``)처럼 공백 직렬화가 다르면 누락된다.
        여기서는 row 단위로 안전하게 파싱해 whitespace/직렬화 차이에 무관하게
        동일 semantic payload 를 매칭한다.
        """
        if not model_name and not lora_name:
            return []

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT batch_id, asset_key, project, model_name, lora_spec_json, updated_at
                FROM generation_tasks
                WHERE batch_id IS NOT NULL
                ORDER BY updated_at DESC
                """
            )
            rows = await cur.fetchall()

        def _row_has_lora(raw: Any, needle: str) -> bool:
            if not raw:
                return False
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return False
            if not isinstance(data, list):
                return False
            for spec in data:
                if isinstance(spec, dict) and spec.get("name") == needle:
                    return True
                if isinstance(spec, str) and spec == needle:
                    return True
            return False

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            batch_id = row["batch_id"]
            if not batch_id:
                continue
            if model_name and row["model_name"] != model_name:
                continue
            if lora_name and not _row_has_lora(row["lora_spec_json"], lora_name):
                continue

            slot = grouped.setdefault(
                str(batch_id),
                {
                    "batch_id": str(batch_id),
                    "asset_key": row["asset_key"],
                    "project": row["project"],
                    "last_updated_at": row["updated_at"],
                    "task_count": 0,
                },
            )
            slot["task_count"] += 1
            updated_at = row["updated_at"]
            if updated_at and (
                slot.get("last_updated_at") is None or updated_at > slot["last_updated_at"]
            ):
                slot["last_updated_at"] = updated_at

        items = sorted(
            grouped.values(),
            key=lambda it: it.get("last_updated_at") or "",
            reverse=True,
        )
        return items[: int(limit)]

    async def list_batch_tasks(self, batch_id: str) -> list[dict[str, Any]]:
        """한 batch 의 ``generation_tasks`` 행을 UI 에서 필요한 컬럼만 반환한다.

        BatchDetail Tasks 탭에서 `id/model/seed/status/attempts/last_error/
        next_attempt_at/updated_at` 을 표로 그리기 위한 엔드포인트.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT
                    id,
                    model_name,
                    seed,
                    status,
                    retries,
                    max_retries,
                    last_error,
                    next_attempt_at,
                    created_at,
                    updated_at
                FROM generation_tasks
                WHERE batch_id=?
                ORDER BY
                    CASE status
                        WHEN 'failed' THEN 0
                        WHEN 'processing' THEN 1
                        WHEN 'queued' THEN 2
                        WHEN 'done' THEN 3
                        ELSE 4
                    END,
                    created_at ASC
                """,
                (batch_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def cancel_batch(self, batch_id: str) -> dict[str, int]:
        """배치의 ``queued`` / ``processing`` 태스크를 ``cancelled`` 로 전환.

        이미 ``done`` / ``failed`` 인 task 는 건드리지 않는다 (history 보존).
        ``processing`` 도 status 만 cancelled 로 마크 — ComfyUI 서버에서
        실제로 실행 중인 prompt 는 자체적으로 끝까지 돈다 (interrupt 미구현).
        그래도 결과 저장 단계에서 cancelled 상태 task 는 candidate 가
        만들어지지 않도록 워커 측에서 가드해야 안전 — 단, 본 PR scope 밖이라
        기록만 하고 race window 는 수용 (UI 가 즉시 사라지지 않을 수 있음).

        반환: {cancelled_queued, cancelled_processing, untouched_done,
        untouched_failed} — UI 에 정확한 수치 표시용.
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM generation_tasks
                WHERE batch_id=?
                GROUP BY status
                """,
                (batch_id,),
            )
            counts: dict[str, int] = {row["status"]: int(row["n"]) for row in await cur.fetchall()}
            cancel_q = int(counts.get("queued", 0))
            cancel_p = int(counts.get("processing", 0))
            await conn.execute(
                """
                UPDATE generation_tasks
                SET status='cancelled',
                    last_error=COALESCE(last_error, 'cancelled by user'),
                    updated_at=?
                WHERE batch_id=? AND status IN ('queued','processing')
                """,
                (now, batch_id),
            )
            await conn.commit()
        return {
            "cancelled_queued": cancel_q,
            "cancelled_processing": cancel_p,
            "untouched_done": int(counts.get("done", 0)),
            "untouched_failed": int(counts.get("failed", 0)),
        }

    async def delete_batch(self, batch_id: str) -> dict[str, Any]:
        """배치의 task / candidate row 를 모두 삭제하고 파일 경로를 반환.

        파일 unlink 는 호출자 (server.py) 책임 — DB 트랜잭션과 FS unlink 를
        엮으면 partial failure 시 정합성이 깨진다. DB 만 먼저 정리하고 파일
        리스트를 돌려주면 server 가 best-effort 로 unlink.

        active 태스크 (queued/processing) 가 남아 있으면 caller 가 먼저
        ``cancel_batch`` 를 호출했어야 한다 — 본 함수는 status 와 무관하게
        삭제 (force semantics). 호출 정책은 endpoint 레이어에서.

        반환: {deleted_tasks, deleted_candidates, image_paths[]}.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT image_path FROM asset_candidates WHERE batch_id=?",
                (batch_id,),
            )
            image_paths = [str(r["image_path"]) for r in await cur.fetchall() if r["image_path"]]

            cur = await conn.execute(
                "DELETE FROM asset_candidates WHERE batch_id=?",
                (batch_id,),
            )
            deleted_candidates = cur.rowcount or 0

            cur = await conn.execute(
                "DELETE FROM generation_tasks WHERE batch_id=?",
                (batch_id,),
            )
            deleted_tasks = cur.rowcount or 0

            await conn.commit()
        return {
            "deleted_tasks": int(deleted_tasks),
            "deleted_candidates": int(deleted_candidates),
            "image_paths": image_paths,
        }

    async def retry_failed_batch_tasks(self, batch_id: str) -> list[int]:
        """해당 batch 에서 ``status='failed'`` 인 태스크를 큐로 되돌린다.

        retry 카운터는 보존하되 ``status='queued'`` + ``next_attempt_at=now`` 로
        놓아 워커가 다음 tick 에 곧바로 claim 하게 만든다. 되돌린 task id 리스트
        반환.
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, job_id FROM generation_tasks WHERE batch_id=? AND status='failed'",
                (batch_id,),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            if not rows:
                return []
            ids = [int(r["id"]) for r in rows]
            job_ids = list({r["job_id"] for r in rows if r.get("job_id")})
            placeholders = ",".join("?" * len(ids))
            await conn.execute(
                f"""
                UPDATE generation_tasks
                SET status='queued',
                    last_error=NULL,
                    next_attempt_at=?,
                    updated_at=?
                WHERE id IN ({placeholders})
                """,
                (now, now, *ids),
            )
            # 연결된 jobs.failed_count 를 재계산해서 상태가 따라 오도록 한다.
            for job_id in job_ids:
                await conn.execute(
                    """
                    UPDATE jobs
                    SET failed_count = (
                        SELECT COUNT(*) FROM generation_tasks
                        WHERE job_id=? AND status='failed'
                    ),
                    updated_at=?
                    WHERE id=?
                    """,
                    (job_id, now, job_id),
                )
            await conn.commit()
        for job_id in job_ids:
            await self.refresh_job_status(job_id)
        return ids

    async def get_candidate_by_id(self, candidate_id: int) -> dict[str, Any] | None:
        """후보 단건 조회 (id 기준)."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM asset_candidates WHERE id=?",
                (candidate_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def reject_candidate(self, candidate_id: int) -> bool:
        """후보를 reject 마킹한다 (GC 우선 대상)."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE asset_candidates SET is_rejected=1 WHERE id=?",
                (candidate_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def unreject_candidate(self, candidate_id: int) -> bool:
        """reject 마킹을 되돌린다 (5초 undo toast 용)."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE asset_candidates SET is_rejected=0 WHERE id=?",
                (candidate_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def mark_candidate_picked(self, candidate_id: int, asset_id: str) -> bool:
        """이 후보가 ``asset_id`` 메인 asset으로 승격됐다고 표시한다.

        ``list_today_batches`` 의 ``approved`` 판정은 "이 batch에서 한 장이라도
        picked되었나"로 결정된다 (단순히 ``(project, asset_key)`` 가 존재하는지
        보던 이전 휴리스틱은, 같은 키 재생성/inline 키 편집 시 잘못 판정함).
        ``picked_asset_id`` 는 undo-approve가 어떤 candidate를 풀어야 하는지
        역추적할 때 쓴다.
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE asset_candidates SET picked_at=?, picked_asset_id=? WHERE id=?",
                (now, asset_id, candidate_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def unmark_candidates_picked_for_asset(self, asset_id: str) -> int:
        """``asset_id`` 와 묶인 모든 candidate의 picked 마킹을 해제한다.

        ``/api/assets/{id}/undo-approve`` 가 호출. 풀지 않으면 batch가
        영구히 ``approved`` 로 남아 cherry-pick 큐에서 사라진 채 복구 안 됨.
        반환값: 영향받은 행 수 (0이면 해당 asset이 candidate-기반 승인이
        아니었거나 이미 풀린 상태)."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE asset_candidates
                SET picked_at=NULL, picked_asset_id=NULL
                WHERE picked_asset_id=?
                """,
                (asset_id,),
            )
            await conn.commit()
            return int(cursor.rowcount)

    async def batch_has_picked_candidate(self, batch_id: str) -> bool:
        """``batch_id`` 의 후보 중 한 장이라도 ``picked_at IS NOT NULL`` 이면 True."""
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute(
                """
                SELECT 1 FROM asset_candidates
                WHERE batch_id=? AND picked_at IS NOT NULL
                LIMIT 1
                """,
                (batch_id,),
            )
            row = await cur.fetchone()
            return row is not None

    async def count_pending_candidates(self, batch_id: str) -> dict[str, int]:
        """batch에 남아 있는 처리 후보 카운트.

        - ``total``: 전체 후보
        - ``rejected``: ``is_rejected=1`` 인 후보
        - ``approved``: 동일 ``(project, asset_key)`` 의 메인 asset이 이미 존재하면 1, 아니면 0
          (간이 휴리스틱: cherry-pick 1장 = 1 batch 결판)
        - ``remaining``: ``total - rejected - (approved ? total : 0)`` 으로 보지 않고,
          단순히 "rejected가 아닌 후보 수" 를 반환한다 (UI 카운터용).
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_rejected=1 THEN 1 ELSE 0 END) AS rejected
                FROM asset_candidates
                WHERE batch_id=?
                """,
                (batch_id,),
            )
            row = await cur.fetchone()
            total = int(row["total"] or 0) if row else 0
            rejected = int(row["rejected"] or 0) if row else 0
        return {
            "total": total,
            "rejected": rejected,
            "remaining": max(total - rejected, 0),
        }

    async def list_today_batches(
        self, since: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """``list_recent_batches`` 의 thin wrapper — 오늘 큐 헤더 전용.

        approved 여부는 "이 batch에서 한 장이라도 picked되었나"로 판정한다.
        같은 ``(project, asset_key)`` 가 다른 경로(이전 batch, 수동 import)로
        이미 존재하더라도, 새 batch는 사용자가 한 장 골라야 approved가 된다.
        """
        rows = await self.list_recent_batches(since=since, limit=limit)
        for row in rows:
            batch_id = row.get("batch_id")
            row["approved"] = (
                bool(batch_id) and await self.batch_has_picked_candidate(batch_id)
            )
            counts = await self.count_pending_candidates(row["batch_id"])
            row["remaining"] = counts["remaining"]
        return rows

    async def get_latest_asset_history(self, asset_id: str) -> dict[str, Any] | None:
        """가장 최근 asset_history 한 건 반환 (undo-approve 용)."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM asset_history
                WHERE asset_id=?
                ORDER BY version DESC
                LIMIT 1
                """,
                (asset_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_asset_history(self, history_id: int) -> bool:
        """asset_history 한 행 삭제 (undo-approve 시 최신 history 제거)."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "DELETE FROM asset_history WHERE id=?",
                (history_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def delete_asset(self, asset_id: str) -> bool:
        """assets 행 + 종속 history를 삭제한다 (undo-approve: 신규 생성된 asset 되돌리기)."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "DELETE FROM asset_history WHERE asset_id=?", (asset_id,)
            )
            cursor = await conn.execute(
                "DELETE FROM assets WHERE id=?", (asset_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def restore_asset_from_history(
        self, asset_id: str, history: dict[str, Any]
    ) -> bool:
        """history 한 건을 assets primary로 되돌린다 (undo-approve)."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE assets SET
                    image_path=?,
                    width=?,
                    height=?,
                    color_count=?,
                    has_alpha=?,
                    validation_status=?,
                    validation_message=?,
                    generation_seed=?,
                    generation_model=?,
                    generation_prompt=?,
                    metadata_json=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    history["image_path"],
                    history["width"],
                    history["height"],
                    history["color_count"],
                    history["has_alpha"],
                    history["validation_status"],
                    history.get("validation_message"),
                    history.get("generation_seed"),
                    history.get("generation_model"),
                    history.get("generation_prompt"),
                    history.get("metadata_json"),
                    now,
                    asset_id,
                ),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def list_recent_batches(
        self, since: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """최근 batch 목록 (각 batch_id 별 집계).

        - ``since``: ISO8601 UTC, 이 시각 이후 생성된 batch만 필터.
        - 각 항목: batch_id, project, asset_key, category, workflow_category,
          workflow_variants(distinct list), backend, total, done, failed, active,
          first_created_at, last_updated_at, candidate_total, rejected_count.
        - workflow_variants 는 batch 안에서 다양한 variant 가 섞여 있을 수
          있어 distinct 집합. 단일 variant batch 면 길이 1.
        """
        params: list[Any] = []
        where = "WHERE batch_id IS NOT NULL"
        if since:
            where += " AND created_at >= ?"
            params.append(since)
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                f"""
                SELECT
                    batch_id,
                    MIN(project) AS project,
                    MIN(asset_key) AS asset_key,
                    MIN(category) AS category,
                    MIN(job_id) AS job_id,
                    MIN(workflow_category) AS workflow_category,
                    MIN(backend) AS backend,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status IN ('queued','processing') THEN 1 ELSE 0 END) AS active,
                    MIN(created_at) AS first_created_at,
                    MAX(updated_at) AS last_updated_at
                FROM generation_tasks
                {where}
                GROUP BY batch_id
                ORDER BY first_created_at DESC
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
            results = [dict(row) for row in rows]
        for row in results:
            async with aiosqlite.connect(self.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    """
                    SELECT
                        COUNT(*) AS candidate_total,
                        SUM(CASE WHEN is_rejected=1 THEN 1 ELSE 0 END) AS rejected_count
                    FROM asset_candidates
                    WHERE batch_id=?
                    """,
                    (row["batch_id"],),
                )
                agg = await cur.fetchone()
                row["candidate_total"] = int(agg["candidate_total"] or 0) if agg else 0
                row["rejected_count"] = int(agg["rejected_count"] or 0) if agg else 0

                # workflow_variants distinct — batch 안에 여러 variant 가 섞일
                # 수 있어 GROUP BY 단계에서 집계 불가. 별도 1쿼리로 모음.
                vcur = await conn.execute(
                    """
                    SELECT DISTINCT workflow_variant
                    FROM generation_tasks
                    WHERE batch_id=? AND workflow_variant IS NOT NULL
                    ORDER BY workflow_variant
                    """,
                    (row["batch_id"],),
                )
                vrows = await vcur.fetchall()
                row["workflow_variants"] = [
                    str(r["workflow_variant"]) for r in vrows if r["workflow_variant"]
                ]
        return results

    async def list_asset_candidates(
        self,
        project: str,
        asset_key: str,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """특정 에셋 키에 대한 후보 슬롯 목록."""
        if job_id:
            q = """
                SELECT * FROM asset_candidates
                WHERE project=? AND asset_key=? AND job_id=?
                ORDER BY slot_index ASC
                """
            params = (project, asset_key, job_id)
        else:
            q = """
                SELECT * FROM asset_candidates
                WHERE project=? AND asset_key=?
                ORDER BY created_at DESC, slot_index ASC
                """
            params = (project, asset_key)
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(q, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def count_candidate_slots(
        self,
        project: str,
        asset_key: str,
        job_id: str,
    ) -> int:
        """해당 작업에 대해 저장된 후보 슬롯 수."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM asset_candidates
                WHERE project=? AND asset_key=? AND job_id=?
                """,
                (project, asset_key, job_id),
            )
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def set_asset_provenance(
        self,
        asset_id: str,
        *,
        generation_seed: int | None,
        generation_model: str | None,
        generation_prompt: str | None,
        metadata_json: str | None,
        approval_mode: str | None = None,
    ) -> bool:
        """첫 승인 시 ``upsert_scanned_asset`` 이 비워둔 generation_* 필드를 채운다.

        ``replace_asset_primary_image`` 와 달리 history 행을 생성하지 않는다
        (이전 primary가 없는 신규 asset 이라 history에 남길 게 없음).
        ``POST /api/assets/{id}/regenerate`` 같은 후속 기능이 이 정보를 그대로
        쓰기 위해 필요.

        ``approval_mode`` 는 None 이면 기존 값 유지, 주어지면 덮어씀
        (cherry-pick 으로 bypass candidate 가 promote 된 케이스에서 'bypass'
        flag 가 새 asset 에 따라가게 한다).
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                UPDATE assets SET
                    generation_seed=?,
                    generation_model=?,
                    generation_prompt=?,
                    metadata_json=?,
                    approval_mode=COALESCE(?, approval_mode),
                    updated_at=?
                WHERE id=?
                """,
                (
                    generation_seed,
                    generation_model,
                    generation_prompt,
                    metadata_json,
                    approval_mode,
                    now,
                    asset_id,
                ),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def replace_asset_primary_image(
        self,
        asset_id: str,
        *,
        image_path: str,
        width: int,
        height: int,
        color_count: int,
        has_alpha: bool,
        validation_status: str,
        validation_message: str | None,
        generation_seed: int | None,
        generation_model: str | None,
        generation_prompt: str | None,
        metadata_json: str | None,
        approval_mode: str | None = None,
    ) -> bool:
        """후보 선택 등으로 메인 에셋 파일을 교체한다(이전 내용은 asset_history에 남김)."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,))
            existing = await cur.fetchone()
            if existing is None:
                return False
            ex = dict(existing)
            ver_cur = await conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM asset_history WHERE asset_id=?",
                (ex["id"],),
            )
            ver_row = await ver_cur.fetchone()
            next_version = int(ver_row[0] if ver_row else 0) + 1
            await conn.execute(
                """
                INSERT INTO asset_history (
                    asset_id, version, job_id, image_path, width, height,
                    color_count, has_alpha, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ex["id"],
                    next_version,
                    ex.get("job_id"),
                    ex["image_path"],
                    ex["width"],
                    ex["height"],
                    ex["color_count"],
                    ex["has_alpha"],
                    ex["validation_status"],
                    ex.get("validation_message"),
                    ex.get("generation_seed"),
                    ex.get("generation_model"),
                    ex.get("generation_prompt"),
                    ex.get("metadata_json"),
                    now,
                ),
            )
            # approval_mode 가 None 이면 기존 값 유지 (CASE WHEN), 주어지면 덮어씀.
            await conn.execute(
                """
                UPDATE assets SET
                    image_path=?,
                    width=?,
                    height=?,
                    color_count=?,
                    has_alpha=?,
                    validation_status=?,
                    validation_message=?,
                    generation_seed=?,
                    generation_model=?,
                    generation_prompt=?,
                    metadata_json=?,
                    approval_mode=COALESCE(?, approval_mode),
                    updated_at=?
                WHERE id=?
                """,
                (
                    image_path,
                    width,
                    height,
                    color_count,
                    1 if has_alpha else 0,
                    validation_status,
                    validation_message,
                    generation_seed,
                    generation_model,
                    generation_prompt,
                    metadata_json,
                    approval_mode,
                    now,
                    asset_id,
                ),
            )
            await conn.commit()
        return True
