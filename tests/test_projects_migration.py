"""projects 테이블 마이그레이션 + quarantine 회귀 가드.

design doc §"마이그레이션 (T1: Quarantine)" / §"Decisions Locked - 11"
의 동작을 hard assert.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from models import (
    DEFAULT_PROJECT_SLUG,
    Database,
    is_valid_project_slug,
    suggest_project_slug,
    utc_now,
)


def _seed_legacy_rows(db_path: Path, projects: list[str]) -> None:
    """기존 free-form project 값을 legacy 테이블에 직접 INSERT (Database.init 전).

    현실적 마이그레이션 경로 — 운영 DB 처럼 projects 테이블이 비어있고
    assets/tasks/candidates 에만 free-form project 가 누적된 상태를 재현.
    """

    async def go() -> None:
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE assets (id TEXT PRIMARY KEY, project TEXT NOT NULL, "
                "asset_key TEXT NOT NULL, category TEXT NOT NULL, status TEXT, "
                "image_path TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, "
                "color_count INTEGER NOT NULL, has_alpha INTEGER NOT NULL, "
                "validation_status TEXT NOT NULL, validation_message TEXT, "
                "generation_seed INTEGER, generation_model TEXT, generation_prompt TEXT, "
                "metadata_json TEXT, approval_mode TEXT NOT NULL DEFAULT 'manual', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "UNIQUE(project, asset_key))"
            )
            await conn.execute(
                "CREATE TABLE generation_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "job_id TEXT NOT NULL, project TEXT NOT NULL, asset_key TEXT NOT NULL, "
                "category TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            await conn.execute(
                "CREATE TABLE asset_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "project TEXT NOT NULL, asset_key TEXT NOT NULL, slot_index INTEGER NOT NULL, "
                "job_id TEXT NOT NULL, image_path TEXT NOT NULL, width INTEGER NOT NULL, "
                "height INTEGER NOT NULL, color_count INTEGER NOT NULL, "
                "validation_status TEXT NOT NULL, created_at TEXT NOT NULL, "
                "UNIQUE(project, asset_key, slot_index, job_id))"
            )
            now = utc_now()
            for i, project in enumerate(projects):
                await conn.execute(
                    "INSERT OR IGNORE INTO assets(id, project, asset_key, category, "
                    "status, image_path, width, height, color_count, has_alpha, "
                    "validation_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'character', 'pending', '/tmp/x.png', 64, 64, 16, 0, 'pending', ?, ?)",
                    (f"a{i}", project, f"key{i}", now, now),
                )
            await conn.commit()

    asyncio.run(go())


def test_slug_validation_rules() -> None:
    """SLUG_REGEX + reserved + default-project 예외."""
    assert is_valid_project_slug("wooridul-factory")
    assert is_valid_project_slug("ab")
    assert is_valid_project_slug("p1-2-3")
    # default-project: reserved 'default' prefix 와 별개로 명시 허용 (마이그레이션 호환).
    assert is_valid_project_slug(DEFAULT_PROJECT_SLUG)
    # 위반 케이스.
    assert not is_valid_project_slug("a")  # 1자
    assert not is_valid_project_slug("MyGame")  # 대문자
    assert not is_valid_project_slug("my project")  # 공백
    assert not is_valid_project_slug("my_project")  # 언더스코어
    assert not is_valid_project_slug("1abc")  # 숫자 시작
    assert not is_valid_project_slug("admin")  # reserved
    assert not is_valid_project_slug("system")
    assert not is_valid_project_slug("")
    assert not is_valid_project_slug("a" * 41)


def test_suggest_slug_normalization() -> None:
    """suggestion 은 invalid input 을 lower + non-allowed→'-' 로 변환."""
    assert suggest_project_slug("MyGame") == "mygame"
    assert suggest_project_slug("my project!") == "my-project"
    assert suggest_project_slug("UPPER_CASE") == "upper-case"
    assert suggest_project_slug("123abc") == "p-123abc"
    assert suggest_project_slug("a" * 50) == "a" * 40
    assert suggest_project_slug("") == ""
    assert suggest_project_slug("---") == ""


def test_default_project_seeded_in_empty_db(tmp_path: Path) -> None:
    """빈 DB 부팅 시 default-project 가 자동 seed."""

    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.init()
        proj = await db.get_project(DEFAULT_PROJECT_SLUG)
        assert proj is not None
        assert proj["slug"] == DEFAULT_PROJECT_SLUG
        assert proj["display_name"] == "Default Project"
        assert proj["archived_at"] is None
        assert proj["purge_status"] is None

    asyncio.run(scenario())


def test_backfill_valid_slugs_from_legacy_rows(tmp_path: Path) -> None:
    """assets/tasks/candidates 의 valid project 들이 projects 에 백필됨."""
    db_path = tmp_path / "legacy.db"
    _seed_legacy_rows(db_path, ["wooridul-factory", "test-game", "another-one"])
    db = Database(db_path)
    asyncio.run(db.init())
    rows = asyncio.run(db.list_projects())
    slugs = {r["slug"] for r in rows}
    # default-project + 3 valid backfilled.
    assert slugs == {DEFAULT_PROJECT_SLUG, "wooridul-factory", "test-game", "another-one"}


def test_invalid_slugs_quarantined(tmp_path: Path) -> None:
    """invalid free-form 값은 projects 테이블에 안 들어감 (read-only quarantine)."""
    db_path = tmp_path / "quar.db"
    _seed_legacy_rows(
        db_path,
        ["valid-one", "MyGame", "with space", "default-project", "valid-two"],
    )
    db = Database(db_path)
    asyncio.run(db.init())
    rows = asyncio.run(db.list_projects())
    slugs = {r["slug"] for r in rows}
    assert "valid-one" in slugs
    assert "valid-two" in slugs
    assert "default-project" in slugs
    # quarantined.
    assert "MyGame" not in slugs
    assert "with space" not in slugs


def test_init_is_idempotent(tmp_path: Path) -> None:
    """init() 두 번 호출해도 projects row 가 중복 생성되지 않는다."""

    async def scenario() -> None:
        db = Database(tmp_path / "idem.db")
        await db.init()
        await db.init()
        rows = await db.list_projects()
        slugs = [r["slug"] for r in rows]
        assert slugs.count(DEFAULT_PROJECT_SLUG) == 1

    asyncio.run(scenario())


def test_upsert_project_idempotent_inserts_only_first_time(tmp_path: Path) -> None:
    """auto-create 게이트: 첫 호출은 INSERT, 두 번째는 no-op."""

    async def scenario() -> None:
        db = Database(tmp_path / "upsert.db")
        await db.init()
        first = await db.upsert_project_idempotent("brand-new-slug")
        assert first is True
        second = await db.upsert_project_idempotent("brand-new-slug")
        assert second is False
        proj = await db.get_project("brand-new-slug")
        assert proj is not None and proj["slug"] == "brand-new-slug"

    asyncio.run(scenario())


def test_archive_unarchive_round_trip(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "arch.db")
        await db.init()
        await db.upsert_project_idempotent("to-archive")
        archived = await db.archive_project("to-archive")
        assert archived is not None and archived["archived_at"] is not None
        # idempotent: 두 번 archive 해도 archived_at 가 안 바뀜.
        first_at = archived["archived_at"]
        archived_again = await db.archive_project("to-archive")
        assert archived_again["archived_at"] == first_at
        # unarchive.
        unarchived = await db.unarchive_project("to-archive")
        assert unarchived is not None and unarchived["archived_at"] is None

    asyncio.run(scenario())


def test_project_counts_zero_for_empty_project(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "counts.db")
        await db.init()
        await db.upsert_project_idempotent("empty-proj")
        c = await db.project_counts("empty-proj")
        assert c == {
            "asset_count": 0,
            "batch_count": 0,
            "candidate_count": 0,
            "active_task_count": 0,
            "last_active_at": None,
        }

    asyncio.run(scenario())


def test_list_projects_include_archived_filter(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "filter.db")
        await db.init()
        await db.upsert_project_idempotent("active-1")
        await db.upsert_project_idempotent("to-archive-1")
        await db.archive_project("to-archive-1")
        all_rows = await db.list_projects(include_archived=True)
        active_rows = await db.list_projects(include_archived=False)
        all_slugs = {r["slug"] for r in all_rows}
        active_slugs = {r["slug"] for r in active_rows}
        assert "to-archive-1" in all_slugs
        assert "to-archive-1" not in active_slugs
        assert "active-1" in active_slugs
        assert DEFAULT_PROJECT_SLUG in active_slugs

    asyncio.run(scenario())


def test_cascade_delete_removes_data(tmp_path: Path) -> None:
    """purge confirm 의 DB 삭제 단계가 3개 테이블을 한 번에 정리."""

    async def scenario() -> None:
        db = Database(tmp_path / "purge.db")
        await db.init()
        await db.upsert_project_idempotent("doomed")
        await db.upsert_scanned_asset(
            project="doomed",
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
        deleted = await db.cascade_delete_project_data("doomed")
        assert deleted["assets"] == 1
        # project row 자체는 caller 가 별도 transaction 에서 삭제.
        proj = await db.get_project("doomed")
        assert proj is not None
        await db.delete_project_row("doomed")
        proj_gone = await db.get_project("doomed")
        assert proj_gone is None

    asyncio.run(scenario())
