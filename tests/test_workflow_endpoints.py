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
    # 메타 사이드카 없는 변형은 빈 meta 객체 (forward-compat)
    assert v["meta"]["intent"] == ""
    assert v["meta"]["tags"] == []
    assert v["meta"]["prompt_template"] is None
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
            "approval_mode": "auto",
        })
    assert r.status_code == 422


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
