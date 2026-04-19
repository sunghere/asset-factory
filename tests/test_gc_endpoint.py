"""GC 메트릭/수동 실행 엔드포인트 회귀 테스트."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from models import Database


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = Database(tmp_path / "gc.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)
    # 매 테스트마다 GC 상태를 초기화한다.
    monkeypatch.setattr(
        server,
        "_gc_state",
        {
            "last_run_at": None,
            "last_result": None,
            "last_error": None,
            "run_count": 0,
        },
    )
    return data


def test_gc_status_initial_state(isolated: Path) -> None:
    """서버 부팅 직후엔 last_run_at이 None이어야 한다."""
    with TestClient(server.app) as client:
        r = client.get("/api/system/gc/status")
    assert r.status_code == 200
    body = r.json()
    assert body["last_run_at"] is None
    assert body["last_result"] is None
    assert body["last_error"] is None
    assert body["run_count"] == 0


def test_gc_run_updates_state(isolated: Path) -> None:
    """수동 실행 후 status가 갱신되고 deleted_files 키를 노출해야 한다."""
    with TestClient(server.app) as client:
        run_resp = client.post("/api/system/gc/run")
        assert run_resp.status_code == 200
        run_body = run_resp.json()
        assert run_body["status"] == "ok"
        assert "deleted_files" in run_body["result"]
        assert "scanned_files" in run_body["result"]

        status_resp = client.get("/api/system/gc/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["last_run_at"] is not None
    assert body["last_result"] is not None
    assert body["last_error"] is None
    assert body["run_count"] >= 1


def test_gc_run_records_error_and_returns_500(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_gc_candidates``가 raise하면 500과 함께 last_error에 메시지가 남는다."""
    def _boom(_root: Path) -> None:
        raise RuntimeError("simulated GC failure")

    monkeypatch.setattr(server, "run_gc_candidates", _boom)

    with TestClient(server.app) as client:
        run_resp = client.post("/api/system/gc/run")
        assert run_resp.status_code == 500
        assert "simulated GC failure" in run_resp.json()["detail"]

        status_resp = client.get("/api/system/gc/status")
    body = status_resp.json()
    assert body["last_run_at"] is not None
    assert body["last_error"] is not None
    assert "simulated GC failure" in body["last_error"]
    assert body["last_result"] is None
    assert body["run_count"] >= 1


def test_gc_run_requires_api_key_when_set(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API_KEY가 설정되면 ``POST /api/system/gc/run``에 인증이 필요하다."""
    monkeypatch.setattr(server, "api_key", "secret")

    with TestClient(server.app) as client:
        unauth = client.post("/api/system/gc/run")
        assert unauth.status_code == 401

        ok = client.post("/api/system/gc/run", headers={"x-api-key": "secret"})
        assert ok.status_code == 200
