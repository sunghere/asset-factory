"""Asset Factory SQLite 모델/쿼리 레이어."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


def utc_now() -> str:
    """UTC ISO8601 문자열 반환."""
    return datetime.now(timezone.utc).isoformat()


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
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_batch ON generation_tasks(batch_id)"
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
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_batch ON asset_candidates(batch_id)"
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
                    created_at, updated_at
                )
                VALUES (
                    :job_id, :project, :asset_key, :category, :prompt, :negative_prompt,
                    :model_name, :width, :height, :steps, :cfg, :sampler, 'queued',
                    0, :max_retries, NULL, :expected_size, :max_colors,
                    :candidate_slot, :candidates_total,
                    :batch_id, :lora_spec_json, :seed,
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
                SET status='processing', updated_at=?
                WHERE id=?
                """,
                (utc_now(), row["id"]),
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
            await conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status, image_path, width, height,
                    color_count, has_alpha, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt,
                    metadata_json, created_at, updated_at
                )
                VALUES (
                    :id, :job_id, :project, :asset_key, :category, :status, :image_path, :width, :height,
                    :color_count, :has_alpha, :validation_status, :validation_message,
                    :generation_seed, :generation_model, :generation_prompt,
                    :metadata_json, :created_at, :updated_at
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

    async def list_assets(
        self,
        project: str | None = None,
        status: str | None = None,
        category: str | None = None,
        validation_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """필터 기반 에셋 목록 조회."""
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
            return [dict(row) for row in rows]

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        """에셋 단건 조회."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

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
            return dict(row) if row else None

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

    async def list_approved_assets(self, project: str | None = None) -> list[dict[str, Any]]:
        """승인된 에셋 목록을 반환한다."""
        query = "SELECT * FROM assets WHERE status='approved'"
        params: list[Any] = []
        if project:
            query += " AND project=?"
            params.append(project)
        query += " ORDER BY asset_key ASC"

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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
                    batch_id, is_rejected, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
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
                    now,
                ),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def list_batch_candidates(self, batch_id: str) -> list[dict[str, Any]]:
        """한 batch에 속한 모든 후보를 시간순으로 반환한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM asset_candidates
                WHERE batch_id=?
                ORDER BY is_rejected ASC, created_at ASC, slot_index ASC
                """,
                (batch_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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

    async def mark_candidate_picked(self, candidate_id: int) -> bool:
        """이 후보가 메인 asset으로 승격됐다고 표시한다.

        ``list_today_batches`` 의 ``approved`` 판정은 "이 batch에서 한 장이라도
        picked되었나"로 결정된다 (단순히 ``(project, asset_key)`` 가 존재하는지
        보던 이전 휴리스틱은, 같은 키 재생성/inline 키 편집 시 잘못 판정함).
        """
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE asset_candidates SET picked_at=? WHERE id=?",
                (now, candidate_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

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
        - 각 항목: batch_id, project, asset_key, category, total, done, failed,
          rejected_candidates, picked_candidates, first_created_at, last_updated_at.
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
    ) -> bool:
        """첫 승인 시 ``upsert_scanned_asset`` 이 비워둔 generation_* 필드를 채운다.

        ``replace_asset_primary_image`` 와 달리 history 행을 생성하지 않는다
        (이전 primary가 없는 신규 asset 이라 history에 남길 게 없음).
        ``POST /api/assets/{id}/regenerate`` 같은 후속 기능이 이 정보를 그대로
        쓰기 위해 필요.
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
                    updated_at=?
                WHERE id=?
                """,
                (
                    generation_seed,
                    generation_model,
                    generation_prompt,
                    metadata_json,
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
                    now,
                    asset_id,
                ),
            )
            await conn.commit()
        return True
