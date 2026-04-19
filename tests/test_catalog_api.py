"""SD 카탈로그 API: A1111 mock + YAML merge 검증."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from catalog import load_catalog_yaml, merge_loras, merge_models
from generator import SDError
from models import Database


# ----------------------------------------------------------------------------
# pure unit: load + merge
# ----------------------------------------------------------------------------
def test_load_catalog_yaml_returns_empty_when_missing(tmp_path: Path) -> None:
    result = load_catalog_yaml(tmp_path / "missing.yml")
    assert result == {"models": {}, "loras": {}}


def test_load_catalog_yaml_normalizes_keys(tmp_path: Path) -> None:
    yml = tmp_path / "c.yml"
    yml.write_text(
        """
models:
  PixelArt_XL_v1.5:
    tags: [pixel_art]
loras:
  Marine_Uniform_v2.safetensors:
    weight_default: 0.7
    tags: [character]
""",
        encoding="utf-8",
    )
    cat = load_catalog_yaml(yml)
    # 키는 lowercase + 확장자 제거
    assert "pixelart_xl_v1.5" in cat["models"]
    assert "marine_uniform_v2" in cat["loras"]


def test_merge_models_attaches_metadata_when_match() -> None:
    sd = [
        {"model_name": "pixelart_xl_v1.5", "title": "pixelart_xl_v1.5 [abc]", "hash": "abc"},
        {"model_name": "no_meta_model", "title": "no_meta_model [xyz]"},
    ]
    catalog = {
        "models": {"pixelart_xl_v1.5": {"tags": ["pixel_art"], "notes": "강함"}},
        "loras": {},
    }
    merged = merge_models(sd, catalog)
    matched = next(m for m in merged if m["name"] == "pixelart_xl_v1.5")
    unmatched = next(m for m in merged if m["name"] == "no_meta_model")
    assert matched["tags"] == ["pixel_art"]
    assert matched["notes"] == "강함"
    assert matched["has_metadata"] is True
    assert unmatched["tags"] == []
    assert unmatched["has_metadata"] is False


def test_merge_loras_provides_default_weight_range() -> None:
    sd = [{"name": "marine_uniform_v2"}, {"name": "unknown_lora"}]
    catalog = {
        "models": {},
        "loras": {
            "marine_uniform_v2": {
                "weight_default": 0.6,
                "weight_range": [0.4, 1.0],
                "tags": ["military"],
            },
        },
    }
    merged = merge_loras(sd, catalog)
    matched = next(item for item in merged if item["name"] == "marine_uniform_v2")
    unmatched = next(item for item in merged if item["name"] == "unknown_lora")
    assert matched["weight_default"] == 0.6
    assert matched["weight_range"] == [0.4, 1.0]
    assert matched["tags"] == ["military"]
    assert unmatched["weight_default"] == 0.7
    assert unmatched["weight_range"] == [0.0, 1.0]
    assert unmatched["has_metadata"] is False


# ----------------------------------------------------------------------------
# endpoint: A1111 mock + YAML merge
# ----------------------------------------------------------------------------
class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "cat.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)
    yml = tmp_path / "sd_catalog.yml"
    yml.write_text(
        """
models:
  pixelart_xl_v1.5:
    tags: [pixel_art]
    notes: "강함"
loras:
  marine_uniform_v2:
    weight_default: 0.7
    weight_range: [0.4, 1.0]
    tags: [character, military]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "CATALOG_YAML_PATH", yml)
    return db


def test_models_endpoint_merges_yaml(isolated, monkeypatch) -> None:  # noqa: ANN001
    async def fake_list_models() -> list[dict]:
        return [
            {"model_name": "pixelart_xl_v1.5", "title": "pixelart_xl_v1.5 [abc]", "hash": "abc"},
            {"model_name": "deliberate_v3", "title": "deliberate_v3 [xyz]"},
        ]

    monkeypatch.setattr(server.sd_client, "list_models", fake_list_models)

    with TestClient(server.app) as client:
        r = client.get("/api/sd/catalog/models")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 2
    matched = next(item for item in data["items"] if item["name"] == "pixelart_xl_v1.5")
    assert matched["has_metadata"] is True
    assert matched["tags"] == ["pixel_art"]


def test_loras_endpoint_merges_yaml_and_defaults(isolated, monkeypatch) -> None:  # noqa: ANN001
    async def fake_list_loras() -> list[dict]:
        return [
            {"name": "marine_uniform_v2", "alias": "marine"},
            {"name": "unknown_lora"},
        ]

    monkeypatch.setattr(server.sd_client, "list_loras", fake_list_loras)

    with TestClient(server.app) as client:
        r = client.get("/api/sd/catalog/loras")
    assert r.status_code == 200, r.text
    data = r.json()
    matched = next(item for item in data["items"] if item["name"] == "marine_uniform_v2")
    unknown = next(item for item in data["items"] if item["name"] == "unknown_lora")
    assert matched["weight_default"] == 0.7
    assert matched["weight_range"] == [0.4, 1.0]
    assert matched["has_metadata"] is True
    assert unknown["has_metadata"] is False


def test_models_endpoint_returns_503_when_sd_unreachable(isolated, monkeypatch) -> None:  # noqa: ANN001
    async def boom() -> list[dict]:
        raise SDError("connection refused", code="unreachable")

    monkeypatch.setattr(server.sd_client, "list_models", boom)
    with TestClient(server.app) as client:
        r = client.get("/api/sd/catalog/models")
    assert r.status_code == 503
