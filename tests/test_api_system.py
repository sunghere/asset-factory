"""/api/system/{db,worker,logs/recent} 스키마 회귀 테스트.

System.jsx 의 3 블록이 이 계약을 기반으로 렌더하므로 키 이름/타입이 틀어지면
UI 가 즉시 깨진다. 여기서는 DB row count, worker heartbeat, ring buffer
3가지가 각각 독립적으로 잘 작동하는지만 확인한다.
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
    async def publish(self, _event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN001
    db_path = tmp_path / "system.db"
    db = Database(db_path)
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    monkeypatch.setattr(server, "DB_PATH", db_path)
    # 워커 상태와 로그 ring 을 깨끗한 상태로 돌린다.
    server._worker_state.update(
        last_heartbeat_at=None,
        current_task=None,
        last_task_id=None,
        processed_count=0,
    )
    monkeypatch.setattr(server, "worker_task", None)
    server._log_ring.clear()
    return db, db_path


def test_system_db_reports_tables_and_queue(isolated, monkeypatch) -> None:  # noqa: ANN001
    db, db_path = isolated

    # 본 테스트는 queued/processing/failed 의 정확한 카운트를 단언하는데,
    # TestClient(..) 의 lifespan 이 실제 generation_worker 를 띄워 db.claim_next_task
    # 를 호출한다. 워커가 queued task 를 claim 해 processing 으로 옮기는 race
    # window 에서 (queued=2, processing=1) 같이 합 3 인 상태가 잡혀 어쌔션이
    # 깨졌다 (Python 3.12 + GitHub runner 타이밍에서만 종종 재현). claim 을
    # 비활성하면 워커는 idle 상태로만 돌고 DB count 는 seed 그대로 유지된다.
    # test_system_worker_contract 는 별도 테스트라 영향 없음 (heartbeat 만 검증).
    async def _no_claim():
        return None
    monkeypatch.setattr(db, "claim_next_task", _no_claim)

    async def seed() -> None:
        await db.create_job("job_s1", "generation", None)
        # queued · processing · failed 각 1개 — queue 블록 모든 슬롯을 덮는다.
        tids: list[int] = []
        for idx, status in enumerate(("queued", "processing", "failed")):
            task_id = await db.enqueue_generation_task(
                {
                    "job_id": "job_s1",
                    "project": "p",
                    "asset_key": f"k{idx}",
                    "category": "sprite",
                    "prompt": "x",
                    "negative_prompt": None,
                    "model_name": "m",
                    "width": 32,
                    "height": 32,
                    "steps": 1,
                    "cfg": 1.0,
                    "sampler": "DPM++ 2M",
                    "expected_size": 32,
                    "max_colors": 32,
                    "max_retries": 3,
                    "batch_id": "btc_sys",
                    "seed": idx,
                }
            )
            tids.append(int(task_id))
            async with aiosqlite.connect(db.db_path) as conn:
                await conn.execute(
                    "UPDATE generation_tasks SET status=? WHERE id=?",
                    (status, task_id),
                )
                await conn.commit()

    asyncio.run(seed())

    with TestClient(server.app) as client:
        r = client.get("/api/system/db")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == str(db_path)
    assert body["exists"] is True
    assert body["size_bytes"] > 0
    tables = body["tables"]
    assert tables["jobs"] == 1
    assert tables["generation_tasks"] == 3
    assert tables["asset_candidates"] == 0
    assert tables["assets"] == 0
    queue = body["queue"]
    # 환경/타이밍에 따라 recover_orphan_tasks() 적용 시점이 달라질 수 있다.
    # (processing 유지: queued=1,processing=1) 또는 (복구 완료: queued=2,processing=0)
    assert queue["queued_total"] in (1, 2)
    assert queue["processing"] in (0, 1)
    assert queue["queued_total"] + queue["processing"] == 2
    # queued 행은 모두 next_attempt_at=NULL 로 넣었으므로 due는 queued_total과 같다.
    assert queue["queued_due"] == queue["queued_total"]
    assert queue["failed"] == 1


def test_system_worker_contract(isolated) -> None:
    """TestClient(..) 의 lifespan 이 실제 워커를 기동하므로 ``alive=True`` +
    non-null heartbeat 가 기대치. 스키마/타입 계약만 핀으로 잡는다."""
    db, _ = isolated

    async def seed_job() -> None:
        await db.create_job("job_w1", "generation", None)

    asyncio.run(seed_job())

    with TestClient(server.app) as client:
        r = client.get("/api/system/worker")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alive"] is True
    assert body["last_heartbeat_at"] is not None
    assert isinstance(body["last_heartbeat_at"], str)
    assert isinstance(body["processed_count"], int)
    assert body["queue_depth"] == 0
    assert body["queue_due"] == 0
    assert body["processing"] == 0
    assert body["failed"] == 0
    # current_task 는 idle 이면 None, claim 시 dict. 어느 쪽이든 이 두 값만 가능.
    assert body["current_task"] is None or isinstance(body["current_task"], dict)


def test_system_logs_recent_filters_by_level(isolated) -> None:
    # ring 에 수동으로 3건 넣고 필터/정렬(최신순) 확인.
    server._log_ring.extend(
        [
            {"ts": "2026-04-20T00:00:00+00:00", "level": "info", "message": "boot"},
            {"ts": "2026-04-20T00:00:01+00:00", "level": "warn", "message": "slow"},
            {"ts": "2026-04-20T00:00:02+00:00", "level": "error", "message": "boom"},
        ]
    )
    with TestClient(server.app) as client:
        all_items = client.get("/api/system/logs/recent").json()
        errors_only = client.get("/api/system/logs/recent?level=error").json()
        top_one = client.get("/api/system/logs/recent?limit=1").json()

    assert all_items["count"] == 3
    # 최신(error) 이 먼저
    assert [it["level"] for it in all_items["items"]] == ["error", "warn", "info"]
    assert errors_only["count"] == 1
    assert errors_only["items"][0]["message"] == "boom"
    # limit=1 → ring 끝(= error) 만 돌려받는다.
    assert top_one["count"] == 1
    assert top_one["items"][0]["level"] == "error"


def test_system_logs_recent_limit_validation(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.get("/api/system/logs/recent?limit=0")
    assert r.status_code == 422
