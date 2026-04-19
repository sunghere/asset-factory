"""``GET /api/cherry-pick/queue`` shape + remaining 카운트 검증.

DB 헬퍼 (``count_pending_candidates``, ``list_today_batches``) 도 함께 검증한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from models import Database


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


def _make_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), (8, 16, 32, 255)).save(path)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "queue.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)
    return db, data


async def _seed_batch(
    db: Database, data: Path, *, batch_id: str, asset_key: str, n: int
) -> list[int]:
    """주어진 batch에 n장 후보 + 1개의 enqueued task를 만든다.

    task가 있어야 ``list_recent_batches`` 가 batch를 본다 (generation_tasks 기반 GROUP BY).
    """
    job_id = "test_job_" + batch_id
    await db.create_job(job_id, "design_batch", None)
    for slot in range(n):
        await db.enqueue_generation_task({
            "job_id": job_id,
            "project": "proj",
            "asset_key": asset_key,
            "category": "character",
            "prompt": "p",
            "negative_prompt": None,
            "model_name": "m",
            "width": 32,
            "height": 32,
            "steps": 20,
            "cfg": 7.0,
            "sampler": "Euler",
            "max_retries": 1,
            "expected_size": None,
            "max_colors": 32,
            "batch_id": batch_id,
            "seed": 100 + slot,
            "lora_spec_json": None,
        })
    ids: list[int] = []
    for slot in range(n):
        img = data / "candidates" / "proj" / asset_key / batch_id / f"slot_{slot}.png"
        _make_png(img)
        await db.insert_asset_candidate(
            project="proj",
            asset_key=asset_key,
            slot_index=slot,
            job_id=job_id,
            image_path=str(img),
            width=32,
            height=32,
            color_count=4,
            validation_status="pass",
            validation_message="ok",
            generation_seed=100 + slot,
            generation_model="m",
            generation_prompt="p",
            metadata_json='{"steps":20,"max_colors":32}',
            batch_id=batch_id,
        )
        cand = (await db.list_batch_candidates(batch_id))[-1]
        ids.append(int(cand["id"]))
    return ids


def test_count_pending_candidates(isolated) -> None:
    db, data = isolated
    ids = asyncio.run(_seed_batch(db, data, batch_id="btc_q1", asset_key="hero", n=5))

    counts = asyncio.run(db.count_pending_candidates("btc_q1"))
    assert counts == {"total": 5, "rejected": 0, "remaining": 5}

    asyncio.run(db.reject_candidate(ids[0]))
    asyncio.run(db.reject_candidate(ids[1]))
    counts = asyncio.run(db.count_pending_candidates("btc_q1"))
    assert counts == {"total": 5, "rejected": 2, "remaining": 3}


def test_queue_endpoint_shape_and_counts(isolated) -> None:
    db, data = isolated
    asyncio.run(_seed_batch(db, data, batch_id="btc_q2", asset_key="hero", n=4))
    asyncio.run(_seed_batch(db, data, batch_id="btc_q3", asset_key="enemy", n=6))
    # btc_q3에서 2장 reject
    cand_ids = asyncio.run(db.list_batch_candidates("btc_q3"))
    asyncio.run(db.reject_candidate(int(cand_ids[0]["id"])))
    asyncio.run(db.reject_candidate(int(cand_ids[1]["id"])))

    with TestClient(server.app) as client:
        r = client.get("/api/cherry-pick/queue")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "since" in body
    assert "items" in body and "total_remaining" in body
    assert body["total_batches"] >= 2
    by_id = {item["batch_id"]: item for item in body["items"]}
    assert by_id["btc_q2"]["remaining"] == 4
    assert by_id["btc_q3"]["remaining"] == 4  # 6 - 2 rejected
    # approved=False (메인 asset 없음)
    assert by_id["btc_q2"]["approved"] is False
    assert by_id["btc_q3"]["approved"] is False
    # total_remaining = 모든 pending batch 의 remaining 합 (8 이상)
    assert body["total_remaining"] >= 8


def test_queue_marks_approved_only_when_candidate_picked(isolated) -> None:
    """approved 판정은 ``(project, asset_key)`` 존재 여부가 아니라
    "이 batch에서 한 장 골랐나"여야 한다.

    회귀 방지: 같은 키로 재생성한 배치가 (이전 배치로 만든) primary가 있다는
    이유로 자동 approved 처리되어 cherry-pick 큐에서 사라지면 안 된다.
    """
    db, data = isolated
    cand_ids = asyncio.run(_seed_batch(db, data, batch_id="btc_q4", asset_key="hero_done", n=3))

    # 같은 (project, asset_key) 의 메인 asset을 미리 만들어둔다
    # (예: 어제 배치로 이미 승인된 상태). 이 사실만으로는 오늘 배치를
    # approved로 봐선 안 된다.
    async def make_main() -> None:
        existing = data / "primary.png"
        _make_png(existing)
        await db.upsert_scanned_asset(
            project="proj",
            asset_key="hero_done",
            category="character",
            image_path=str(existing),
            width=32, height=32, color_count=4, has_alpha=True,
            validation_status="pass", validation_message="ok",
        )

    asyncio.run(make_main())
    with TestClient(server.app) as client:
        r = client.get("/api/cherry-pick/queue")
    body = r.json()
    item = next(it for it in body["items"] if it["batch_id"] == "btc_q4")
    # 메인 asset이 존재해도, 이 batch에서 picked 된 candidate가 없으니 pending
    assert item["approved"] is False, (
        "메인 asset 존재만으로 새 batch를 approved로 판정하면 안 된다 "
        "(codex P1: list_today_batches 회귀)"
    )

    # 한 장 picked 처리 → 같은 batch가 approved로 바뀌어야 한다
    asyncio.run(db.mark_candidate_picked(cand_ids[0]))
    with TestClient(server.app) as client:
        r = client.get("/api/cherry-pick/queue")
    body = r.json()
    item = next(it for it in body["items"] if it["batch_id"] == "btc_q4")
    assert item["approved"] is True

    # approved batch는 pending_batches/ total_remaining 집계에서 빠진다
    pending = [b for b in body["items"] if not b.get("approved")]
    assert body["pending_batches"] == len(pending)
    assert body["total_remaining"] == sum(int(b["remaining"]) for b in pending)


def test_queue_approved_after_inline_key_edit_still_marks_original_batch(isolated) -> None:
    """inline asset_key 편집(=approve 시 다른 키로 보냄)으로 새 asset이 만들어져도,
    원본 batch의 candidate에 picked_at이 박히므로 batch는 approved로 계산된다.

    회귀 방지: codex P1 — "approving a candidate after editing the key inline never
    marks the original batch as done"."""
    db, data = isolated
    cand_ids = asyncio.run(_seed_batch(db, data, batch_id="btc_inline", asset_key="orig_key", n=3))

    # 원본 키와 다른 키로 picked 처리한다 (실 코드는
    # /api/assets/approve-from-candidate 가 mark_candidate_picked 를 호출).
    asyncio.run(db.mark_candidate_picked(cand_ids[0]))

    with TestClient(server.app) as client:
        r = client.get("/api/cherry-pick/queue")
    body = r.json()
    item = next(it for it in body["items"] if it["batch_id"] == "btc_inline")
    assert item["approved"] is True


def test_queue_since_filter_excludes_old_batches(isolated) -> None:
    db, data = isolated
    asyncio.run(_seed_batch(db, data, batch_id="btc_old", asset_key="hero", n=2))
    # 미래 시각으로 since를 주면 결과 없음
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with TestClient(server.app) as client:
        r = client.get("/api/cherry-pick/queue", params={"since": future})
    body = r.json()
    assert body["total_batches"] == 0
    assert body["pending_batches"] == 0
    assert body["total_remaining"] == 0
