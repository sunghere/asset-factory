import asyncio
from pathlib import Path

from models import Database


def test_asset_summary_counts_by_status_validation_and_category(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "test.db")
        await db.init()

        await db.upsert_scanned_asset(
            project="cat-raising",
            asset_key="ksh_baby_idle",
            category="character",
            image_path="/tmp/a.png",
            width=64,
            height=64,
            color_count=20,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        await db.upsert_scanned_asset(
            project="cat-raising",
            asset_key="btn_feed",
            category="ui",
            image_path="/tmp/b.png",
            width=32,
            height=32,
            color_count=40,
            has_alpha=True,
            validation_status="fail",
            validation_message="palette",
        )
        assets = await db.list_assets(project="cat-raising")
        await db.update_asset_status(assets[0]["id"], "approved")

        summary = await db.get_asset_summary(project="cat-raising")
        assert summary["total"] == 2
        assert summary["by_status"]["approved"] == 1
        assert summary["by_status"]["pending"] == 1
        assert summary["by_validation"]["pass"] == 1
        assert summary["by_validation"]["fail"] == 1
        assert summary["by_category"]["character"] == 1
        assert summary["by_category"]["ui"] == 1

    asyncio.run(scenario())
