"""W7: task-level 지수 백오프 회귀 테스트.

검증 항목
---------
- ``retry_or_fail_task``가 retry 분기에서 ``next_attempt_at``을 저장한다.
- ``next_attempt_at``이 미래인 동안 ``claim_next_task``는 None을 돌려준다.
- 시각이 지나면 다시 점유 가능하다.
- ``force_fail=True``이면 ``next_attempt_at``과 무관하게 즉시 ``failed``.
- ``_compute_next_attempt_at``는 환경변수와 시도 횟수에 따라 단조 증가하고 cap에 걸린다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import server
from models import Database


@pytest.fixture()
def fresh_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "backoff.db")
    asyncio.run(db.init())
    return db


def _enqueue_one(db: Database) -> dict:
    async def scenario() -> dict:
        await db.create_job("job1", "generation", None)
        await db.enqueue_generation_task(
            {
                "job_id": "job1",
                "project": "p",
                "asset_key": "k",
                "category": "sprite",
                "prompt": "x",
                "negative_prompt": None,
                "model_name": None,
                "width": 64,
                "height": 64,
                "steps": 1,
                "cfg": 1.0,
                "sampler": "DPM++ 2M",
                "expected_size": 64,
                "max_colors": 32,
                "max_retries": 3,
            }
        )
        claimed = await db.claim_next_task()
        assert claimed is not None
        return claimed

    return asyncio.run(scenario())


def test_retry_sets_next_attempt_at_in_future(fresh_db: Database) -> None:
    task = _enqueue_one(fresh_db)
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()

    asyncio.run(
        fresh_db.retry_or_fail_task(task, "boom", next_attempt_at=future)
    )

    again = asyncio.run(fresh_db.claim_next_task())
    assert again is None, "백오프 기간에는 점유되면 안 된다"


def test_claim_returns_task_after_due_time(fresh_db: Database) -> None:
    task = _enqueue_one(fresh_db)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    asyncio.run(
        fresh_db.retry_or_fail_task(task, "transient", next_attempt_at=past)
    )

    again = asyncio.run(fresh_db.claim_next_task())
    assert again is not None
    assert again["id"] == task["id"]
    assert again["retries"] == task["retries"] + 1


def test_force_fail_ignores_next_attempt_at(fresh_db: Database) -> None:
    task = _enqueue_one(fresh_db)
    future = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()

    asyncio.run(
        fresh_db.retry_or_fail_task(
            task, "fatal", force_fail=True, next_attempt_at=future
        )
    )

    async def fetch_status() -> str:
        import aiosqlite

        async with aiosqlite.connect(fresh_db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT status FROM generation_tasks WHERE id=?", (task["id"],)
            )
            row = await cur.fetchone()
            assert row is not None
            return str(row["status"])

    assert asyncio.run(fetch_status()) == "failed"


def test_soonest_due_seconds_returns_zero_when_due(fresh_db: Database) -> None:
    task = _enqueue_one(fresh_db)
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    asyncio.run(fresh_db.retry_or_fail_task(task, "x", next_attempt_at=past))

    wait = asyncio.run(fresh_db.soonest_due_seconds(default=99.0))
    assert wait == 0.0


def test_soonest_due_seconds_returns_default_when_no_queued(
    fresh_db: Database,
) -> None:
    wait = asyncio.run(fresh_db.soonest_due_seconds(default=42.0))
    assert wait == 42.0


def test_soonest_due_seconds_positive_when_future(fresh_db: Database) -> None:
    task = _enqueue_one(fresh_db)
    future = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    asyncio.run(fresh_db.retry_or_fail_task(task, "x", next_attempt_at=future))

    wait = asyncio.run(fresh_db.soonest_due_seconds(default=99.0))
    assert 0.0 < wait <= 10.5


def test_compute_next_attempt_at_grows_with_retries(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("TASK_BACKOFF_BASE_SEC", "1")
    monkeypatch.setenv("TASK_BACKOFF_MAX_SEC", "1000")
    monkeypatch.setattr(server.random, "uniform", lambda _a, _b: 0.0)

    now = datetime.now(timezone.utc)
    s0 = datetime.fromisoformat(server._compute_next_attempt_at(0))
    s1 = datetime.fromisoformat(server._compute_next_attempt_at(1))
    s3 = datetime.fromisoformat(server._compute_next_attempt_at(3))

    d0 = (s0 - now).total_seconds()
    d1 = (s1 - now).total_seconds()
    d3 = (s3 - now).total_seconds()

    # base*2^n: 1, 2, 8 (대략, +1초 이내 호출 시간 오차 허용)
    assert 0.5 < d0 < 2.0
    assert 1.5 < d1 < 3.0
    assert 7.0 < d3 < 10.0


def test_compute_next_attempt_at_caps_at_max(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("TASK_BACKOFF_BASE_SEC", "1")
    monkeypatch.setenv("TASK_BACKOFF_MAX_SEC", "5")
    monkeypatch.setattr(server.random, "uniform", lambda _a, _b: 0.0)

    now = datetime.now(timezone.utc)
    s10 = datetime.fromisoformat(server._compute_next_attempt_at(10))
    delta = (s10 - now).total_seconds()
    assert delta <= 5.5
