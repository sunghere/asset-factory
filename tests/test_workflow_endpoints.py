"""/api/workflows/{catalog,generate} 엔드포인트 통합 테스트.

server.app 의 TestClient 를 띄우되 ComfyUI/SD 백엔드 호출은 일어나지 않게
한다 (worker 가 큐를 picking 해도 이 테스트에서는 task 가 enqueue 만 검증되고
worker 가 그것을 처리하기 전에 컨텍스트 매니저가 빠져나간다).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from models import Database
from sd_backend import A1111Backend, BackendRegistry, ComfyUIBackend
from workflow_registry import WorkflowRegistry


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


def _build_mini_registry(tmp_path: Path) -> WorkflowRegistry:
    """sprite/v (available) + pixel_bg/sdxl (needs_conversion) 를 가진 미니 레지스트리."""
    (tmp_path / "sprite").mkdir(parents=True)
    (tmp_path / "sprite" / "v.json").write_text(
        json.dumps({
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "OG"},
                  "_meta": {"title": "Positive Prompt"}},
            "11": {"class_type": "KSampler",
                    "inputs": {"seed": 0, "steps": 30, "cfg": 6.5,
                               "sampler_name": "dpmpp_2m", "scheduler": "karras"},
                    "_meta": {"title": "KSampler #1"}},
            "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
                   "_meta": {"title": "Save PixelAlpha"}},
        }),
        encoding="utf-8",
    )
    (tmp_path / "registry.yml").write_text(
        """
version: 1
categories:
  sprite:
    description: "테스트 스프라이트"
    variants:
      v:
        description: "테스트 변형"
        file: sprite/v.json
        primary: true
        outputs:
          - { node_title: "Save PixelAlpha", label: pixel_alpha, primary: true }
        defaults:
          steps: 30
          cfg: 6.5
          sampler: dpmpp_2m
  pixel_bg:
    description: "API 변환 필요"
    variants:
      sdxl:
        ui_file: pixel_bg/c1.json
        file: null
        status: needs_api_conversion
        defaults: {}
""",
        encoding="utf-8",
    )
    return WorkflowRegistry(root=tmp_path)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    """test_catalog_api.isolated 와 같은 패턴 — DB/broker/registry 격리."""
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)

    reg = _build_mini_registry(tmp_path / "wfroot")
    monkeypatch.setattr(server, "workflow_registry", reg)
    # backends 의 ComfyUIBackend 도 새 registry 기반으로 다시 만들어야 catalog 일관
    monkeypatch.setattr(
        server,
        "backends",
        BackendRegistry({
            "a1111": A1111Backend(server.sd_client),
            "comfyui": ComfyUIBackend(server.comfyui_client, reg),
        }),
    )
    # 디스크 가드는 테스트용으로 패스
    monkeypatch.setattr(server, "_ensure_disk_space_for_enqueue", lambda: None)
    return db


# ----------------------------------------------------------------------------
# /api/workflows/catalog
# ----------------------------------------------------------------------------


def test_catalog_endpoint_returns_registry_shape(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/catalog")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == 1
    cats = data["categories"]
    assert "sprite" in cats
    assert "pixel_bg" in cats
    sprite = cats["sprite"]
    assert sprite["primary_variant"] == "v"
    v = sprite["variants"]["v"]
    assert v["available"] is True
    assert v["status"] == "ready"
    assert v["outputs"] == [{"label": "pixel_alpha", "primary": True}]
    # needs_conversion 도 보임
    assert cats["pixel_bg"]["primary_variant"] is None
    assert cats["pixel_bg"]["variants"]["sdxl"]["available"] is False
    assert cats["pixel_bg"]["variants"]["sdxl"]["status"] == "needs_api_conversion"


# ----------------------------------------------------------------------------
# /api/workflows/generate — 정상 경로
# ----------------------------------------------------------------------------


def test_generate_endpoint_enqueues_single_task(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "hero",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "1girl, silver hair",
            "seed": 42,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["workflow_category"] == "sprite"
    assert body["workflow_variant"] == "v"
    assert body["candidates_total"] == 1
    assert body["primary_output"] == "pixel_alpha"

    # DB 검증 — task 1개 enqueue, backend=comfyui, workflow_* 채워짐
    rows = asyncio.run(_fetch_tasks(isolated, job_id))
    assert len(rows) == 1
    t = rows[0]
    assert t["backend"] == "comfyui"
    assert t["workflow_category"] == "sprite"
    assert t["workflow_variant"] == "v"
    assert t["seed"] == 42
    assert t["prompt"] == "1girl, silver hair"
    assert t["candidate_slot"] is None    # candidates_total=1 → slot 없음
    assert t["candidates_total"] == 1
    # variant.defaults 가 NOT NULL 컬럼에 채워졌는지
    assert t["steps"] == 30
    assert abs(t["cfg"] - 6.5) < 1e-6
    assert t["sampler"] == "dpmpp_2m"


def test_generate_endpoint_enqueues_n_candidates(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "hero",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "x",
            "seed": 100,
            "candidates_total": 4,
        })
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    rows = asyncio.run(_fetch_tasks(isolated, job_id))
    assert len(rows) == 4
    # seed 가 +1 씩 증가, slot 0..3
    seeds = sorted(t["seed"] for t in rows)
    slots = sorted(t["candidate_slot"] for t in rows)
    assert seeds == [100, 101, 102, 103]
    assert slots == [0, 1, 2, 3]
    for t in rows:
        assert t["candidates_total"] == 4
        assert t["backend"] == "comfyui"


def test_generate_endpoint_passes_workflow_params_json(isolated) -> None:  # noqa: ANN001
    """workflow_params 가 task.workflow_params_json 으로 직렬화."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "hero",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "x",
            "workflow_params": {
                "pose_image": "custom_pose.png",
                "controlnet_strength": 0.92,
                "lora_strengths": {"my_lora": 0.5},
            },
        })
    assert r.status_code == 200, r.text
    rows = asyncio.run(_fetch_tasks(isolated, r.json()["job_id"]))
    assert len(rows) == 1
    parsed = json.loads(rows[0]["workflow_params_json"])
    assert parsed["pose_image"] == "custom_pose.png"
    assert parsed["controlnet_strength"] == 0.92
    assert parsed["lora_strengths"] == {"my_lora": 0.5}


