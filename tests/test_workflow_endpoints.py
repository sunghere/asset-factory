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
    """sprite/v (template 있음, subject 모드, §1.C tags 포함) + sprite/legacy_var
    (template 없음, legacy only, meta yaml 없음) + pixel_bg/sdxl (needs_conversion).

    §1.B subject-injection + §1.C recommend/search 두 흐름 모두 검증 가능한 통합
    fixture.
    """
    (tmp_path / "sprite").mkdir(parents=True)
    sample_workflow = json.dumps({
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "OG"},
              "_meta": {"title": "Positive Prompt"}},
        "11": {"class_type": "KSampler",
                "inputs": {"seed": 0, "steps": 30, "cfg": 6.5,
                           "sampler_name": "dpmpp_2m", "scheduler": "karras"},
                "_meta": {"title": "KSampler #1"}},
        "13": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"},
               "_meta": {"title": "Save PixelAlpha"}},
    })
    (tmp_path / "sprite" / "v.json").write_text(sample_workflow, encoding="utf-8")
    (tmp_path / "sprite" / "legacy_var.json").write_text(sample_workflow, encoding="utf-8")
    # §1.A 사이드카 — sprite/v 에 §1.B prompt_template + §1.C tags/use_cases 통합.
    (tmp_path / "sprite" / "v.meta.yaml").write_text(
        """
schema_version: 1
intent: "test variant"
output_layout:
  kind: pose_grid
  rows: 1
  cols: 3
  alpha: true
tags: [pixel-art, character, sprite, rpg]
use_cases:
  - "RPG character with pose sheet"
not_for:
  - "scenery — use bg/*"
prompt_template:
  base_positive: "pixel art, sprite sheet"
  base_negative: "blurry, low quality"
  user_slot:
    label: subject
    description: "subject only"
    required: true
    min_chars: 8
    max_chars: 400
  injection_rule: "{base_positive}, {subject}"
""",
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
        description: "subject 모드 변형 (template 있음)"
        file: sprite/v.json
        primary: true
        outputs:
          - { node_title: "Save PixelAlpha", label: pixel_alpha, primary: true }
        defaults:
          steps: 30
          cfg: 6.5
          sampler: dpmpp_2m
      legacy_var:
        description: "legacy 변형 (template 없음)"
        file: sprite/legacy_var.json
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
    # 디스크 가드는 테스트용으로 패스. 시그니처 호환: () 와 (expected_files=N) 둘 다 수용.
    monkeypatch.setattr(
        server, "_ensure_disk_space_for_enqueue", lambda *_a, **_kw: None,
    )
    return db


# ----------------------------------------------------------------------------
# /api/workflows/catalog
# ----------------------------------------------------------------------------


def test_catalog_endpoint_returns_registry_shape(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/catalog")
    assert r.status_code == 200, r.text
    data = r.json()
    # spec §1.A.3: version 1 → 2
    assert data["version"] == 2
    cats = data["categories"]
    assert "sprite" in cats
    assert "pixel_bg" in cats
    sprite = cats["sprite"]
    assert sprite["primary_variant"] == "v"
    v = sprite["variants"]["v"]
    assert v["available"] is True
    assert v["status"] == "ready"
    assert v["outputs"] == [{"label": "pixel_alpha", "primary": True}]
    # 메타 사이드카 v.meta.yaml 로드 — intent / tags / prompt_template 채워짐
    assert v["meta"]["intent"] == "test variant"
    assert "pixel-art" in v["meta"]["tags"]
    assert "character" in v["meta"]["tags"]
    assert v["meta"]["prompt_template"]["base_positive"] == "pixel art, sprite sheet"
    # legacy_var (사이드카 yaml 없음) 은 빈 meta — forward-compat
    legacy = cats["sprite"]["variants"]["legacy_var"]
    assert legacy["meta"]["intent"] == ""
    assert legacy["meta"]["prompt_template"] is None
    # needs_conversion 도 보임
    assert cats["pixel_bg"]["primary_variant"] is None
    assert cats["pixel_bg"]["variants"]["sdxl"]["available"] is False
    assert cats["pixel_bg"]["variants"]["sdxl"]["status"] == "needs_api_conversion"


# ----------------------------------------------------------------------------
# §1.C /api/workflows/recommend (POST) + /search (GET)
# ----------------------------------------------------------------------------


def test_recommend_endpoint_returns_candidates(isolated) -> None:  # noqa: ANN001
    """자연어 query → 후보 목록 + score + scoring_method='rule'."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/recommend", json={
            "query": "RPG character pixel sprite",
            "top": 3,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "RPG character pixel sprite"
    assert body["scoring_method"] == "rule"
    candidates = body["candidates"]
    assert len(candidates) >= 1
    top = candidates[0]
    assert top["variant"] == "sprite/v"
    assert top["score"] > 0.0
    assert "intent" in top
    assert "tags_hit" in top
    # 'character' 가 tags 와 use_cases 양쪽 매칭
    assert "character" in top["tags_hit"]


def test_recommend_endpoint_empty_query_returns_empty(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/recommend", json={"query": "   ", "top": 5})
    assert r.status_code == 200
    assert r.json()["candidates"] == []


def test_recommend_endpoint_top_truncates(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/recommend", json={
            "query": "pixel character",
            "top": 1,
        })
    assert r.status_code == 200
    assert len(r.json()["candidates"]) == 1


def test_recommend_endpoint_invalid_top_rejected(isolated) -> None:  # noqa: ANN001
    """top=0 또는 top=100 같은 범위 위반 → 422."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/recommend", json={"query": "x", "top": 100})
    assert r.status_code == 422


def test_search_endpoint_must_tag(isolated) -> None:  # noqa: ANN001
    """``?tag=pixel-art`` → 매칭 변형 목록."""
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/search?tag=pixel-art")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filters"] == {"tag": ["pixel-art"], "not": []}
    names = {m["variant"] for m in body["matches"]}
    assert "sprite/v" in names


