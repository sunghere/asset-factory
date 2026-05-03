"""/api/projects CRUD 엔드포인트 (DB-backed) 회귀 가드.

design doc §"API 엔드포인트 요약" + §"`/api/projects` 응답 schema 상세".
AGENTS.md §6 — frontend 가 의존하는 키들을 server 에서 명시 derive 한다는
계약을 ``test_projects_api_response_shape`` 로 hard assert.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from models import DEFAULT_PROJECT_SLUG, Database


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "crud.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)  # require_api_key 우회
    return db


def test_list_projects_returns_default_seed(isolated) -> None:
    """빈 DB 부팅 직후 default-project 가 응답에 포함."""
    with TestClient(server.app) as client:
        r = client.get("/api/projects")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    slugs = {item["slug"] for item in body["items"]}
    assert DEFAULT_PROJECT_SLUG in slugs


def test_projects_api_response_shape(isolated) -> None:
    """응답 키 셋 hard assert (AGENTS.md §6 — FE↔BE 계약 회귀 가드).

    UI 는 이 키들에 의존하므로 server 가 누락하면 hidden breakage.
    """
    with TestClient(server.app) as client:
        r = client.get("/api/projects")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "default-project 가 항상 seed 되어 비어있을 수 없음"
    item = items[0]
    expected_keys = {
        "slug",
        "display_name",
        "description",
        "created_at",
        "archived_at",
        "purge_status",
        "asset_count",
        "batch_count",
        "candidate_count",
        "active_task_count",
        "last_active_at",
    }
    assert expected_keys.issubset(set(item.keys())), (
        f"missing keys: {expected_keys - set(item.keys())}"
    )


def test_create_project_explicit(isolated) -> None:
    """명시적 등록 — 신규 slug 가 응답에 즉시 반영."""
    with TestClient(server.app) as client:
        r = client.post(
            "/api/projects",
            json={"slug": "wooridul-factory", "display_name": "Wooridul Factory"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "wooridul-factory"
    assert body["display_name"] == "Wooridul Factory"
    assert body["archived_at"] is None
    assert body["asset_count"] == 0


def test_create_project_invalid_slug_returns_400_with_suggestion(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/projects",
            json={"slug": "MyGame", "display_name": "My Game"},
        )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "invalid_project_slug"
    assert detail["suggestion"] == "mygame"


def test_create_project_conflict_on_existing_slug(isolated) -> None:
    """기존에 default-project 가 seeded 되어 있으므로 동일 slug 로 POST 시 409."""
    with TestClient(server.app) as client:
        r = client.post(
            "/api/projects",
            json={"slug": DEFAULT_PROJECT_SLUG, "display_name": "Other"},
        )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "project_exists"


def test_get_project_detail(isolated) -> None:
    with TestClient(server.app) as client:
        client.post(
            "/api/projects",
            json={"slug": "alpha-proj", "display_name": "Alpha"},
        )
        r = client.get("/api/projects/alpha-proj")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "alpha-proj"
    assert body["asset_count"] == 0


def test_get_project_404_when_unknown(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.get("/api/projects/does-not-exist")
    assert r.status_code == 404


def test_patch_project_updates_display_name(isolated) -> None:
    with TestClient(server.app) as client:
        client.post(
            "/api/projects",
            json={"slug": "to-rename", "display_name": "Old Name"},
        )
        r = client.patch(
            "/api/projects/to-rename",
            json={"display_name": "New Name", "description": "A description"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "New Name"
    assert body["description"] == "A description"


def test_patch_project_404_when_unknown(isolated) -> None:
    with TestClient(server.app) as client:
        r = client.patch(
            "/api/projects/does-not-exist",
            json={"display_name": "X"},
        )
    assert r.status_code == 404


def test_list_projects_include_archived_filter(isolated) -> None:
    """``include_archived=false`` 시 archived 제외."""
    db: Database = isolated
    asyncio.run(db.upsert_project_idempotent("active-only"))
    asyncio.run(db.upsert_project_idempotent("archived-one"))
    asyncio.run(db.archive_project("archived-one"))
    with TestClient(server.app) as client:
        all_r = client.get("/api/projects?include_archived=true")
        active_r = client.get("/api/projects?include_archived=false")
    all_slugs = {i["slug"] for i in all_r.json()["items"]}
    active_slugs = {i["slug"] for i in active_r.json()["items"]}
    assert "archived-one" in all_slugs
    assert "archived-one" not in active_slugs
    assert "active-only" in active_slugs


def test_counts_reflect_seeded_data(isolated) -> None:
    """asset_count, candidate_count 가 실데이터 반영."""
    db: Database = isolated

    async def seed() -> None:
        await db.upsert_project_idempotent("counted")
        await db.upsert_scanned_asset(
            project="counted",
            asset_key="k1",
            category="character",
            image_path="/tmp/x.png",
            width=64,
            height=64,
            color_count=10,
            has_alpha=False,
            validation_status="pass",
            validation_message=None,
        )

    asyncio.run(seed())
    with TestClient(server.app) as client:
        r = client.get("/api/projects/counted")
    body = r.json()
    assert body["asset_count"] == 1
    assert body["candidate_count"] == 0
    assert body["batch_count"] == 0
