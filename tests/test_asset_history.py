"""에셋 이력 API 및 replace 시 asset_history 적재."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from models import Database
from validator import validate_asset


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "hist.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return db


def _make_png(path: Path) -> None:
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(path)


def test_replace_asset_primary_creates_history(isolated_db: Database, tmp_path: Path) -> None:
    img1 = tmp_path / "one.png"
    img2 = tmp_path / "two.png"
    _make_png(img1)
    _make_png(img2)

    async def run() -> None:
        await isolated_db.upsert_scanned_asset(
            project="p",
            asset_key="a1",
            category="sprite",
            image_path=str(img1),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        rows = await isolated_db.list_assets(project="p")
        asset_id = rows[0]["id"]
        v = validate_asset(img2)
        await isolated_db.replace_asset_primary_image(
            asset_id,
            image_path=str(img2),
            width=v.width,
            height=v.height,
            color_count=v.color_count,
            has_alpha=v.has_alpha,
            validation_status="pass" if v.passed else "fail",
            validation_message=v.message,
            generation_seed=123,
            generation_model="m",
            generation_prompt="p",
            metadata_json='{"steps":20}',
        )
        hist = await isolated_db.list_asset_history(asset_id)
        assert len(hist) == 1
        assert hist[0]["image_path"] == str(img1)

    asyncio.run(run())


def test_get_asset_history_endpoint(isolated_db: Database, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "api_key", None)
    img1 = tmp_path / "one.png"
    img2 = tmp_path / "two.png"
    _make_png(img1)
    _make_png(img2)

    async def prep() -> str:
        await isolated_db.upsert_scanned_asset(
            project="p",
            asset_key="a1",
            category="sprite",
            image_path=str(img1),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        rows = await isolated_db.list_assets(project="p")
        asset_id = rows[0]["id"]
        v = validate_asset(img2)
        await isolated_db.replace_asset_primary_image(
            asset_id,
            image_path=str(img2),
            width=v.width,
            height=v.height,
            color_count=v.color_count,
            has_alpha=v.has_alpha,
            validation_status="pass" if v.passed else "fail",
            validation_message=v.message,
            generation_seed=None,
            generation_model=None,
            generation_prompt=None,
            metadata_json=None,
        )
        return asset_id

    asset_id = asyncio.run(prep())
    with TestClient(server.app) as client:
        r = client.get(f"/api/assets/{asset_id}/history")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert str(img1) in data[0]["image_path"]


def test_get_asset_detail_endpoint(isolated_db: Database, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "api_key", None)
    img = tmp_path / "x.png"
    _make_png(img)

    async def prep() -> str:
        await isolated_db.upsert_scanned_asset(
            project="p",
            asset_key="key",
            category="ui",
            image_path=str(img),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        rows = await isolated_db.list_assets(project="p")
        return rows[0]["id"]

    asset_id = asyncio.run(prep())
    with TestClient(server.app) as client:
        r = client.get(f"/api/assets/{asset_id}/detail")
    assert r.status_code == 200
    assert r.json()["asset_key"] == "key"

