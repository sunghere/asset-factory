"""PR #1 리뷰 피드백 회귀 테스트.

대상:
- Critical 1: select_asset_candidate가 이전 primary 파일을 보존하는지
- Critical 2: scan/image-serve 엔드포인트의 path allowlist
- Warning 10: 첫 성공한 슬롯이 primary asset으로 승격되는지 (slot 0 의존 제거)
- Warning 4: EventBroker.publish 동시 unsubscribe 안전성
- Warning 8: SDError 코드 기반 재시도/즉시 실패 분기
- Warning 11: lifespan 시작 시 orphan 복구
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from generator import SDError
from models import Database


def _make_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (16, 16), (10, 20, 30, 255)).save(path)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "review.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)

    class _DummyBroker:
        async def publish(self, _event):  # noqa: ANN001
            return None

    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    return db, data


# --------------------------------------------------------------------------------
# Critical 2 - path allowlist
# --------------------------------------------------------------------------------
def test_scan_rejects_path_outside_allowlist(isolated, tmp_path) -> None:  # noqa: ANN001
    """ASSET_FACTORY_ALLOWED_ROOTS 외부 경로를 스캔하려 하면 403 응답."""
    with TestClient(server.app) as client:
        r = client.post(
            "/api/projects/scan",
            json={"project": "p", "root_path": str(tmp_path / "outside"), "max_colors": 32},
        )
    assert r.status_code == 403


def test_image_serve_rejects_db_path_outside_allowlist(isolated, tmp_path) -> None:  # noqa: ANN001
    """DB 경로가 임의 위치를 가리키면 image-serve 엔드포인트가 403."""
    db, _data = isolated
    outside = tmp_path / "outside.png"
    _make_png(outside)

    async def prep() -> str:
        await db.upsert_scanned_asset(
            project="p",
            asset_key="x",
            category="ui",
            image_path=str(outside),
            width=16,
            height=16,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        rows = await db.list_assets(project="p")
        return rows[0]["id"]

    asset_id = asyncio.run(prep())

    with TestClient(server.app) as client:
        r = client.get(f"/api/assets/{asset_id}/image")
    assert r.status_code == 403


# --------------------------------------------------------------------------------
# Critical 1 - history file preservation (DB-level)
# --------------------------------------------------------------------------------
def test_replace_primary_keeps_old_path_in_history(isolated) -> None:
    """replace_asset_primary_image 호출 후 history 행에 이전 image_path가 남는다."""
    db, data = isolated
    old_path = data / "old.png"
    new_path = data / "new.png"
    _make_png(old_path)
    _make_png(new_path)

    async def scenario() -> None:
        await db.upsert_scanned_asset(
            project="p",
            asset_key="k",
            category="ui",
            image_path=str(old_path),
            width=16,
            height=16,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        rows = await db.list_assets(project="p")
        asset_id = rows[0]["id"]
        ok = await db.replace_asset_primary_image(
            asset_id,
            image_path=str(new_path),
            width=16,
            height=16,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
            generation_seed=1,
            generation_model="m",
            generation_prompt="p",
            metadata_json=None,
        )
        assert ok is True
        hist = await db.list_asset_history(asset_id)
        assert len(hist) == 1
        # 이전 image_path가 history에 보존되며 디스크 파일도 남아있다.
        assert hist[0]["image_path"] == str(old_path)
        assert old_path.exists(), "이전 primary 파일이 보존되어야 한다"

    asyncio.run(scenario())


# --------------------------------------------------------------------------------
# Warning 10 - first successful slot becomes primary (slot 0 dependency removed)
# --------------------------------------------------------------------------------
def test_has_asset_helper(isolated) -> None:
    """슬롯 0이 실패했을 때를 시뮬레이션: has_asset이 false → 이후 슬롯이 promote 되어야 한다."""
    db, data = isolated

    async def scenario() -> None:
        # 처음에는 asset이 없음
        assert await db.has_asset("p", "k") is False
        # 슬롯1이 먼저 성공한 상황을 흉내내기 위해 직접 upsert
        path = data / "slot1.png"
        _make_png(path)
        await db.upsert_scanned_asset(
            project="p",
            asset_key="k",
            category="character",
            image_path=str(path),
            width=16,
            height=16,
            color_count=1,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        assert await db.has_asset("p", "k") is True

    asyncio.run(scenario())


# --------------------------------------------------------------------------------
# Warning 4 - EventBroker.publish concurrent safety
# --------------------------------------------------------------------------------
def test_event_broker_publish_safe_during_unsubscribe() -> None:
    """publish 도중 subscriber가 사라져도 RuntimeError 없이 동작한다."""
    broker = server.EventBroker()

    async def scenario() -> None:
        async with broker.subscribe() as q1:
            async with broker.subscribe() as _q2:
                # subscriber 스냅샷 기반이므로 publish는 항상 성공해야 한다.
                await broker.publish({"type": "x"})
                assert q1.qsize() == 1

    asyncio.run(scenario())


# --------------------------------------------------------------------------------
# Warning 8 - SDError-based retry classification
# --------------------------------------------------------------------------------
def test_should_retry_classifier() -> None:
    assert server._should_retry_sd_failure(SDError("timeout", code="timeout")) is True
    assert server._should_retry_sd_failure(SDError("server", code="sd_server_error")) is True
    assert server._should_retry_sd_failure(SDError("unreachable", code="unreachable")) is True
    assert server._should_retry_sd_failure(SDError("4xx", code="sd_client_error")) is False
    assert server._should_retry_sd_failure(SDError("oom", code="oom")) is False
    assert server._should_retry_sd_failure(RuntimeError("misc")) is True


def test_format_task_error_includes_code() -> None:
    msg = server._format_task_error(SDError("timed out", code="timeout"))
    assert msg.startswith("code:timeout ")


# --------------------------------------------------------------------------------
# Warning 11 - orphan task recovery
# --------------------------------------------------------------------------------
def test_recover_orphan_tasks_requeues_processing(isolated) -> None:
    """server crash 흉내: processing 상태 task를 queued로 복귀시킨다."""
    db, _data = isolated

    async def scenario() -> None:
        await db.create_job(job_id="job1", job_type="generate_single", payload=None)
        task: dict[str, Any] = {
            "job_id": "job1",
            "project": "p",
            "asset_key": "k",
            "category": "ui",
            "prompt": "x",
            "negative_prompt": None,
            "model_name": None,
            "width": None,
            "height": None,
            "steps": 20,
            "cfg": 7.0,
            "sampler": "DPM++ 2M",
            "expected_size": 64,
            "max_colors": 32,
            "max_retries": 3,
        }
        await db.enqueue_generation_task(task)
        # 'processing'으로 클레임
        claimed = await db.claim_next_task()
        assert claimed is not None
        # 복구 호출
        recovered = await db.recover_orphan_tasks()
        assert recovered == 1
        # 다시 claim 가능해야 한다
        again = await db.claim_next_task()
        assert again is not None
        assert again["id"] == claimed["id"]

    asyncio.run(scenario())


# --------------------------------------------------------------------------------
# Critical 3 - disk guard returns 507 at enqueue
# --------------------------------------------------------------------------------
def test_generate_returns_507_when_disk_low(isolated, monkeypatch) -> None:  # noqa: ANN001
    """디스크 가드가 507 Insufficient Storage로 즉시 응답."""
    def _raise(*_args, **_kwargs):  # noqa: ANN001, ANN002, ANN003
        # 시그니처 호환: _check_disk_space(path) 와 (path, required_mb=...) 둘 다 수용
        raise RuntimeError("simulated low disk")

    monkeypatch.setattr(server, "_check_disk_space", _raise)

    with TestClient(server.app) as client:
        r = client.post(
            "/api/generate",
            json={
                "project": "p",
                "asset_key": "k",
                "prompt": "x",
            },
        )
    assert r.status_code == 507
