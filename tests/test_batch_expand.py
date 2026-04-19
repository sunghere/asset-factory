"""design batch 곱집합 expand + batch_id 부여 검증."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from models import Database


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "batch.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)
    return db


def test_expand_full_cartesian_product() -> None:
    """prompts(2) × models(2) × loras(3) × seeds(5) = 60."""
    spec = server.DesignBatchRequest(
        asset_key="marine",
        prompts=["p1", "p2"],
        models=["m1", "m2"],
        loras=[
            [],
            [server.LoraSpec(name="lora_a", weight=0.7)],
            [server.LoraSpec(name="lora_a", weight=0.5), server.LoraSpec(name="lora_b", weight=0.3)],
        ],
        seeds_per_combo=5,
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 2 * 2 * 3 * 5
    assert all(task["asset_key"] == "marine" for task in tasks)
    # LoRA 토큰이 prompt에 자동 append 되었는지 (한 칸이라도 있는 case)
    with_lora = [t for t in tasks if "<lora:" in t["prompt"]]
    assert len(with_lora) == 2 * 2 * 2 * 5  # lora가 비어있지 않은 2개 조합 × 나머지
    # lora_spec_json 직렬화가 일관되는지
    sample = next(t for t in tasks if t["lora_spec_json"] != "[]")
    parsed = json.loads(sample["lora_spec_json"])
    assert parsed[0]["name"].startswith("lora_")


def test_expand_explicit_seeds_overrides_per_combo() -> None:
    """seeds 명시 시 seeds_per_combo는 무시된다."""
    spec = server.DesignBatchRequest(
        asset_key="x",
        prompts=["p"],
        models=["m"],
        loras=[[]],
        seeds=[111, 222, 333],
        seeds_per_combo=64,
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 3
    assert sorted(t["seed"] for t in tasks) == [111, 222, 333]


def test_expand_default_when_models_or_loras_empty() -> None:
    """models/loras 비어있으면 [None]/[[]] 로 대체되어 task가 생성된다."""
    spec = server.DesignBatchRequest(
        asset_key="x", prompts=["p"], seeds=[1]
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 1
    assert tasks[0]["model_name"] is None
    assert json.loads(tasks[0]["lora_spec_json"]) == []


def test_expand_rejects_empty_prompts() -> None:
    spec = server.DesignBatchRequest(asset_key="x", prompts=[])
    with pytest.raises(ValueError):
        server.expand_design_batch(spec)


def test_post_batches_assigns_batch_id_and_enqueues_tasks(isolated: Database) -> None:
    body = {
        "asset_key": "marine_v2_idle",
        "project": "test-game",
        "prompts": ["a sad marine, pixel art"],
        "models": ["pixelart_xl_v1.5", "deliberate_v3"],
        "loras": [[{"name": "marine_uniform_v2", "weight": 0.7}]],
        "seeds_per_combo": 3,
        "common": {"steps": 28, "cfg": 7.0, "sampler": "DPM++ 2M", "max_colors": 32},
    }
    with TestClient(server.app) as client:
        r = client.post("/api/batches", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["batch_id"].startswith("btc_")
    assert data["expanded_count"] == 1 * 2 * 1 * 3  # 6

    async def check() -> None:
        # 모든 task가 같은 batch_id를 가져야 한다
        async with __import__("aiosqlite").connect(str(isolated.db_path)) as conn:
            cur = await conn.execute(
                "SELECT batch_id, COUNT(*) FROM generation_tasks GROUP BY batch_id"
            )
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == data["batch_id"]
            assert rows[0][1] == 6

    asyncio.run(check())


def test_post_batches_rejects_empty_prompts(isolated: Database) -> None:
    body = {"asset_key": "x", "prompts": []}
    with TestClient(server.app) as client:
        r = client.post("/api/batches", json=body)
    assert r.status_code == 400


def test_mcp_design_asset_returns_mcp_format(isolated: Database) -> None:
    body = {
        "asset_key": "x",
        "prompts": ["p"],
        "seeds": [42],
    }
    with TestClient(server.app) as client:
        r = client.post("/api/mcp/design_asset", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["isError"] is False
    assert isinstance(data["content"], list)
    assert "batch_id" in data["structuredContent"]


def test_recent_batches_lists_after_enqueue(isolated: Database) -> None:
    body = {"asset_key": "x", "prompts": ["p"], "seeds": [1, 2]}
    with TestClient(server.app) as client:
        r1 = client.post("/api/batches", json=body)
        assert r1.status_code == 200
        r2 = client.get("/api/batches?limit=10")
    assert r2.status_code == 200
    data = r2.json()
    assert data["count"] >= 1
    first = data["items"][0]
    assert first["asset_key"] == "x"
    assert first["total"] == 2
