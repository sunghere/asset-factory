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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
                """
            )
            await conn.commit()
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
                    created_at TEXT NOT NULL,
                    UNIQUE(project, asset_key, slot_index, job_id)
                )
                """
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
                    created_at, updated_at
                )
                VALUES (
                    :job_id, :project, :asset_key, :category, :prompt, :negative_prompt,
                    :model_name, :width, :height, :steps, :cfg, :sampler, 'queued',
                    0, :max_retries, NULL, :expected_size, :max_colors,
                    :candidate_slot, :candidates_total,
                    :created_at, :updated_at
                )
                """,
                {
                    **task,
                    "candidate_slot": task.get("candidate_slot"),
                    "candidates_total": task.get("candidates_total"),
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
        """다음 queued 태스크를 processing으로 점유한다."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM generation_tasks
                WHERE status='queued'
                ORDER BY id ASC
                LIMIT 1
                """
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

    async def retry_or_fail_task(self, task: dict[str, Any], error_message: str) -> None:
        """실패 태스크를 재시도하거나 최종 실패 처리."""
        now = utc_now()
        retries = int(task["retries"])
        max_retries = int(task["max_retries"])
        async with aiosqlite.connect(self.db_path) as conn:
            if retries + 1 < max_retries:
                await conn.execute(
                    """
                    UPDATE generation_tasks
                    SET status='queued', retries=?, last_error=?, updated_at=?
                    WHERE id=?
                    """,
                    (retries + 1, error_message, now, task["id"]),
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
    ) -> int:
        """후보 이미지 한 슬롯을 저장한다. INSERT OR REPLACE."""
        now = utc_now()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO asset_candidates (
                    project, asset_key, slot_index, job_id, image_path,
                    width, height, color_count, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    now,
                ),
            )
            await conn.commit()
            return int(cursor.lastrowid)

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