def test_search_endpoint_multiple_must_tags_and(isolated) -> None:  # noqa: ANN001
    """다중 ``?tag=`` → AND."""
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/search?tag=pixel-art&tag=character")
    assert r.status_code == 200
    names = {m["variant"] for m in r.json()["matches"]}
    assert names == {"sprite/v"}


def test_search_endpoint_must_not_excludes(isolated) -> None:  # noqa: ANN001
    """``?not=`` 가지면 제외."""
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/search?tag=pixel-art&not=character")
    assert r.status_code == 200
    names = {m["variant"] for m in r.json()["matches"]}
    # sprite/v 가 'character' 가져 제외됨
    assert "sprite/v" not in names


def test_search_endpoint_no_filter_returns_all(isolated) -> None:  # noqa: ANN001
    """필터 미지정 → 모든 가용 변형 (sprite/v + sprite/legacy_var).

    legacy_var 는 meta yaml 부재라 tags 는 빈 list — 그래도 available 이니 포함.
    """
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/search")
    assert r.status_code == 200
    names = {m["variant"] for m in r.json()["matches"]}
    assert names == {"sprite/v", "sprite/legacy_var"}


def test_search_endpoint_unknown_tag_returns_empty(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.get("/api/workflows/search?tag=nonexistent-xyz")
    assert r.status_code == 200
    assert r.json()["matches"] == []


# ----------------------------------------------------------------------------
# /api/workflows/generate — 정상 경로
# ----------------------------------------------------------------------------


def test_generate_endpoint_enqueues_single_task(isolated) -> None:  # noqa: ANN001
    """legacy 모드 — prompt 통째 입력. §1.B 도입 후 기존 호환성 유지 검증."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "hero",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "1girl, silver hair",
            "prompt_mode": "legacy",
            "seed": 42,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["workflow_category"] == "sprite"
    assert body["workflow_variant"] == "v"
    assert body["candidates_total"] == 1
    assert body["primary_output"] == "pixel_alpha"
    # §1.B: 응답에 prompt_resolution 포함
    assert body["prompt_resolution"]["mode"] == "legacy"
    assert body["prompt_resolution"]["final_positive"] == "1girl, silver hair"

    # DB 검증 — task 1개 enqueue, backend=comfyui, workflow_* 채워짐
    rows = asyncio.run(_fetch_tasks(isolated, job_id))
    assert len(rows) == 1
    t = rows[0]
    assert t["backend"] == "comfyui"
    assert t["workflow_category"] == "sprite"
    assert t["workflow_variant"] == "v"
    assert t["seed"] == 42
    # legacy 모드 — DB prompt = request.prompt 그대로
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
            "prompt_mode": "legacy",
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
            "prompt_mode": "legacy",
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
            "prompt_mode": "legacy",
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


def test_health_sd_timeout_treated_as_dead_backend(isolated, monkeypatch) -> None:  # noqa: ANN001
    """백엔드 health_check 가 timeout 만큼 hang 해도 endpoint 가 빠르게 timeout error 로 마감.

    실 사고 시나리오: SD WebUI 가 죽은 채 SYN_SENT 가 30초 이상 hang →
    sd_client retries 까지 합치면 단일 요청에 141초가 묶인다. 이걸 endpoint
    레벨에서 짧게 잘라내서 /system, /catalog 화면이 무한 로딩되지 않도록 한다.
    """
    import asyncio as _asyncio

    async def hang() -> dict:
        await _asyncio.sleep(60)  # 절대 끝나지 않을 만큼
        return {"ok": True}

    async def fake_comfy_health() -> dict:
        return {"ok": True, "comfyui_version": "x"}

    monkeypatch.setattr(server.sd_client, "health_check", hang)
    monkeypatch.setattr(server.comfyui_client, "health_check", fake_comfy_health)
    # 테스트가 빠르게 끝나도록 0.1s 로 cap
    monkeypatch.setattr(server, "_SD_HEALTH_TIMEOUT_SECONDS", 0.1)

    import time as _time
    start = _time.monotonic()
    with TestClient(server.app) as client:
        r = client.get("/api/health/sd")
    elapsed = _time.monotonic() - start

    # ComfyUI 살아있으니 200 (한쪽만 죽었어도 200 정책)
    assert r.status_code == 200
    body = r.json()
    assert body["backends"]["a1111"]["ok"] is False
    assert "timeout" in body["backends"]["a1111"]["error"].lower()
    # 진짜 60초 안 기다리고 빨리 끝나야 한다 (cap 0.1s + overhead)
    assert elapsed < 5.0, f"endpoint should fail fast, took {elapsed:.2f}s"


def test_sd_catalog_models_timeout_returns_503_fast(isolated, monkeypatch) -> None:  # noqa: ANN001
    """list_models 가 hang 해도 endpoint 가 빠르게 503 으로 마감."""
    import asyncio as _asyncio
    import time as _time

    async def hang() -> list:
        await _asyncio.sleep(60)
        return []

    monkeypatch.setattr(server.sd_client, "list_models", hang)
    monkeypatch.setattr(server, "_SD_CATALOG_TIMEOUT_SECONDS", 0.1)

    start = _time.monotonic()
    with TestClient(server.app) as client:
        r = client.get("/api/sd/catalog/models")
    elapsed = _time.monotonic() - start

    assert r.status_code == 503
    assert "timeout" in r.json()["detail"].lower()
    assert elapsed < 5.0, f"endpoint should fail fast, took {elapsed:.2f}s"


def test_sd_catalog_loras_timeout_returns_503_fast(isolated, monkeypatch) -> None:  # noqa: ANN001
    """list_loras 가 hang 해도 endpoint 가 빠르게 503 으로 마감."""
    import asyncio as _asyncio
    import time as _time

    async def hang() -> list:
        await _asyncio.sleep(60)
        return []

    monkeypatch.setattr(server.sd_client, "list_loras", hang)
    monkeypatch.setattr(server, "_SD_CATALOG_TIMEOUT_SECONDS", 0.1)

    start = _time.monotonic()
    with TestClient(server.app) as client:
        r = client.get("/api/sd/catalog/loras")
    elapsed = _time.monotonic() - start

    assert r.status_code == 503
    assert "timeout" in r.json()["detail"].lower()
    assert elapsed < 5.0, f"endpoint should fail fast, took {elapsed:.2f}s"


# ── /api/comfyui/health ────────────────────────────────────────────────────
# A1111 deprecated 후 Catalog/System 이 1차 데이터로 사용. 항상 200 + ok 분기.


def test_comfyui_health_ok_returns_full_payload(isolated, monkeypatch) -> None:  # noqa: ANN001
    """ComfyUI 정상 시 200 + ok=true + system/queue/workflows 필드 포함."""

    async def fake_health() -> dict:
        return {
            "ok": True,
            "comfyui_version": "0.19.3",
            "python_version": "3.10.11",
            "device_count": 1,
            "device_names": ["cuda:0 NVIDIA GeForce RTX 4080 SUPER"],
        }

    async def fake_queue() -> dict:
        return {"queue_running": [{"prompt_id": "abc"}], "queue_pending": []}

    monkeypatch.setattr(server.comfyui_client, "health_check", fake_health)
    monkeypatch.setattr(server.comfyui_client, "queue_state", fake_queue)

    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["comfyui_version"] == "0.19.3"
    assert body["python_version"] == "3.10.11"
    assert body["device_count"] == 1
    assert body["device_names"] == ["cuda:0 NVIDIA GeForce RTX 4080 SUPER"]
    assert body["queue"] == {"running": 1, "pending": 0}
    assert body["host"].startswith("http://")
    assert body["fetched_at"]  # ISO timestamp 비어있지 않음
    # workflow_registry 가 fixture 환경에서도 0 이상은 로드함
    assert isinstance(body["workflows_available"], int)
    assert body["workflows_available"] >= 0


def test_comfyui_health_timeout_returns_200_with_ok_false(isolated, monkeypatch) -> None:  # noqa: ANN001
    """ComfyUI hang → 5초(여기선 0.1s cap) 안에 200 + ok=false + error 필드.

    /api/health/sd 와 달리 503 을 안 낸다. 프론트가 status code 분기 없이
    단일 응답으로 판단하도록 하기 위함 (PLAN_comfyui_catalog.md §3.1.1 정책).
    """
    import asyncio as _asyncio
    import time as _time

    async def hang() -> dict:
        await _asyncio.sleep(60)
        return {}

    monkeypatch.setattr(server.comfyui_client, "health_check", hang)
    monkeypatch.setattr(server, "_COMFYUI_HEALTH_TIMEOUT_SECONDS", 0.1)

    start = _time.monotonic()
    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/health")
    elapsed = _time.monotonic() - start

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "timeout" in body["error"].lower()
    assert body["host"].startswith("http://")
    assert elapsed < 5.0, f"endpoint should fail fast, took {elapsed:.2f}s"


def test_comfyui_health_exception_returns_200_with_ok_false(isolated, monkeypatch) -> None:  # noqa: ANN001
    """ComfyUI 가 RuntimeError 등 raise 해도 200 + ok=false + error 메시지 보존."""

    async def boom() -> dict:
        raise RuntimeError("ComfyUI process gone")

    monkeypatch.setattr(server.comfyui_client, "health_check", boom)

    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "RuntimeError" in body["error"]
    assert "ComfyUI process gone" in body["error"]


def test_comfyui_health_queue_failure_propagates_as_ok_false(isolated, monkeypatch) -> None:  # noqa: ANN001
    """system_stats 는 OK 인데 /queue 호출이 실패해도 ok=false 로 마감.

    health 와 queue 가 한 timeout block 안에서 순차 호출되므로, 둘 중 하나라도
    실패하면 전체 ok=false. (부분 성공 응답을 허용하면 프론트 로직이 복잡해짐.)
    """

    async def fake_health() -> dict:
        return {"ok": True, "comfyui_version": "0.19.3"}

    async def boom_queue() -> dict:
        raise RuntimeError("queue endpoint flaky")

    monkeypatch.setattr(server.comfyui_client, "health_check", fake_health)
    monkeypatch.setattr(server.comfyui_client, "queue_state", boom_queue)

    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "queue endpoint flaky" in body["error"]


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


# ----------------------------------------------------------------------------
# Bypass 모드 (P0-1)
# ----------------------------------------------------------------------------


def test_generate_default_approval_mode_is_manual(isolated) -> None:  # noqa: ANN001
    """approval_mode 미지정 → task 에 'manual' 저장."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "a",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "p",
            "prompt_mode": "legacy",
        })
    assert r.status_code == 200, r.text
    assert r.json()["approval_mode"] == "manual"
    rows = asyncio.run(_fetch_tasks(isolated, r.json()["job_id"]))
    assert rows[0]["approval_mode"] == "manual"


