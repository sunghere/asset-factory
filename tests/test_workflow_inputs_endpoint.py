"""``POST /api/workflows/inputs`` 통합 테스트.

multipart 업로드 → content-type/size/PIL 검증 → ComfyUIClient.upload_input_image.
ComfyUIClient 는 capture-all fake 로 대체해 호출 인자만 검증한다 (실 ComfyUI
호출 X).
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from generator import SDError
from models import Database


# ----------------------------------------------------------------------------
# helpers / fixtures
# ----------------------------------------------------------------------------


class _FakeComfyClient:
    """ComfyUIClient.upload_input_image 만 흉내. 호출 인자 캡처."""

    def __init__(
        self,
        response: dict[str, str] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.response = response or {
            "name": "abc_input.png",
            "subfolder": "asset-factory",
            "type": "input",
        }
        self.raise_exc = raise_exc
        self.calls: list[dict[str, object]] = []

    async def upload_input_image(
        self,
        image_bytes: bytes,
        filename: str,
        subfolder: str = "asset-factory",
        overwrite: bool = True,
    ) -> dict[str, str]:
        self.calls.append({
            "image_bytes_len": len(image_bytes),
            "filename": filename,
            "subfolder": subfolder,
            "overwrite": overwrite,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _png_bytes(size: tuple[int, int] = (8, 8), color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 255, 0)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)  # require_api_key 통과
    fake = _FakeComfyClient()
    monkeypatch.setattr(server, "comfyui_client", fake)
    return fake


# ----------------------------------------------------------------------------
# happy path
# ----------------------------------------------------------------------------


def test_upload_png_happy_path_returns_comfy_response(isolated: _FakeComfyClient) -> None:
    png = _png_bytes()
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("char.png", png, "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "name": "abc_input.png",
        "subfolder": "asset-factory",
        "type": "input",
    }

    # ComfyUIClient 호출 인자 검증
    assert len(isolated.calls) == 1
    call = isolated.calls[0]
    assert call["image_bytes_len"] == len(png)
    assert call["subfolder"] == "asset-factory"  # 빈 form → 디폴트
    # filename 은 _safe_input_filename 거쳐 sha 접두 + 안전화
    assert isinstance(call["filename"], str)
    assert call["filename"].endswith("_char.png")


def test_upload_jpeg_accepted(isolated: _FakeComfyClient) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    assert r.status_code == 200, r.text


def test_upload_explicit_subfolder_passes_through(isolated: _FakeComfyClient) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
            data={"subfolder": "user.uploads"},
        )
    assert r.status_code == 200
    assert isolated.calls[-1]["subfolder"] == "user.uploads"


def test_upload_subfolder_traversal_normalized_to_default(isolated: _FakeComfyClient) -> None:
    """``../etc`` 같은 path traversal 시도는 _safe_subfolder 가 ``asset-factory`` 로 정규화."""
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
            data={"subfolder": "../etc"},
        )
    assert r.status_code == 200
    assert isolated.calls[-1]["subfolder"] == "asset-factory"


# ----------------------------------------------------------------------------
# rejection paths
# ----------------------------------------------------------------------------


def test_upload_unsupported_content_type_returns_415(isolated: _FakeComfyClient) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.txt", b"hello world", "text/plain")},
        )
    assert r.status_code == 415
    assert isolated.calls == []  # ComfyUI 호출 안 됨


def test_upload_empty_file_returns_400(isolated: _FakeComfyClient) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", b"", "image/png")},
        )
    assert r.status_code == 400
    assert isolated.calls == []


def test_upload_oversized_returns_413(isolated: _FakeComfyClient, monkeypatch) -> None:
    """20MB 상한 — 테스트에서는 작은 값으로 patch 후 검증."""
    monkeypatch.setattr(server, "MAX_INPUT_BYTES", 100)
    big_png = _png_bytes(size=(64, 64))
    assert len(big_png) > 100  # sanity — patch 가 의미 있는 값
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", big_png, "image/png")},
        )
    assert r.status_code == 413
    assert isolated.calls == []


def test_upload_polyglot_png_header_with_garbage_body_returns_400(
    isolated: _FakeComfyClient,
) -> None:
    """PNG signature + 임의 garbage → PIL verify 실패 → 400.

    content-type whitelist 만으로는 polyglot (PNG 헤더 + ZIP/PHP body) 통과
    가능. 두 번째 방어선이 PIL 디코딩 — 실제 디코딩 시 에러.
    """
    polyglot = b"\x89PNG\r\n\x1a\n" + b"PK\x03\x04" + b"\x00" * 200
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("evil.png", polyglot, "image/png")},
        )
    assert r.status_code == 400
    assert "디코딩" in r.json().get("detail", "")
    assert isolated.calls == []


def test_upload_truncated_png_returns_400(isolated: _FakeComfyClient) -> None:
    """짤린 PNG (헤더만 + 끊김) → PIL verify 실패 → 400."""
    truncated = _png_bytes()[:20]  # 헤더 + 일부 IHDR
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", truncated, "image/png")},
        )
    assert r.status_code == 400
    assert isolated.calls == []


# ----------------------------------------------------------------------------
# downstream errors
# ----------------------------------------------------------------------------


def test_upload_comfyui_unreachable_returns_502(monkeypatch, tmp_path: Path) -> None:
    """ComfyUI 도달 실패 → 502 (외부 의존성 매핑)."""
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    fake = _FakeComfyClient(raise_exc=SDError("conn refused", code="unreachable"))
    monkeypatch.setattr(server, "comfyui_client", fake)

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
        )
    assert r.status_code == 502
    assert "conn refused" in r.json().get("detail", "")


def test_upload_comfyui_timeout_returns_502(monkeypatch, tmp_path: Path) -> None:
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    fake = _FakeComfyClient(raise_exc=SDError("slow", code="timeout"))
    monkeypatch.setattr(server, "comfyui_client", fake)

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
        )
    assert r.status_code == 502


def test_upload_comfyui_server_error_returns_500(monkeypatch, tmp_path: Path) -> None:
    """ComfyUI 5xx → 500 (외부 서버 결함이 우리 서버 통해 사용자에게 전달)."""
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    fake = _FakeComfyClient(raise_exc=SDError("disk full", code="sd_server_error"))
    monkeypatch.setattr(server, "comfyui_client", fake)

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
        )
    assert r.status_code == 500


# ----------------------------------------------------------------------------
# 인증
# ----------------------------------------------------------------------------


def test_upload_missing_api_key_returns_401(monkeypatch, tmp_path: Path) -> None:
    """``api_key`` 가 설정돼 있는데 ``X-API-Key`` 헤더 누락 → 401."""
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", "S3CRET")  # 설정됨
    monkeypatch.setattr(server, "comfyui_client", _FakeComfyClient())

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
        )
    assert r.status_code == 401


# ----------------------------------------------------------------------------
# /api/workflows/inputs/from-asset
# ----------------------------------------------------------------------------


def _seed_asset(db: Database, *, project: str, asset_key: str, image_path: Path) -> str:
    """upsert_scanned_asset 으로 미니 asset 삽입. id 반환."""
    asyncio.run(
        db.upsert_scanned_asset(
            project=project,
            asset_key=asset_key,
            category="character",
            image_path=str(image_path),
            width=8,
            height=8,
            color_count=1,
            has_alpha=False,
            validation_status="pass",
            validation_message="",
        )
    )
    return f"{project}:{asset_key}"


@pytest.fixture()
def isolated_with_data(tmp_path: Path, monkeypatch):  # noqa: ANN001
    """from-asset 테스트용 — DATA_DIR 도 tmp_path 로 옮겨 _allowed_roots 통과."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)

    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    fake = _FakeComfyClient()
    monkeypatch.setattr(server, "comfyui_client", fake)
    return {"db": db, "fake": fake, "data": data}


