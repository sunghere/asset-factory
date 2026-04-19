from fastapi.testclient import TestClient

import server


def test_read_only_endpoint_is_public(monkeypatch) -> None:
    monkeypatch.setattr(server, "api_key", "secret")
    with TestClient(server.app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200


def test_mutation_endpoint_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(server, "api_key", "secret")
    with TestClient(server.app) as client:
        response = client.post(
            "/api/generate",
            json={
                "project": "cat-raising",
                "asset_key": "ksh_baby_idle",
                "category": "character",
                "prompt": "pixel art cat idle",
            },
        )
        assert response.status_code == 401


def test_mutation_endpoint_accepts_valid_api_key(monkeypatch) -> None:
    monkeypatch.setattr(server, "api_key", "secret")
    with TestClient(server.app) as client:
        response = client.post(
            "/api/generate",
            headers={"x-api-key": "secret"},
            json={
                "project": "cat-raising",
                "asset_key": "ksh_baby_idle",
                "category": "character",
                "prompt": "pixel art cat idle",
            },
        )
        assert response.status_code == 200
        assert "job_id" in response.json()
