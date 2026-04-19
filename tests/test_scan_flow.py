import asyncio
from pathlib import Path

import server
from models import Database
from PIL import Image


class _DummyEventBroker:
    async def publish(self, event):  # noqa: ANN001
        return None


def _write_minimal_png(path: Path) -> None:
    image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    image.save(path, format="PNG")


def test_scan_project_assets_updates_db_and_summary(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "scan.db")
        await db.init()

        root = tmp_path / "assets"
        (root / "sprites" / "cats").mkdir(parents=True)
        (root / "ui").mkdir(parents=True)
        (root / "items").mkdir(parents=True)

        _write_minimal_png(root / "sprites" / "cats" / "ksh_baby_idle.png")
        _write_minimal_png(root / "ui" / "btn_feed.png")
        _write_minimal_png(root / "items" / "fish_premium.png")

        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "event_broker", _DummyEventBroker())

        result = await server.scan_project_assets(
            server.ScanRequest(project="cat-raising", root_path=str(root), max_colors=32)
        )
        assert result["scanned_count"] == 3

        assets = await db.list_assets(project="cat-raising")
        by_key = {item["asset_key"]: item for item in assets}
        assert "ksh_baby_idle" in by_key
        assert by_key["ksh_baby_idle"]["category"] == "character"
        assert by_key["btn_feed"]["category"] == "ui"
        assert by_key["fish_premium"]["category"] == "item"

        summary = await db.get_asset_summary(project="cat-raising")
        assert summary["total"] == 3
        assert summary["by_status"]["pending"] == 3
        assert summary["by_category"]["character"] == 1
        assert summary["by_category"]["ui"] == 1
        assert summary["by_category"]["item"] == 1

    asyncio.run(scenario())