def test_generate_bypass_mode_persists_flag(isolated) -> None:  # noqa: ANN001
    """approval_mode='bypass' 가 task 에 그대로 저장되고 응답에 echo."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "tmp_sketch",
            "asset_key": "a",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "p",
            "prompt_mode": "legacy",
            "approval_mode": "bypass",
        })
    assert r.status_code == 200, r.text
    assert r.json()["approval_mode"] == "bypass"
    rows = asyncio.run(_fetch_tasks(isolated, r.json()["job_id"]))
    assert rows[0]["approval_mode"] == "bypass"


def test_generate_invalid_approval_mode_rejected(isolated) -> None:  # noqa: ANN001
    """Literal 검증 — 'auto' 등 미정의 값은 422."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "proj",
            "asset_key": "a",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "p",
            "prompt_mode": "legacy",
            "approval_mode": "auto",
        })
    assert r.status_code == 422


# ----------------------------------------------------------------------------
# §1.B subject-injection — generate 응답 + DB + /api/jobs/{id}
# ----------------------------------------------------------------------------


def test_generate_subject_mode_returns_resolution(isolated) -> None:  # noqa: ANN001
    """subject 모드 — 응답에 prompt_resolution 노출, DB prompt 가 final_positive."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "prompt": "",     # legacy 통째 입력 안 함
            "subject": "1girl, silver hair, blue dress, holding sword",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    pr = body["prompt_resolution"]
    assert pr["mode"] == "subject"
    assert pr["user_slot"] == "subject"
    assert pr["user_input"] == "1girl, silver hair, blue dress, holding sword"
    assert "pixel art, sprite sheet" in pr["final_positive"]
    assert "1girl, silver hair" in pr["final_positive"]
    assert pr["final_negative"] == "blurry, low quality"

    # DB 의 prompt = final_positive (실제 ComfyUI 디스패치 값)
    rows = asyncio.run(_fetch_tasks(isolated, body["job_id"]))
    assert rows[0]["prompt"] == pr["final_positive"]
    assert rows[0]["negative_prompt"] == pr["final_negative"]
    # prompt_resolution_json 도 저장됨
    saved = json.loads(rows[0]["prompt_resolution_json"])
    assert saved["mode"] == "subject"


def test_generate_subject_mode_too_short_returns_400(isolated) -> None:  # noqa: ANN001
    """subject 모드 — min_chars=8 위반 시 HTTP 400 + code=subject_length_invalid."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "subject": "short",   # < 8 chars
        })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "subject_length_invalid"


