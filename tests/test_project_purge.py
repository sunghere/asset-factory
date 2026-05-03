"""Project purge (dry-run / confirm / tombstone / retry) 회귀 가드.

design doc §"Purge (Tombstone state machine)" + AGENTS.md §8 (file↔DB
정합성). 각 single transaction 단계가 깨지면 dangling row 또는 ghost dir
발생.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from models import Database


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "purge.db")
    asyncio.run(db.init())

    class _DummyBroker:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def publish(self, event):  # noqa: ANN001
            self.events.append(event)

    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    return db, tmp_path


def _seed_project_with_files(
    db: Database, tmp_path: Path, slug: str, *, archived: bool = False
) -> None:
    async def go() -> None:
        await db.upsert_project_idempotent(slug)
        if archived:
            await db.archive_project(slug)
        await db.upsert_scanned_asset(
            project=slug,
            asset_key="k1",
            category="character",
            image_path=str(tmp_path / "data" / "approved" / slug / "k1.png"),
            width=64,
            height=64,
            color_count=10,
            has_alpha=False,
            validation_status="pass",
            validation_message=None,
        )

    asyncio.run(go())
    # 디스크 디렉토리 + 파일 생성.
    for sub in ("approved", "candidates"):
        d = tmp_path / "data" / sub / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "f.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_purge_requires_archive_first(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "active-doomed")
    with TestClient(server.app) as client:
        r = client.post("/api/projects/active-doomed/purge?dry_run=true")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "purge_requires_archive"


def test_purge_dry_run_returns_counts_no_changes(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "ready-purge", archived=True)
    with TestClient(server.app) as client:
        r = client.post("/api/projects/ready-purge/purge?dry_run=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["will_delete"]["assets"] == 1
    assert body["will_delete"]["files"] == 2  # approved + candidates
    assert body["will_delete"]["bytes"] > 0
    assert body["blocking"] is None
    # DB 변경 없음.
    asyncio.run(db.get_project("ready-purge")) is not None


def test_purge_400_when_neither_flag_set(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "ready", archived=True)
    with TestClient(server.app) as client:
        r = client.post("/api/projects/ready/purge")
    assert r.status_code == 400


def test_purge_400_when_both_flags_true(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "ready", archived=True)
    with TestClient(server.app) as client:
        r = client.post("/api/projects/ready/purge?dry_run=true&confirm=true")
    assert r.status_code == 400


def test_purge_confirm_clears_db_and_disk(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "doomed", archived=True)
    approved = tmp / "data" / "approved" / "doomed"
    candidates = tmp / "data" / "candidates" / "doomed"
    assert approved.is_dir() and candidates.is_dir()
    with TestClient(server.app) as client:
        r = client.post("/api/projects/doomed/purge?confirm=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"]["assets"] == 1
    assert body["files_failed"] is False
    assert body["project_removed"] is True
    # DB row 사라짐.
    assert asyncio.run(db.get_project("doomed")) is None
    # 디스크 디렉토리 사라짐.
    assert not approved.exists()
    assert not candidates.exists()


def test_purge_dry_run_active_tasks_blocking(isolated) -> None:
    db, tmp = isolated

    async def seed_with_active_task() -> None:
        import aiosqlite

        await db.upsert_project_idempotent("busy")
        await db.archive_project("busy")
        await db.create_job("job-x", "generation", None)
        await db.enqueue_generation_task(
            {
                "job_id": "job-x",
                "project": "busy",
                "asset_key": "k",
                "category": "sprite",
                "prompt": "x",
                "negative_prompt": None,
                "model_name": "m",
                "width": 64,
                "height": 64,
                "steps": 1,
                "cfg": 1.0,
                "sampler": "DPM++ 2M",
                "expected_size": 64,
                "max_colors": 32,
                "max_retries": 3,
                "batch_id": "b1",
                "seed": 1,
            }
        )
        # 워커가 즉시 claim 해서 fail 처리하지 못하도록 next_attempt_at 을 미래로
        # 미루고 status 는 'queued' 그대로 유지 — active_task_count 카운트 대상.
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute(
                "UPDATE generation_tasks SET next_attempt_at=? WHERE project=?",
                ("2099-01-01T00:00:00+00:00", "busy"),
            )
            await conn.commit()

    asyncio.run(seed_with_active_task())
    with TestClient(server.app) as client:
        r = client.post("/api/projects/busy/purge?dry_run=true")
        confirm_r = client.post("/api/projects/busy/purge?confirm=true")
    body = r.json()
    assert body["blocking"] is not None and body["blocking"]["active_task_count"] >= 1
    # confirm 은 active_task_count > 0 → 409.
    assert confirm_r.status_code == 409
    assert confirm_r.json()["detail"]["error"] == "active_tasks_in_flight"


def test_purge_404_when_unknown(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post("/api/projects/unknown/purge?dry_run=true")
    assert r.status_code == 404


def test_purge_retry_404_when_not_in_purging(isolated) -> None:
    db, tmp = isolated
    _seed_project_with_files(db, tmp, "normal", archived=True)
    with TestClient(server.app) as client:
        r = client.post("/api/projects/normal/purge/retry")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_in_purging_state"


def test_purge_retry_completes_when_disk_clean(isolated) -> None:
    """purge_status='purging' 인 project (디스크 부재) 의 retry 가 row 제거."""
    db, tmp = isolated
    asyncio.run(db.upsert_project_idempotent("stuck"))
    asyncio.run(db.archive_project("stuck"))
    asyncio.run(db.set_project_purge_status("stuck", "purging"))
    # 디스크 디렉토리 없음 → retry 가 즉시 성공.
    with TestClient(server.app) as client:
        r = client.post("/api/projects/stuck/purge/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files_failed"] is False
    assert body["project_removed"] is True
    assert asyncio.run(db.get_project("stuck")) is None
