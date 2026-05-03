"""Worker-level archive guard 회귀 가드.

design doc §"Archive Write Inventory (T3)" — archive 후 in-flight task 결
과가 도착해도 candidate INSERT 가 skip 되어야 한다 (drain 일관성).
누락되면 archived project 가 새 candidate row 를 받아 cherry-pick UI 에 다
시 등장하는 회귀.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from models import Database


def test_archived_project_skips_candidate_insert(tmp_path: Path, caplog) -> None:  # noqa: ANN001
    """archived project 로 insert_asset_candidate 호출 → 0 반환 + log warn."""

    async def scenario() -> None:
        db = Database(tmp_path / "guard.db")
        await db.init()
        await db.upsert_project_idempotent("frozen-mid")
        await db.archive_project("frozen-mid")
        with caplog.at_level(logging.WARNING):
            cid = await db.insert_asset_candidate(
                project="frozen-mid",
                asset_key="k",
                slot_index=0,
                job_id="job1",
                image_path="/tmp/x.png",
                width=64,
                height=64,
                color_count=10,
                validation_status="pass",
                validation_message=None,
                generation_seed=1,
                generation_model=None,
                generation_prompt="p",
                metadata_json=None,
                batch_id="b1",
            )
        assert cid == 0, "archived project 에 INSERT 가 일어나면 안 됨"
        assert any("WORKER_ARCHIVED_SKIP" in rec.message for rec in caplog.records)

    asyncio.run(scenario())


def test_purging_project_skips_candidate_insert(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "purging.db")
        await db.init()
        await db.upsert_project_idempotent("being-purged")
        await db.set_project_purge_status("being-purged", "purging")
        cid = await db.insert_asset_candidate(
            project="being-purged",
            asset_key="k",
            slot_index=0,
            job_id="job1",
            image_path="/tmp/x.png",
            width=64,
            height=64,
            color_count=10,
            validation_status="pass",
            validation_message=None,
            generation_seed=1,
            generation_model=None,
            generation_prompt="p",
            metadata_json=None,
            batch_id="b1",
        )
        assert cid == 0

    asyncio.run(scenario())


def test_active_project_still_inserts_candidate(tmp_path: Path) -> None:
    """non-archived 정상 흐름 — 가드가 false positive 를 만들지 않는지 확인."""

    async def scenario() -> None:
        db = Database(tmp_path / "active.db")
        await db.init()
        await db.upsert_project_idempotent("active-proj")
        cid = await db.insert_asset_candidate(
            project="active-proj",
            asset_key="k",
            slot_index=0,
            job_id="job1",
            image_path="/tmp/x.png",
            width=64,
            height=64,
            color_count=10,
            validation_status="pass",
            validation_message=None,
            generation_seed=1,
            generation_model=None,
            generation_prompt="p",
            metadata_json=None,
            batch_id="b1",
        )
        assert cid > 0

    asyncio.run(scenario())


def test_quarantined_legacy_project_still_inserts(tmp_path: Path) -> None:
    """projects 테이블에 row 가 없는 legacy slug 는 worker 가 그대로 통과시켜야 한다.

    archive 가드는 등록된 project 의 archived/purging 만 막는다 — 검증되지 않은
    quarantined 값은 worker level 에서 추가 차단하지 않음 (write API 가 이미
    검증). 기존 in-flight 결과는 그대로 들어와야 read-only 보존이 일관.
    """

    async def scenario() -> None:
        db = Database(tmp_path / "legacy.db")
        await db.init()
        # projects 테이블에 row 없음 (quarantined slug 시뮬레이션은 init 후 직접
        # candidate insert 만 — get_project 은 None 을 반환).
        cid = await db.insert_asset_candidate(
            project="LegacyMixedCase",
            asset_key="k",
            slot_index=0,
            job_id="job1",
            image_path="/tmp/x.png",
            width=64,
            height=64,
            color_count=10,
            validation_status="pass",
            validation_message=None,
            generation_seed=1,
            generation_model=None,
            generation_prompt="p",
            metadata_json=None,
            batch_id="b1",
        )
        # row 가 만들어짐 (quarantined free-form 보존 — read-only).
        assert cid > 0

    asyncio.run(scenario())
