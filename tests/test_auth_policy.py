from fastapi.testclient import TestClient

import server


def test_read_only_endpoint_is_public(monkeypatch) -> None:
    monkeypatch.setattr(server, "api_key", "secret")
    with TestClient(server.app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200


_BATCH_BODY = {
    "project": "cat-raising",
    "asset_key": "ksh_baby_idle",
    "category": "character",
    "workflow_category": "sprite",
    "workflow_variants": ["pixel_alpha"],
    "prompts": ["pixel art cat idle pose"],
    "prompt_mode": "legacy",
    "seeds": [1],
}


def test_mutation_endpoint_requires_api_key(monkeypatch) -> None:
    """write 엔드포인트(/api/batches) 가 API key 미동봉 시 401."""
    monkeypatch.setattr(server, "api_key", "secret")
    with TestClient(server.app) as client:
        response = client.post("/api/batches", json=_BATCH_BODY)
        assert response.status_code == 401


def test_mutation_endpoint_accepts_valid_api_key(monkeypatch, tmp_path) -> None:
    """write 엔드포인트가 유효 API key + 워크플로우 spec 으로 200."""
    from models import Database

    monkeypatch.setattr(server, "api_key", "secret")
    db = server.db.__class__(tmp_path / "auth.db")
    import asyncio
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)

    class _Broker:
        async def publish(self, _e):  # noqa: ANN001
            return None
    monkeypatch.setattr(server, "event_broker", _Broker())

    with TestClient(server.app) as client:
        response = client.post(
            "/api/batches",
            headers={"x-api-key": "secret"},
            json=_BATCH_BODY,
        )
        assert response.status_code == 200, response.text
        assert "batch_id" in response.json()


def test_legacy_generate_endpoint_returns_410(monkeypatch) -> None:
    """/api/generate 는 A1111 죽음 → 410 + Sunset/Link 헤더."""
    monkeypatch.setattr(server, "api_key", None)
    with TestClient(server.app) as client:
        r = client.post(
            "/api/generate",
            json={
                "project": "pp",
                "asset_key": "k",
                "category": "character",
                "prompt": "x",
            },
        )
    assert r.status_code == 410
    assert r.headers.get("Deprecation") == "true"
    assert r.headers.get("Sunset")
    assert "/api/workflows/generate" in r.headers.get("Link", "")
    body = r.json()["detail"]
    assert body["successor"] == "/api/workflows/generate"


def test_legacy_generate_batch_endpoint_returns_410(monkeypatch) -> None:
    """/api/generate/batch 도 410 + successor=/api/batches."""
    monkeypatch.setattr(server, "api_key", None)
    with TestClient(server.app) as client:
        r = client.post("/api/generate/batch", json={"project": "pp", "spec": {}})
    assert r.status_code == 410
    assert "/api/batches" in r.headers.get("Link", "")
    body = r.json()["detail"]
    assert body["successor"] == "/api/batches"