def test_generate_subject_mode_empty_returns_400(isolated) -> None:  # noqa: ANN001
    """subject 모드 — required=True 인데 빈 입력 → HTTP 400 + code=subject_required."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "subject": "",
            "prompt_mode": "subject",
        })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "subject_required"


def test_generate_subject_mode_with_legacy_variant_returns_400(isolated) -> None:  # noqa: ANN001
    """prompt_mode=subject 인데 변형에 prompt_template 없음 → HTTP 400."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "legacy_var",   # template 없음
            "prompt": "x",
            "subject": "1girl, silver hair",
            "prompt_mode": "subject",
        })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "prompt_template_missing_for_variant"


def test_generate_subject_mode_negative_appended(isolated) -> None:  # noqa: ANN001
    """subject 모드 — user negative 가 base_negative 뒤에 추가만 (override X)."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "subject": "1girl, silver hair, blue dress",
            "negative_prompt": "extra negative tokens",
        })
    assert r.status_code == 200
    pr = r.json()["prompt_resolution"]
    assert "blurry" in pr["final_negative"]      # base 유지
    assert "extra negative tokens" in pr["final_negative"]    # user 추가


def test_generate_subject_style_extra_appended(isolated) -> None:  # noqa: ANN001
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "subject": "1girl, silver hair",
            "style_extra": "cinematic lighting",
        })
    assert r.status_code == 200
    pr = r.json()["prompt_resolution"]
    assert pr["final_positive"].endswith("cinematic lighting")


def test_generate_legacy_variant_falls_through(isolated) -> None:  # noqa: ANN001
    """prompt_template 없는 변형 — 자동 legacy 모드 (mode='legacy')."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "legacy_var",
            "prompt": "user-written full prompt with all keywords",
        })
    assert r.status_code == 200
    pr = r.json()["prompt_resolution"]
    assert pr["mode"] == "legacy"
    assert pr["final_positive"] == "user-written full prompt with all keywords"