def test_from_asset_happy_path_uploads_existing_image(isolated_with_data) -> None:
    img_path = isolated_with_data["data"] / "char.png"
    img_path.write_bytes(_png_bytes())
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat",
        asset_key="hero",
        image_path=img_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id, "subfolder": "asset-factory"},
        )
    assert r.status_code == 200, r.text
    fake = isolated_with_data["fake"]
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["subfolder"] == "asset-factory"
    assert call["filename"].endswith("_char.png")


def test_from_asset_unknown_id_returns_404(isolated_with_data) -> None:
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": "does-not-exist"},
        )
    assert r.status_code == 404


def test_from_asset_image_outside_allowed_roots_returns_403(isolated_with_data, tmp_path: Path) -> None:
    """asset.image_path 가 _allowed_roots 외부 → _ensure_path_allowed 가 403.

    악의적이거나 손상된 DB 레코드 방어.
    """
    rogue_dir = tmp_path / "rogue"
    rogue_dir.mkdir()
    rogue_path = rogue_dir / "evil.png"
    rogue_path.write_bytes(_png_bytes())

    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat",
        asset_key="rogue",
        image_path=rogue_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 403


def test_from_asset_missing_file_on_disk_returns_404(isolated_with_data) -> None:
    """DB 에는 있는데 디스크 파일이 사라진 경우 (dangling) → 404."""
    img_path = isolated_with_data["data"] / "ghost.png"
    # 파일 안 만듦 — DB 에만 등록
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat",
        asset_key="ghost",
        image_path=img_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 404


def test_from_asset_default_subfolder_is_asset_factory(isolated_with_data) -> None:
    """body 에 subfolder 누락 → ``""`` 디폴트 → ``_safe_subfolder`` 가 ``asset-factory`` 로 정규화."""
    img_path = isolated_with_data["data"] / "x.png"
    img_path.write_bytes(_png_bytes())
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat",
        asset_key="x",
        image_path=img_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},  # subfolder 생략
        )
    assert r.status_code == 200
    assert isolated_with_data["fake"].calls[-1]["subfolder"] == "asset-factory"


def test_from_asset_comfyui_unreachable_returns_502(isolated_with_data) -> None:
    img_path = isolated_with_data["data"] / "x.png"
    img_path.write_bytes(_png_bytes())
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat",
        asset_key="x",
        image_path=img_path,
    )
    isolated_with_data["fake"].raise_exc = SDError("conn refused", code="unreachable")

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 502
