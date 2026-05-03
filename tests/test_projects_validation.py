"""ensure_project_writable Depends 적용 회귀 가드.

design doc §"Archive Write Inventory (T3)" 와 §"자동 생성 흐름 (A2)" 의 모든
write 진입점이 invalid slug → 400, archived → 409, purging → 410 를 반환하는
지 hard assert. 누락 시 worker / candidate path 가 archived project 에 쓰는
buggy 회귀가 다시 등장한다.
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
    db = Database(tmp_path / "validation.db")
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


def test_batches_rejects_invalid_slug_with_suggestion(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/batches",
            json={
                "asset_key": "k",
                "project": "MyGame",
                "category": "character",
                "workflow_category": "sprite",
                "workflow_variants": ["x"],
                "prompts": ["hi"],
            },
        )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "invalid_project_slug"
    assert detail["suggestion"] == "mygame"


def test_workflows_generate_rejects_invalid_slug(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/generate",
            json={
                "project": "1invalid",
                "asset_key": "k",
                "workflow_category": "sprite",
                "workflow_variant": "x",
                "prompt": "x",
            },
        )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_project_slug"


def test_scan_rejects_invalid_slug(isolated, tmp_path: Path) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/projects/scan",
            json={
                "project": "MyGame",
                "root_path": str(tmp_path),
            },
        )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_project_slug"


def test_batches_archived_project_returns_409(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("locked-down"))
    asyncio.run(db.archive_project("locked-down"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/batches",
            json={
                "asset_key": "k",
                "project": "locked-down",
                "category": "character",
                "workflow_category": "sprite",
                "workflow_variants": ["x"],
                "prompts": ["hi"],
            },
        )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "project_archived"


def test_batches_purging_project_returns_410(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("being-purged"))
    asyncio.run(db.set_project_purge_status("being-purged", "purging"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/batches",
            json={
                "asset_key": "k",
                "project": "being-purged",
                "category": "character",
                "workflow_category": "sprite",
                "workflow_variants": ["x"],
                "prompts": ["hi"],
            },
        )
    assert r.status_code == 410
    assert r.json()["detail"]["error"] == "project_being_purged"


def test_unknown_valid_slug_auto_creates_registry_row(isolated) -> None:
    """미등록 + valid slug → 자동 등록 (이후 enqueue/expand 결과는 별도)."""
    db: Database = isolated
    assert asyncio.run(db.get_project("never-seen-slug")) is None
    with TestClient(server.app) as client:
        # workflow_variants 가 비어있어 expand 결과 비어 400 이 떨어지지만
        # ensure_project_writable 가 먼저 INSERT 하므로 그 효과는 남음.
        client.post(
            "/api/batches",
            json={
                "asset_key": "k",
                "project": "never-seen-slug",
                "category": "character",
                "workflow_category": "sprite",
                "workflow_variants": [],
                "prompts": [],
            },
        )
    after = asyncio.run(db.get_project("never-seen-slug"))
    assert after is not None
    assert after["slug"] == "never-seen-slug"


def test_archived_blocks_workflows_generate(isolated) -> None:
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("frozen"))
    asyncio.run(db.archive_project("frozen"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/generate",
            json={
                "project": "frozen",
                "asset_key": "k",
                "workflow_category": "sprite",
                "workflow_variant": "x",
                "prompt": "y",
            },
        )
    assert r.status_code == 409