def test_get_job_returns_prompt_resolution(isolated) -> None:  # noqa: ANN001
    """/api/jobs/{id} 응답에 prompt_resolution 노출 (DB 에서 재조회)."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "v",
            "subject": "1girl, silver hair, blue dress",
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        # GET /api/jobs/{id}
        j = client.get(f"/api/jobs/{job_id}")
    assert j.status_code == 200
    body = j.json()
    assert body["prompt_resolution"]["mode"] == "subject"
    assert "pixel art" in body["prompt_resolution"]["final_positive"]


def test_get_job_legacy_variant_prompt_resolution_legacy(isolated) -> None:  # noqa: ANN001
    """legacy 변형 호출 시 /api/jobs/{id} 의 prompt_resolution.mode='legacy'."""
    with TestClient(server.app) as client:
        r = client.post("/api/workflows/generate", json={
            "project": "p", "asset_key": "k",
            "workflow_category": "sprite",
            "workflow_variant": "legacy_var",
            "prompt": "user prompt full",
        })
        job_id = r.json()["job_id"]
        j = client.get(f"/api/jobs/{job_id}")
    assert j.json()["prompt_resolution"]["mode"] == "legacy"


def _insert_bypass_asset(db, project: str, asset_key: str) -> str:
    """테스트용: 직접 assets 테이블에 bypass 행 삽입."""
    import aiosqlite
    import uuid as _uuid

    async def _go() -> str:
        asset_id = str(_uuid.uuid4())
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status,
                    image_path, width, height, color_count,
                    has_alpha, validation_status,
                    approval_mode, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, 'sprite', 'approved', '/tmp/x.png',
                          64, 64, 16, 0, 'pass', 'bypass',
                          '2026-01-01T00:00:00', '2026-01-01T00:00:00')
                """,
                (asset_id, project, asset_key),
            )
            await conn.commit()
        return asset_id

    return asyncio.run(_go())


