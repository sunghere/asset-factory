"""후보 슬롯 선택 API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from models import Database


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "cand.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    data = tmp_path / "data"
    monkeypatch.setattr(server, "DATA_DIR", data)
    data.mkdir(parents=True, exist_ok=True)
    return db


def _make_png(path: Path) -> None:
    Image.new("RGBA", (32, 32), (5, 5, 5, 255)).save(path)


def test_select_candidate_promotes_slot_to_primary(isolated_db: Database, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "api_key", None)
    main = tmp_path / "main.png"
    slot1 = tmp_path / "slot1.png"
    _make_png(main)
    _make_png(slot1)

    async def prep() -> tuple[str, str]:
        await isolated_db.upsert_scanned_asset(
            project="proj",
            asset_key="char_idle",
            category="character",
            image_path=str(main),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        asset = (await isolated_db.list_assets(project="proj"))[0]
        job_id = "job-test-1"
        await isolated_db.insert_asset_candidate(
            project="proj",
            asset_key="char_idle",
            slot_index=1,
            job_id=job_id,
            image_path=str(slot1),
            width=32,
            height=32,
            color_count=4,
            validation_status="pass",
            validation_message="ok",
            generation_seed=99,
            generation_model="m",
            generation_prompt="prompt",
            metadata_json='{"steps":20,"max_colors":32}',
        )
        return asset["id"], job_id

    asset_id, job_id = asyncio.run(prep())

    with TestClient(server.app) as client:
        r = client.post(
            f"/api/assets/{asset_id}/select-candidate",
            json={"job_id": job_id, "slot_index": 1},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    async def check() -> None:
        asset = await isolated_db.get_asset(asset_id)
        assert asset is not None
        assert asset["image_path"] == str(main)
        hist = await isolated_db.list_asset_history(asset_id)
        assert len(hist) == 1

    asyncio.run(check())


def test_list_project_assets_filter(isolated_db: Database, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "api_key", None)
    p = tmp_path / "a.png"
    _make_png(p)

    async def prep() -> None:
        await isolated_db.upsert_scanned_asset(
            project="alpha",
            asset_key="x",
            category="ui",
            image_path=str(p),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )

    asyncio.run(prep())

    with TestClient(server.app) as client:
        r = client.get("/api/projects/alpha/assets")
    assert r.status_code == 200
    assert len(r.json()) == 1
