"""CodeQL 대응 — /cherry-pick 오래된 북마크 리다이렉트 안전성.

``cherry_pick_redirect`` 는 외부에서 주어지는 ``batch_id`` 를 URL 경로에 꽂기
때문에 과거 CodeQL 에서 open-redirect 로 잡혔다. fix 는 ``^[A-Za-z0-9_-]{1,64}$``
정규식에 맞는 값만 받아 ``/app/cherry-pick/<id>`` 로 보내고, 그 외에는 전부
``/app/queue`` 로 떨어뜨리는 것. 이 테스트는 그 계약을 핀으로 고정한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import server


def _client() -> TestClient:
    # API key / DB 격리는 필요 없다 (redirect 는 read-only, 전역 의존성 없음).
    return TestClient(server.app)


def test_redirect_valid_batch_id_goes_to_app_cherry_pick() -> None:
    with _client() as client:
        r = client.get("/cherry-pick?batch_id=btc_ABC-123", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/cherry-pick/btc_ABC-123"


def test_redirect_missing_batch_id_goes_to_queue() -> None:
    with _client() as client:
        r = client.get("/cherry-pick", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/queue"


def test_redirect_empty_batch_id_goes_to_queue() -> None:
    with _client() as client:
        r = client.get("/cherry-pick?batch_id=", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app/queue"


def test_redirect_rejects_external_url_injection() -> None:
    """``//evil.example`` · ``https://evil.example`` 같은 외부 URL 시도."""
    payloads = [
        "//evil.example/pwn",
        "https://evil.example",
        "http://evil.example/redir",
        "/app/../../etc/passwd",
        "btc_ok/../../drop",
    ]
    with _client() as client:
        for payload in payloads:
            r = client.get("/cherry-pick", params={"batch_id": payload}, follow_redirects=False)
            assert r.status_code == 302, payload
            assert r.headers["location"] == "/app/queue", payload


def test_redirect_rejects_crlf_injection() -> None:
    """헤더 인젝션 고전 — ``\\r\\n`` 섞인 값은 location 에 흘러가면 안 된다."""
    with _client() as client:
        r = client.get(
            "/cherry-pick",
            params={"batch_id": "btc\r\nSet-Cookie: evil=1"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers["location"] == "/app/queue"


def test_redirect_rejects_too_long_batch_id() -> None:
    with _client() as client:
        r = client.get(
            "/cherry-pick",
            params={"batch_id": "a" * 65},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers["location"] == "/app/queue"


def test_redirect_accepts_boundary_length_batch_id() -> None:
    safe = "A" * 64
    with _client() as client:
        r = client.get("/cherry-pick", params={"batch_id": safe}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/app/cherry-pick/{safe}"