def _insert_manual_asset(db, project: str, asset_key: str) -> str:
    """테스트용: 직접 assets 테이블에 manual(approved) 행 삽입."""
    import aiosqlite
    import uuid as _uuid

    async def _go() -> str:
        asset_id = str(_uuid.uuid4())
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status,
                    image_path, width, height, color_count,
                    has_alpha, validation_status,
                    approval_mode, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, 'sprite', 'approved', '/tmp/m.png',
                          64, 64, 16, 0, 'pass', 'manual',
                          '2026-01-01T00:00:00', '2026-01-01T00:00:00')
                """,
                (asset_id, project, asset_key),
            )
            await conn.commit()
        return asset_id

    return asyncio.run(_go())


def test_list_assets_excludes_bypass_by_default(isolated) -> None:  # noqa: ANN001
    """include_bypassed 미지정 → bypass 자산 안 보임."""
    _insert_manual_asset(isolated, "p", "manual_one")
    _insert_bypass_asset(isolated, "p", "bypass_one")
    with TestClient(server.app) as client:
        r = client.get("/api/projects/p/assets")
    assert r.status_code == 200
    keys = {a["asset_key"] for a in r.json()}
    assert keys == {"manual_one"}


def test_list_assets_includes_bypassed_with_flag(isolated) -> None:  # noqa: ANN001
    _insert_manual_asset(isolated, "p", "manual_one")
    _insert_bypass_asset(isolated, "p", "bypass_one")
    with TestClient(server.app) as client:
        r = client.get("/api/projects/p/assets?include_bypassed=true")
    assert r.status_code == 200
    keys = {a["asset_key"] for a in r.json()}
    assert keys == {"manual_one", "bypass_one"}


def test_legacy_assets_endpoint_also_filters_bypass(isolated) -> None:  # noqa: ANN001
    _insert_manual_asset(isolated, "p", "manual_one")
    _insert_bypass_asset(isolated, "p", "bypass_one")
    with TestClient(server.app) as client:
        default = client.get("/api/assets?project=p").json()
        with_flag = client.get("/api/assets?project=p&include_bypassed=true").json()
    assert {a["asset_key"] for a in default} == {"manual_one"}
    assert {a["asset_key"] for a in with_flag} == {"manual_one", "bypass_one"}


def test_assets_response_exposes_approval_mode_field(isolated) -> None:  # noqa: ANN001
    """클라이언트가 한 번의 호출로 'bypass' 여부 식별 가능 — DB 응답 직렬화에서
    approval_mode 가 빠지지 않게 보장 (마이그레이션 안 돈 구 DB 안전망 포함).
    """
    _insert_manual_asset(isolated, "p", "manual_one")
    _insert_bypass_asset(isolated, "p", "bypass_one")
    with TestClient(server.app) as client:
        rows = client.get("/api/projects/p/assets?include_bypassed=true").json()
    by_key = {a["asset_key"]: a for a in rows}
    assert by_key["manual_one"]["approval_mode"] == "manual"
    assert by_key["bypass_one"]["approval_mode"] == "bypass"


def test_assets_legacy_endpoint_also_exposes_approval_mode(isolated) -> None:  # noqa: ANN001
    _insert_manual_asset(isolated, "p", "m")
    _insert_bypass_asset(isolated, "p", "b")
    with TestClient(server.app) as client:
        rows = client.get("/api/assets?project=p&include_bypassed=true").json()
    by_key = {a["asset_key"]: a for a in rows}
    assert by_key["m"]["approval_mode"] == "manual"
    assert by_key["b"]["approval_mode"] == "bypass"


def test_export_manifest_includes_excluded_bypassed_field(isolated) -> None:  # noqa: ANN001
    """/api/export/manifest GET 응답에도 항상 excluded_bypassed 키가 들어감."""
    _insert_manual_asset(isolated, "p", "m1")
    _insert_bypass_asset(isolated, "p", "b1")
    with TestClient(server.app) as client:
        body = client.get("/api/export/manifest?project=p").json()
    assert body["count"] == 1
    assert body["excluded_bypassed"] == 1


def test_export_manifest_empty_project_still_has_excluded_bypassed(isolated) -> None:  # noqa: ANN001
    """asset 없는 프로젝트라도 excluded_bypassed 가 0 (null/누락 아님)."""
    with TestClient(server.app) as client:
        body = client.get("/api/export/manifest?project=does_not_exist").json()
    assert body["excluded_bypassed"] == 0
    assert body["items"] == []


def test_health_reports_bypass_retention_days(isolated, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AF_BYPASS_RETENTION_DAYS", "3")
    with TestClient(server.app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "asset-factory"
    assert body["bypass_retention_days"] == 3.0


def test_health_default_retention_when_env_unset(isolated, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AF_BYPASS_RETENTION_DAYS", raising=False)
    with TestClient(server.app) as client:
        body = client.get("/api/health").json()
    assert body["bypass_retention_days"] == 7.0


def test_approve_from_bypass_candidate_preserves_approval_mode(
    isolated, tmp_path
) -> None:  # noqa: ANN001
    """bypass candidate 가 cherry-pick approve 를 거쳐도 새 asset 이 bypass 격리.

    Regression: 이전에 approve_from_candidate 가 candidate.approval_mode 를 무시
    해 새 asset 이 default 'manual' 로 떨어졌었다 → 일반 list/export 에 leak.
    """
    import aiosqlite

    # candidate 파일 + DB 행
    cand_dir = tmp_path / "candidates" / "tmp_sketch" / "k"
    cand_dir.mkdir(parents=True)
    cand_path = cand_dir / "slot_0.png"
    # 10x10 PNG (validate_asset 가 PIL 로 열어야 하므로 실제 PNG 필요)
    from PIL import Image
    Image.new("RGBA", (10, 10), (200, 50, 50, 255)).save(cand_path)

    async def _seed() -> int:
        async with aiosqlite.connect(isolated.db_path) as conn:
            cur = await conn.execute(
                """INSERT INTO asset_candidates (
                    project, asset_key, slot_index, job_id, image_path,
                    width, height, color_count, validation_status, validation_message,
                    generation_seed, generation_model, generation_prompt, metadata_json,
                    batch_id, is_rejected, approval_mode, created_at
                ) VALUES (?, ?, 0, 'job-bp', ?, 10, 10, 16, 'pass', NULL,
                          NULL, NULL, NULL, NULL, NULL, 0, 'bypass',
                          '2026-01-01T00:00:00')""",
                ("tmp_sketch", "k", str(cand_path)),
            )
            await conn.commit()
            return int(cur.lastrowid)

    cand_id = asyncio.run(_seed())

    # path allowlist 우회 (export 테스트와 동일)
    import server as _server
    orig = _server._ensure_path_allowed
    _server._ensure_path_allowed = lambda p: p  # type: ignore[assignment]
    try:
        with TestClient(server.app) as client:
            r = client.post(
                "/api/assets/approve-from-candidate",
                json={"candidate_id": cand_id, "set_status": "approved"},
            )
        assert r.status_code == 200, r.text
        # 새 asset 의 approval_mode 가 bypass 인지 검증
        async def _check() -> str | None:
            async with aiosqlite.connect(isolated.db_path) as conn:
                cur = await conn.execute(
                    "SELECT approval_mode FROM assets WHERE project='tmp_sketch'"
                )
                row = await cur.fetchone()
                return row[0] if row else None
        approval_mode = asyncio.run(_check())
        assert approval_mode == "bypass", \
            f"bypass candidate → asset 의 approval_mode 는 'bypass' 여야 함 (실제: {approval_mode})"

        # default list 에서는 안 보임
        listing = client.get("/api/projects/tmp_sketch/assets").json()
        assert listing == [], "bypass asset 은 기본 list 에서 제외돼야 함"
        listing_with = client.get(
            "/api/projects/tmp_sketch/assets?include_bypassed=true"
        ).json()
        assert len(listing_with) == 1
    finally:
        _server._ensure_path_allowed = orig  # type: ignore[assignment]


def test_export_excludes_bypass_and_reports_count(isolated, tmp_path) -> None:  # noqa: ANN001
    """export 는 manual 자산만 복사하고 응답의 excluded_bypassed 로 제외 수 알림."""
    # 실제 이미지 파일까지 만들어야 export 가 copy2 함
    import shutil

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    manual_img = src_dir / "manual.png"
    bypass_img = src_dir / "bypass.png"
    manual_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    bypass_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 32)

    import aiosqlite

    async def _seed() -> None:
        async with aiosqlite.connect(isolated.db_path) as conn:
            await conn.execute(
                "INSERT INTO assets (id, job_id, project, asset_key, category, status, "
                "image_path, width, height, color_count, has_alpha, validation_status, "
                "approval_mode, created_at, updated_at) VALUES "
                "('m1', NULL, 'p', 'm1', 'sprite', 'approved', ?, 64, 64, 16, 0, 'pass', "
                "'manual', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                (str(manual_img),),
            )
            await conn.execute(
                "INSERT INTO assets (id, job_id, project, asset_key, category, status, "
                "image_path, width, height, color_count, has_alpha, validation_status, "
                "approval_mode, created_at, updated_at) VALUES "
                "('b1', NULL, 'p', 'b1', 'sprite', 'approved', ?, 64, 64, 16, 0, 'pass', "
                "'bypass', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                (str(bypass_img),),
            )
            await conn.commit()

    asyncio.run(_seed())

    out_dir = tmp_path / "out"
    # _ensure_path_allowed 가 임의 경로를 거부할 수 있어 server 의 allowlist 우회
    import server as _server
    _server.ALLOWED_OUTPUT_ROOTS = (tmp_path,)  # type: ignore[attr-defined]
    monkey_target = getattr(_server, "_ensure_path_allowed", None)
    if monkey_target is not None:
        # 이미 monkeypatch 안 했지만 fixture 가 처리 — 여기선 직접 우회
        _server._ensure_path_allowed = lambda p: p  # type: ignore[assignment]

    try:
        with TestClient(server.app) as client:
            r = client.post("/api/export", json={
                "project": "p",
                "category": None,
                "since": None,
                "output_dir": str(out_dir),
                "save_manifest": True,
            })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["exported_count"] == 1            # manual 만
        assert body["excluded_bypassed"] == 1
        # manifest 에도 bypass 빠짐
        manifest = json.loads(Path(body["manifest_path"]).read_text())
        keys = {item["asset_key"] for item in manifest["items"]}
        assert keys == {"m1"}
    finally:
        if monkey_target is not None:
            _server._ensure_path_allowed = monkey_target  # type: ignore[assignment]


# ── /api/comfyui/catalog ───────────────────────────────────────────────────
# PLAN §3.1.2 — ComfyUI /object_info + WorkflowRegistry cross-ref → 통합 카탈로그.


def test_comfyui_catalog_endpoint_returns_payload(isolated, monkeypatch) -> None:  # noqa: ANN001
    """ComfyUI 정상 시 200 + checkpoints/workflows 등 schema 충족."""

    import json as _json
    from pathlib import Path as _Path

    fixture = _json.loads(
        (_Path(__file__).parent / "fixtures" / "comfyui_object_info.json").read_text(encoding="utf-8")
    )

    async def fake_object_info() -> dict:
        return fixture

    monkeypatch.setattr(server.comfyui_client, "object_info", fake_object_info, raising=False)
    # 캐시 영향 제거 — endpoint 가 캐시를 들고 있으면 클리어
    if hasattr(server, "_comfyui_catalog_cache_clear"):
        server._comfyui_catalog_cache_clear()

    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/catalog")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale"] is False
    assert body["fetched_at"]
    # PLAN §3.1.2 schema
    assert isinstance(body["checkpoints"], list)
    assert any(c["name"] == "AnythingXL_xl.safetensors" for c in body["checkpoints"])
    assert isinstance(body["loras"], list)
    assert isinstance(body["vaes"], list)
    assert isinstance(body["controlnets"], list)
    assert isinstance(body["upscalers"], list)
    assert isinstance(body["workflows"], list)
    assert len(body["workflows"]) >= 1
    # checkpoint row schema
    sample = next(c for c in body["checkpoints"] if c["name"] == "AnythingXL_xl.safetensors")
    assert set(sample.keys()) >= {"name", "family", "used_by_workflows"}
    assert sample["family"] == "sdxl"


def test_comfyui_catalog_endpoint_timeout_returns_stale_or_error(isolated, monkeypatch) -> None:  # noqa: ANN001
    """ComfyUI hang → fail-fast (timeout cap), 200 + (stale 또는 ok=false).

    캐시가 없는 첫 호출이면 `{"ok": false, "error": "timeout..."}` 반환.
    """
    import asyncio as _asyncio
    import time as _time

    async def hang() -> dict:
        await _asyncio.sleep(60)
        return {}

    monkeypatch.setattr(server.comfyui_client, "object_info", hang, raising=False)
    monkeypatch.setattr(server, "_COMFYUI_CATALOG_TIMEOUT_SECONDS", 0.1, raising=False)
    if hasattr(server, "_comfyui_catalog_cache_clear"):
        server._comfyui_catalog_cache_clear()

    start = _time.monotonic()
    with TestClient(server.app) as client:
        r = client.get("/api/comfyui/catalog")
    elapsed = _time.monotonic() - start

    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "timeout" in body["error"].lower()
    assert elapsed < 5.0, f"endpoint should fail fast, took {elapsed:.2f}s"


def test_comfyui_catalog_endpoint_uses_cache_on_second_call(isolated, monkeypatch) -> None:  # noqa: ANN001
    """60s 캐시 — 두 번째 호출은 백엔드 안 친다."""

    import json as _json
    from pathlib import Path as _Path

    fixture = _json.loads(
        (_Path(__file__).parent / "fixtures" / "comfyui_object_info.json").read_text(encoding="utf-8")
    )

    call_count = {"n": 0}

    async def fake_object_info() -> dict:
        call_count["n"] += 1
        return fixture

    monkeypatch.setattr(server.comfyui_client, "object_info", fake_object_info, raising=False)
    if hasattr(server, "_comfyui_catalog_cache_clear"):
        server._comfyui_catalog_cache_clear()

    with TestClient(server.app) as client:
        r1 = client.get("/api/comfyui/catalog")
        r2 = client.get("/api/comfyui/catalog")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # 캐시 적중 → 두 번째 호출에서 백엔드 추가 호출 없어야 함
    assert call_count["n"] == 1, f"expected cache hit, got {call_count['n']} backend calls"
