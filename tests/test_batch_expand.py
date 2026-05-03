"""design batch 곱집합 expand + batch_id 부여 검증 (ComfyUI 워크플로우 기반).

A1111 시절의 ``models × loras × seeds`` 매트릭스가 아니라,
``prompts × workflow_variants × workflow_params_overrides × seeds`` 매트릭스로
expand 된다 — 실제 디스패치도 ``backend='comfyui'`` 로 하드코드.

회귀 가드: A1111 백엔드는 deprecated, 새 task 는 항상 ComfyUI 변형을 가리킨다.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import server
from models import Database


# 본 테스트는 실제 workflow_registry 를 사용한다 (sprite/pixel_alpha 등).
# fixture 로 mock 하지 않는 이유: registry 의 variant.available 분기, defaults
# 머지 동작이 expand_design_batch 의 실제 행동에 직접 영향을 주기 때문.
SPRITE_VARIANT = "pixel_alpha"


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


def _basic_spec(**overrides) -> server.DesignBatchRequest:
    """legacy prompt 모드 default — 테스트는 variant 의 subject min_chars 등을
    우회하고 expand 로직 자체만 검증한다. subject 모드 검증은
    test_prompt_resolution 쪽에 격리.
    """
    base = {
        "asset_key": "marine",
        "workflow_category": "sprite",
        "workflow_variants": [SPRITE_VARIANT],
        "prompts": ["sad marine character"],
        "prompt_mode": "legacy",
    }
    base.update(overrides)
    return server.DesignBatchRequest(**base)


# --- expand_design_batch 단위 ----------------------------------------------------

def test_expand_full_cartesian_product() -> None:
    """prompts(2) × variants(1) × overrides(2) × seeds(5) = 20."""
    spec = _basic_spec(
        prompts=["p1", "p2"],
        workflow_params_overrides=[{}, {"controlnet_strength": 0.7}],
        seeds_per_combo=5,
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 2 * 1 * 2 * 5
    assert all(task["asset_key"] == "marine" for task in tasks)
    assert all(task["backend"] == "comfyui" for task in tasks)
    assert all(task["workflow_category"] == "sprite" for task in tasks)
    assert all(task["workflow_variant"] == SPRITE_VARIANT for task in tasks)


def test_expand_workflow_params_json_serialized() -> None:
    """workflow_params_json 은 override 가 비지 않으면 직렬화된다."""
    spec = _basic_spec(
        workflow_params_overrides=[{}, {"controlnet_strength": 0.85}],
        seeds=[1],
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 2
    empty = next(t for t in tasks if t["workflow_params_json"] is None)
    filled = next(t for t in tasks if t["workflow_params_json"] is not None)
    assert empty["workflow_params_json"] is None
    parsed = json.loads(filled["workflow_params_json"])
    assert parsed == {"controlnet_strength": 0.85}


def test_expand_explicit_seeds_overrides_per_combo() -> None:
    """seeds 명시 시 seeds_per_combo 는 무시된다."""
    spec = _basic_spec(
        prompts=["p"],
        seeds=[111, 222, 333],
        seeds_per_combo=64,
    )
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 3
    assert sorted(t["seed"] for t in tasks) == [111, 222, 333]


def test_expand_uses_variant_defaults_when_common_fields_unset() -> None:
    """common.steps/cfg/sampler 가 None 이면 variant.defaults 가 채워진다."""
    spec = _basic_spec(seeds=[1])
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 1
    variant = server.workflow_registry.variant("sprite", SPRITE_VARIANT)
    assert tasks[0]["steps"] == int(variant.defaults["steps"])
    assert tasks[0]["cfg"] == float(variant.defaults["cfg"])
    assert tasks[0]["sampler"] == str(variant.defaults["sampler"])


def test_expand_common_overrides_variant_defaults() -> None:
    """common.steps 가 명시되면 variant default 보다 우선."""
    spec = _basic_spec(
        seeds=[1],
        common={"steps": 12, "cfg": 4.5, "sampler": "Euler a"},
    )
    tasks = server.expand_design_batch(spec)
    assert tasks[0]["steps"] == 12
    assert tasks[0]["cfg"] == 4.5
    assert tasks[0]["sampler"] == "Euler a"


def test_expand_rejects_empty_prompts() -> None:
    spec = _basic_spec(prompts=[])
    with pytest.raises(ValueError):
        server.expand_design_batch(spec)


def test_expand_rejects_empty_workflow_variants() -> None:
    spec = _basic_spec(workflow_variants=[])
    with pytest.raises(ValueError):
        server.expand_design_batch(spec)


def test_expand_rejects_unknown_variant() -> None:
    spec = _basic_spec(workflow_variants=["does_not_exist"])
    with pytest.raises(ValueError):
        server.expand_design_batch(spec)


def test_expand_default_overrides_when_unset() -> None:
    """workflow_params_overrides 가 비어있어도 기본 [{}] 로 1회 expand."""
    spec = _basic_spec(seeds=[1], workflow_params_overrides=[])
    tasks = server.expand_design_batch(spec)
    assert len(tasks) == 1
    assert tasks[0]["workflow_params_json"] is None


def test_expand_no_legacy_lora_or_model_fields() -> None:
    """A1111 잔재(model_name/lora_spec_json) 가 task 에 새는지 회귀 가드."""
    spec = _basic_spec(seeds=[1])
    tasks = server.expand_design_batch(spec)
    assert tasks[0]["model_name"] is None
    assert "lora_spec_json" not in tasks[0]


# --- POST /api/batches 통합 -----------------------------------------------------

def test_post_batches_assigns_batch_id_and_enqueues_tasks(isolated: Database) -> None:
    body = {
        "asset_key": "marine_v2_idle",
        "project": "test-game",
        "workflow_category": "sprite",
        "workflow_variants": [SPRITE_VARIANT],
        "workflow_params_overrides": [{}, {"controlnet_strength": 0.8}],
        "prompts": ["a sad marine, pixel art"],
        "prompt_mode": "legacy",
        "seeds_per_combo": 3,
        "common": {"max_colors": 32},
    }
    with TestClient(server.app) as client:
        r = client.post("/api/batches", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["batch_id"].startswith("btc_")
    # 1 prompt × 1 variant × 2 overrides × 3 seeds = 6
    assert data["expanded_count"] == 6

    async def check() -> None:
        async with __import__("aiosqlite").connect(str(isolated.db_path)) as conn:
            cur = await conn.execute(
                "SELECT batch_id, COUNT(*), MIN(backend), MIN(workflow_variant) "
                "FROM generation_tasks GROUP BY batch_id"
            )
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == data["batch_id"]
            assert rows[0][1] == 6
            assert rows[0][2] == "comfyui"
            assert rows[0][3] == SPRITE_VARIANT

    asyncio.run(check())


def test_post_batches_rejects_empty_prompts(isolated: Database) -> None:
    body = {
        "asset_key": "x",
        "workflow_category": "sprite",
        "workflow_variants": [SPRITE_VARIANT],
        "prompts": [],
    }
    with TestClient(server.app) as client:
        r = client.post("/api/batches", json=body)
    assert r.status_code == 400


def test_post_batches_rejects_unknown_variant(isolated: Database) -> None:
    body = {
        "asset_key": "x",
        "workflow_category": "sprite",
        "workflow_variants": ["nope"],
        "prompts": ["p"],
    }
    with TestClient(server.app) as client:
        r = client.post("/api/batches", json=body)
    assert r.status_code == 400


def test_mcp_design_asset_returns_mcp_format(isolated: Database) -> None:
    body = {
        "asset_key": "x",
        "workflow_category": "sprite",
        "workflow_variants": [SPRITE_VARIANT],
        "prompts": ["a marine sample prompt"],
        "prompt_mode": "legacy",
        "seeds": [42],
    }
    with TestClient(server.app) as client:
        r = client.post("/api/mcp/design_asset", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["isError"] is False
    assert isinstance(data["content"], list)
    assert "batch_id" in data["structuredContent"]


def test_recent_batches_surfaces_workflow_meta(isolated: Database) -> None:
    """/api/batches 응답이 workflow_category / workflow_variants 를 보여준다."""
    body = {
        "asset_key": "x",
        "workflow_category": "sprite",
        "workflow_variants": [SPRITE_VARIANT],
        "prompts": ["a marine sample prompt"],
        "prompt_mode": "legacy",
        "seeds": [1, 2],
    }
    with TestClient(server.app) as client:
        r1 = client.post("/api/batches", json=body)
        assert r1.status_code == 200, r1.text
        r2 = client.get("/api/batches?limit=10")
    assert r2.status_code == 200
    data = r2.json()
    assert data["count"] >= 1
    first = data["items"][0]
    assert first["asset_key"] == "x"
    assert first["total"] == 2
    assert first["workflow_category"] == "sprite"
    assert first["backend"] == "comfyui"
    assert SPRITE_VARIANT in first["workflow_variants"]
