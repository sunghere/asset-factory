import asyncio
from pathlib import Path

from PIL import Image

import server
from models import Database


def _write_pass_png(path: Path) -> None:
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    image.save(path, format="PNG")


def _write_fail_png(path: Path) -> None:
    image = Image.new("RGBA", (8, 8))
    pixels = image.load()
    color = 0
    for y in range(8):
        for x in range(8):
            pixels[x, y] = (color % 256, (color * 3) % 256, (color * 7) % 256, 255)
            color += 1
    image.save(path, format="PNG")


def test_validate_all_respects_project_filter_and_counts_failures(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "validate.db")
        await db.init()

        root = tmp_path / "images"
        root.mkdir()
        pass_img = root / "ok.png"
        fail_img = root / "too_many_colors.png"
        other_project_img = root / "other.png"
        _write_pass_png(pass_img)
        _write_fail_png(fail_img)
        _write_pass_png(other_project_img)

        await db.upsert_scanned_asset(
            project="cat-raising",
            asset_key="ok_asset",
            category="character",
            image_path=str(pass_img),
            width=8,
            height=8,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        await db.upsert_scanned_asset(
            project="cat-raising",
            asset_key="fail_asset",
            category="character",
            image_path=str(fail_img),
            width=8,
            height=8,
            color_count=64,
            has_alpha=True,
            validation_status="fail",
            validation_message="palette",
        )
        await db.upsert_scanned_asset(
            project="other-project",
            asset_key="other_ok",
            category="ui",
            image_path=str(other_project_img),
            width=8,
            height=8,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )

        monkeypatch.setattr(server, "db", db)

        filtered = await server.validate_all_assets(project="cat-raising")
        assert filtered["project"] == "cat-raising"
        assert filtered["checked"] == 2
        assert filtered["failed"] == 1

        all_projects = await server.validate_all_assets(project=None)
        assert all_projects["checked"] == 3
        assert all_projects["failed"] == 1

    asyncio.run(scenario())