# ----------------------------------------------------------------------------
# /api/workflows/generate — 에러 경로
# ----------------------------------------------------------------------------


def test_generate_endpoint_404_unknown_variant(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "no_such",
            "prompt": "x",
        })
    assert r.status_code == 404
    assert "unknown variant" in r.json()["detail"]


def test_generate_endpoint_400_needs_conversion(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "pixel_bg",
            "workflow_variant": "sdxl",
            "prompt": "x",
        })
    assert r.status_code == 400
    assert "호출 불가" in r.json()["detail"]


def test_generate_endpoint_user_overrides_defaults(isolated) -> None:  # noqa: ANN001
    """request.steps/cfg/sampler 가 variant.defaults 보다 우선."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "x",
            "steps": 22,
            "cfg": 8.0,
            "sampler": "euler_a",
        })
    assert r.status_code == 200, r.text
    rows = asyncio.run(_fetch_tasks(isolated, r.json()["job_id"]))
    assert rows[0]["steps"] == 22
    assert abs(rows[0]["cfg"] - 8.0) < 1e-6
    assert rows[0]["sampler"] == "euler_a"


# ----------------------------------------------------------------------------
# health/sd 통합 — 양 백엔드 응답 형식
# ----------------------------------------------------------------------------


def test_health_sd_returns_both_backends(isolated, monkeypatch) -> None:  # noqa: ANN001
    """양쪽 백엔드 health 가 각각 호출되고 backend 별로 응답이 묶여야 한다."""
    async def fake_a1111_health() -> dict:
        return {"ok": True, "model_count": 3, "models": ["m1", "m2", "m3"]}

    async def fake_comfy_health() -> dict:
        return {"ok": True, "comfyui_version": "0.3.0", "device_count": 1, "device_names": ["cuda"]}

    monkeypatch.setattr(server.sd_client, "health_check", fake_a1111_health)
    monkeypatch.setattr(server.comfyui_client, "health_check", fake_comfy_health)

    with TestClient(server.app) as client:
        r = client.get("/api/health/sd")
    assert r.status_code == 200
    body = r.json()
    assert "a1111" in body["backends"]
    assert "comfyui" in body["backends"]
    assert body["backends"]["a1111"]["ok"] is True
    assert body["backends"]["a1111"]["backend"] == "a1111"
    assert body["backends"]["comfyui"]["ok"] is True


def test_health_sd_one_backend_dead_still_200(isolated, monkeypatch) -> None:  # noqa: ANN001
    """A1111 만 죽었어도 ComfyUI 가 살아있으면 200."""
    async def boom() -> dict:
        raise RuntimeError("a1111 down")

    async def fake_comfy_health() -> dict:
        return {"ok": True, "comfyui_version": "x"}

    monkeypatch.setattr(server.sd_client, "health_check", boom)
    monkeypatch.setattr(server.comfyui_client, "health_check", fake_comfy_health)

    with TestClient(server.app) as client:
        r = client.get("/api/health/sd")
    assert r.status_code == 200
    body = r.json()
    assert body["backends"]["a1111"]["ok"] is False
    assert "a1111 down" in body["backends"]["a1111"]["error"]
    assert body["backends"]["comfyui"]["ok"] is True


def test_health_sd_both_dead_returns_503(isolated, monkeypatch) -> None:  # noqa: ANN001
    async def boom_a() -> dict:
        raise RuntimeError("a1111 down")

    async def boom_c() -> dict:
        raise RuntimeError("comfyui down")

    monkeypatch.setattr(server.sd_client, "health_check", boom_a)
    monkeypatch.setattr(server.comfyui_client, "health_check", boom_c)

    with TestClient(server.app) as client:
        r = client.get("/api/health/sd")
    assert r.status_code == 503


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


async def _fetch_tasks(db: Database, job_id: str) -> list[dict]:
    """job_id 로 task row 들을 dict 리스트로 가져오기 (테스트 검증용)."""
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM generation_tasks WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
