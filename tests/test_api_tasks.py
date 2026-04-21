"""/api/batches/{id}/tasks · /api/batches/{id}/retry-failed 계약 테스트.

BatchDetail Tasks 탭이 이 두 엔드포인트를 기반으로 돌기 때문에 응답 스키마
(특히 ``attempts`` 집계와 ``retried_count``)가 깨지면 UI 가 즉시 붕괴한다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import server
from models import Database


class _DummyBroker:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event):  # noqa: ANN001
        self.events.append(event)


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "tasks.db")
    asyncio.run(db.init())
    broker = _DummyBroker()
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", broker)
    monkeypatch.setattr(server, "api_key", None)  # require_api_key 우회
    return db, broker


async def _seed_tasks(
    db: Database,
    batch_id: str,
    statuses: list[str],
) -> list[int]:
    """주어진 상태 순서대로 태스크를 미리 심는다.

    ``enqueue_generation_task`` 는 상태를 'queued' 로 강제하므로 테스트에서는
    enqueue 후 UPDATE 로 원하는 ``status`` 를 직접 주입한다. BatchDetail 의
    정렬 규칙 (failed → processing → queued → done) 검증에 쓰인다.
    """
    ids: list[int] = []
    await db.create_job("job_t1", "generation", None)
    for idx, status in enumerate(statuses):
        task_id = await db.enqueue_generation_task(
            {
                "job_id": "job_t1",
                "project": "p",
                "asset_key": f"k{idx}",
                "category": "sprite",
                "prompt": "x",
                "negative_prompt": None,
                "model_name": f"m{idx}",
                "width": 64,
                "height": 64,
                "steps": 1,
                "cfg": 1.0,
                "sampler": "DPM++ 2M",
                "expected_size": 64,
                "max_colors": 32,
                "max_retries": 3,
                "batch_id": batch_id,
                "seed": 1000 + idx,
            }
        )
        ids.append(int(task_id))
        # 실제 status/last_error 를 원하는 값으로 덮어쓴다.
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute(
                "UPDATE generation_tasks "
                "SET status=?, last_error=?, retries=? "
                "WHERE id=?",
                (
                    status,
                    "boom" if status == "failed" else None,
                    2 if status == "failed" else 0,
                    task_id,
                ),
            )
            await conn.commit()
    return ids


def test_list_batch_tasks_returns_schema_and_orders_by_status(isolated) -> None:
    db, _ = isolated
    asyncio.run(_seed_tasks(db, "btc_tk1", ["done", "failed", "queued", "processing"]))
    with TestClient(server.app) as client:
        r = client.get("/api/batches/btc_tk1/tasks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batch_id"] == "btc_tk1"
    assert body["count"] == 4
    assert body["failed_count"] == 1
    items = body["items"]
    statuses = [it["status"] for it in items]
    # 정렬: failed 가 최상단, done 이 최하단. (lifespan recover 시 processing -> queued 가능)
    assert statuses[0] == "failed"
    assert statuses[-1] == "done"
    assert statuses.count("queued") in (1, 2)
    assert statuses.count("processing") in (0, 1)
    assert statuses.count("failed") == 1
    assert statuses.count("done") == 1
    # 스키마 핀
    expected_keys = {
        "id", "model", "seed", "status", "attempts", "max_retries",
        "last_error", "next_attempt_at", "created_at", "updated_at",
    }
    for item in items:
        assert expected_keys.issubset(item.keys())
    failed = items[0]
    assert failed["last_error"] == "boom"
    assert failed["attempts"] == 2
    assert failed["max_retries"] == 3


def test_list_batch_tasks_404_for_unknown_batch(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.get("/api/batches/btc_missing/tasks")
    assert r.status_code == 404


def test_retry_failed_requeues_and_publishes_event(isolated) -> None:
    db, broker = isolated
    ids = asyncio.run(_seed_tasks(db, "btc_rf1", ["failed", "failed", "done"]))
    failed_ids = set(ids[:2])
    with TestClient(server.app) as client:
        r = client.post("/api/batches/btc_rf1/retry-failed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batch_id"] == "btc_rf1"
    assert body["retried_count"] == 2
    assert set(body["task_ids"]) == failed_ids

    # failed 가 queued 로 되돌려졌는지 실제 DB 상태 확인.
    async def verify() -> None:
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, status, last_error FROM generation_tasks "
                "WHERE batch_id=?",
                ("btc_rf1",),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        by_id = {int(r["id"]): r for r in rows}
        for fid in failed_ids:
            assert by_id[fid]["status"] == "queued"
            assert by_id[fid]["last_error"] is None
        # done 은 그대로
        done_id = ids[2]
        assert by_id[done_id]["status"] == "done"

    asyncio.run(verify())
    # 이벤트 브로드캐스트 — lib/events.EVT_BATCH_RETRY_FAILED 와 동일한 문자열.
    from lib import events as ev
    matching = [e for e in broker.events if e.get("type") == ev.EVT_BATCH_RETRY_FAILED]
    assert len(matching) == 1
    assert matching[0]["batch_id"] == "btc_rf1"
    assert matching[0]["retried_count"] == 2


def test_retry_failed_noop_when_nothing_failed(isolated) -> None:
    db, broker = isolated
    asyncio.run(_seed_tasks(db, "btc_rf2", ["done", "queued"]))
    with TestClient(server.app) as client:
        r = client.post("/api/batches/btc_rf2/retry-failed")
    assert r.status_code == 200
    body = r.json()
    assert body["retried_count"] == 0
    assert body["task_ids"] == []
    # noop 이면 이벤트도 발행하지 않는다.
    assert broker.events == []


def test_retry_failed_404_for_unknown_batch(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post("/api/batches/btc_nope/retry-failed")
    assert r.status_code == 404


def test_retry_failed_requires_api_key_when_set(isolated, monkeypatch) -> None:
    """``API_KEY`` 가 설정되면 ``POST …/retry-failed`` 에 ``x-api-key`` 가 필요하다."""
    db, _ = isolated
    monkeypatch.setattr(server, "api_key", "secret")
    asyncio.run(_seed_tasks(db, "btc_rf_auth", ["failed"]))
    with TestClient(server.app) as client:
        unauth = client.post("/api/batches/btc_rf_auth/retry-failed")
        assert unauth.status_code == 401
        ok = client.post(
            "/api/batches/btc_rf_auth/retry-failed",
            headers={"x-api-key": "secret"},
        )
        assert ok.status_code == 200
        assert ok.json()["retried_count"] == 1
