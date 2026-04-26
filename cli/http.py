"""asset-factory CLI 의 공유 HTTP 클라이언트.

설계:
- 환경변수 우선순위: ``AF_API_KEY`` > ``API_KEY`` (서버 측 호환).
- ``AF_BASE_URL`` (default ``http://localhost:8000``).
- 4xx/5xx → stderr 친절 메시지 + ``typer.Exit(code=1)`` (라이브러리 트레이스
  대신 깔끔한 셸 종료).
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx
import typer

DEFAULT_BASE_URL = "http://localhost:8000"


def get_api_key() -> str | None:
    """``AF_API_KEY`` 우선, 없으면 ``API_KEY`` 폴백. 둘 다 없으면 None."""
    return os.getenv("AF_API_KEY") or os.getenv("API_KEY")


def get_base_url() -> str:
    return os.getenv("AF_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = get_api_key()
    return {"x-api-key": key} if key else {}


def _format_error(resp: httpx.Response) -> str:
    """HTTP 에러 응답을 한 줄 메시지로.

    FastAPI 422 의 detail 은 ``[{"loc": [...], "msg": "...", "type": "..."}]``
    꼴이라 그대로 출력하면 길고 읽기 힘들다. list 면 첫 항목의 ``msg`` (있으면)
    + 항목 수만 노출.
    """
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"

    detail: Any = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict) and "msg" in first:
            extra = f" (+{len(detail) - 1} more)" if len(detail) > 1 else ""
            return f"HTTP {resp.status_code}: {first['msg']}{extra}"
    return f"HTTP {resp.status_code}: {detail}"


def request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    files: Any = None,
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """JSON 응답을 dict 로 반환. 4xx/5xx 시 친절 메시지 출력 후 exit 1.

    파일 업로드는 ``files`` 사용 (httpx multipart). 그 외 JSON 본문은 ``json``.
    """
    url = f"{get_base_url()}{path}"
    headers = _auth_headers()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(
                method,
                url,
                json=json,
                params=params,
                files=files,
                data=data,
                headers=headers,
            )
    except httpx.RequestError as exc:
        typer.echo(f"asset-factory 서버 연결 실패 ({get_base_url()}): {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if resp.status_code >= 400:
        typer.echo(_format_error(resp), err=True)
        raise typer.Exit(code=1)

    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        # 바이너리/text 응답 — caller 가 raw 가 필요하면 별도 헬퍼 써야 함.
        typer.echo(
            f"asset-factory 응답이 JSON 아님 (Content-Type={resp.headers.get('content-type')})",
            err=True,
        )
        raise typer.Exit(code=1)


def get_bytes(path: str, *, params: dict[str, Any] | None = None, timeout: float = 30.0) -> bytes:
    """이미지 등 binary 응답용. JSON 검증 안 함."""
    url = f"{get_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=_auth_headers(), params=params)
    except httpx.RequestError as exc:
        typer.echo(f"asset-factory 서버 연결 실패: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if resp.status_code >= 400:
        typer.echo(_format_error(resp), err=True)
        raise typer.Exit(code=1)
    return resp.content


__all__ = ["request", "get_bytes", "get_api_key", "get_base_url", "DEFAULT_BASE_URL"]
