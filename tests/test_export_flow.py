import asyncio
import json
from pathlib import Path

import server
from models import Database


class _DummyEventBroker:
    async def publish(self, event):  # noqa: ANN001
        return None


def test_export_copies_only_approved_assets_and_writes_manifest(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "export.db")
        await db.init()

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        approved_path = source_dir / "approved.png"
        pending_path = source_dir / "pending.png"
        approved_path.write_bytes(b"approved-image")
        pending_path.write_bytes(b"pending-image")

        await db.upsert_scanned_asset(
            project="cat-raising",
            asset_key="ksh_baby_idle",
            category="character",
            image_path=str(approved_path),
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
            image_path=str(pending_path),
            width=32,
            height=32,
            color_count=10,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )

        assets = await db.list_assets(project="cat-raising")
        approved_asset_id = next(item["id"] for item in assets if item["asset_key"] == "ksh_baby_idle")
        await db.update_asset_status(approved_asset_id, "approved")

        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "event_broker", _DummyEventBroker())

        output_dir = tmp_path / "exported"
        result = await server.export_assets(
            server.ExportRequest(
                project="cat-raising",
                output_dir=str(output_dir),
                save_manifest=True,
            )
        )

        assert result["exported_count"] == 1
        exported_file = output_dir / "cat-raising" / "character" / "ksh_baby_idle.png"
        assert exported_file.exists()
        assert not (output_dir / "cat-raising" / "ui" / "btn_feed.png").exists()

        manifest_path = Path(result["manifest_path"])
        assert manifest_path.exists()
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_data["count"] == 1
        assert manifest_data["items"][0]["asset_key"] == "ksh_baby_idle"

    asyncio.run(scenario())
