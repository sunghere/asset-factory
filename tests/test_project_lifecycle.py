"""Project archive/unarchive 엔드포인트 회귀 가드.

design doc §"Archive (Soft drain)". archive 후 새 write 거부, in-flight
task 자연 종료, unarchive 후 다시 write 가능.
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
    db = Database(tmp_path / "lifecycle.db")
    asyncio.run(db.init())

    class _DummyBroker:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def publish(self, event):  # noqa: ANN001
            self.events.append(event)

    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    return db


def test_archive_endpoint_sets_archived_at(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("to-archive"))
    with TestClient(server.app) as client:
        r = client.post("/api/projects/to-archive/archive")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived_at"] is not None
    # counts 도 노출.
    assert "asset_count" in body
    assert "active_task_count" in body


def test_archive_404_when_unknown(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post("/api/projects/never/archive")
    assert r.status_code == 404


def test_archive_blocks_new_writes(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("blocked"))
    with TestClient(server.app) as client:
        client.post("/api/projects/blocked/archive")
        r = client.post(
            "/api/batches",
            json={
                "asset_key": "k",
                "project": "blocked",
                "category": "character",
                "workflow_category": "sprite",
                "workflow_variants": ["x"],
                "prompts": ["hi"],
            },
        )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "project_archived"


def test_unarchive_round_trip_restores_writability(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("temp-archive"))
    with TestClient(server.app) as client:
        client.post("/api/projects/temp-archive/archive")
        r = client.post("/api/projects/temp-archive/unarchive")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived_at"] is None


def test_archive_idempotent(isolated) -> None:
    """두 번 archive 해도 archived_at 가 안 바뀐다 (drain 시작 시각 보존)."""
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("twice"))
    with TestClient(server.app) as client:
        r1 = client.post("/api/projects/twice/archive")
        r2 = client.post("/api/projects/twice/archive")
    assert r1.json()["archived_at"] == r2.json()["archived_at"]


def test_archive_purging_returns_409(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("being-purged"))
    asyncio.run(db.set_project_purge_status("being-purged", "purging"))
    with TestClient(server.app) as client:
        r = client.post("/api/projects/being-purged/archive")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "project_being_purged"


def test_unarchive_purging_returns_409(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("being-purged-2"))
    asyncio.run(db.archive_project("being-purged-2"))
    asyncio.run(db.set_project_purge_status("being-purged-2", "purging"))
    with TestClient(server.app) as client:
        r = client.post("/api/projects/being-purged-2/unarchive")
    assert r.status_code == 409
