from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import server


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _DummyAsyncClient:
    def __init__(self, responses: list[_DummyResponse], calls: list[tuple[str, str, dict | None]], **_kwargs):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, headers: dict):
        self._calls.append(("GET", url, None))
        return self._responses.pop(0)

    async def post(self, url: str, *, headers: dict, json: dict):
        self._calls.append(("POST", url, json))
        return self._responses.pop(0)


@pytest.fixture()
def reset_alert_state(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(server, "_alert_state", {"last_alert_at": None, "last_alert_issue_id": None})


def test_create_or_update_incident_issue_skips_without_env(monkeypatch, caplog, reset_alert_state) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "_PC_API_URL", None)
    monkeypatch.setattr(server, "_PC_API_KEY", None)
    monkeypatch.setattr(server, "_PC_COMPANY_ID", None)

    asyncio.run(server._create_or_update_incident_issue("t", "b"))

    assert "Paperclip alert env vars not set" in caplog.text
    assert server._alert_state["last_alert_issue_id"] is None


def test_create_or_update_incident_issue_creates_new_issue_and_updates_state(monkeypatch, reset_alert_state) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "_PC_API_URL", "https://paperclip.local")
    monkeypatch.setattr(server, "_PC_API_KEY", "token")
    monkeypatch.setattr(server, "_PC_COMPANY_ID", "company-1")
    monkeypatch.setattr(server, "_PC_ASSIGNEE_AGENT_ID", "agent-1")

    calls: list[tuple[str, str, dict | None]] = []
    responses = [_DummyResponse(201, {"id": "issue-1"})]

    class _FakeHttpx:
        @staticmethod
        def AsyncClient(**kwargs):
            return _DummyAsyncClient(responses, calls, **kwargs)

    monkeypatch.setattr(server, "httpx", _FakeHttpx)

    asyncio.run(server._create_or_update_incident_issue("title", "body"))

    assert len(calls) == 1
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/companies/company-1/issues")
    assert server._alert_state["last_alert_issue_id"] == "issue-1"
    assert isinstance(server._alert_state["last_alert_at"], str)


def test_create_or_update_incident_issue_within_24h_adds_comment_only(monkeypatch, reset_alert_state) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "_PC_API_URL", "https://paperclip.local")
    monkeypatch.setattr(server, "_PC_API_KEY", "token")
    monkeypatch.setattr(server, "_PC_COMPANY_ID", "company-1")

    server._alert_state["last_alert_issue_id"] = "issue-1"
    server._alert_state["last_alert_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    calls: list[tuple[str, str, dict | None]] = []
    responses = [
        _DummyResponse(200, {"id": "issue-1", "status": "in_progress"}),
        _DummyResponse(201, {"id": "comment-1"}),
    ]

    class _FakeHttpx:
        @staticmethod
        def AsyncClient(**kwargs):
            return _DummyAsyncClient(responses, calls, **kwargs)

    monkeypatch.setattr(server, "httpx", _FakeHttpx)

    asyncio.run(server._create_or_update_incident_issue("title", "body"))

    assert [c[0] for c in calls] == ["GET", "POST"]
    assert calls[0][1].endswith("/api/issues/issue-1")
    assert calls[1][1].endswith("/api/issues/issue-1/comments")


def test_monitoring_tick_fires_stuck_and_queue_alerts(monkeypatch) -> None:  # noqa: ANN001
    class _FakeDB:
        async def find_and_requeue_stuck_tasks(self, _timeout: int):
            return [{"id": 10, "asset_key": "hero", "retries": 3, "max_retries": 3, "started_at": "t"}]

        async def system_stats(self):
            return {"queued_total": 999}

    fired: list[tuple[str, dict | None, int]] = []

    async def _fake_fire_alert(reason: str, task: dict | None = None, *, queue_depth: int = 0) -> None:
        fired.append((reason, task, queue_depth))

    async def _always_down() -> bool:
        return False

    monkeypatch.setattr(server, "db", _FakeDB())
    monkeypatch.setattr(server, "_QUEUE_DEPTH_ALERT_THRESHOLD", 10)
    monkeypatch.setattr(server, "_fire_alert", _fake_fire_alert)
    monkeypatch.setattr(server, "_check_comfyui_ok", _always_down)

    asyncio.run(server._monitoring_tick())

    assert fired[0][0] == "stuck"
    assert fired[0][1]["id"] == 10
    assert fired[1][0] == "queue_depth+comfyui_down"
    assert fired[1][2] == 999
