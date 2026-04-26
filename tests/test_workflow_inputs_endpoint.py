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


def test_upload_real_png_with_appended_zip_payload_strips_trailing(
    isolated: _FakeComfyClient,
) -> None:
    """진짜 valid PNG + 뒤에 ZIP/PHP 페이로드 (real polyglot) — 200 통과하지만
    ComfyUI 로 forward 되는 bytes 는 재인코딩되어 trailing payload 가 strip 돼야 한다.

    이 케이스가 ``Image.verify()`` 로는 못 잡혔던 회귀 — IEND chunk 까지만 검증
    하고 그 뒤 데이터를 무시했음. ``load() + save()`` 패턴은 픽셀 디코드 후
    재인코딩하므로 trailing 자동 제거.
    """
    real_png = _png_bytes(size=(16, 16))
    payload = b"PK\x03\x04" + b"<?php system($_GET[0]); ?>" + b"\x00" * 100
    polyglot = real_png + payload

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("evil.png", polyglot, "image/png")},
        )
    # 실제 valid PNG 부분이 디코드 가능하므로 200
    assert r.status_code == 200, r.text
    assert len(isolated.calls) == 1
    forwarded_bytes = isolated.calls[0]["image_bytes_len"]
    # 재인코딩된 bytes 가 ComfyUI 로 가야 — 길이가 polyglot 원본과 다름
    assert forwarded_bytes != len(polyglot)


def test_upload_polyglot_bytes_actually_stripped_of_payload_signature(
    monkeypatch, tmp_path: Path,
) -> None:
    """forward 되는 bytes 자체에 ZIP signature 가 없는지 직접 검증."""
    # 캡처용 — bytes 도 보관
    captured: dict[str, bytes] = {}

    class _CapturingFake(_FakeComfyClient):
        async def upload_input_image(  # type: ignore[override]
            self, image_bytes: bytes, filename: str,
            subfolder: str = "asset-factory", overwrite: bool = True,
        ) -> dict[str, str]:
            captured["bytes"] = image_bytes
            return await super().upload_input_image(
                image_bytes, filename, subfolder, overwrite,
            )

    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    monkeypatch.setattr(server, "comfyui_client", _CapturingFake())

    real_png = _png_bytes(size=(16, 16))
    polyglot = real_png + b"PK\x03\x04EVIL_PAYLOAD_MARKER"
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("evil.png", polyglot, "image/png")},
        )
    assert r.status_code == 200
    assert b"EVIL_PAYLOAD_MARKER" not in captured["bytes"]
    assert b"PK\x03\x04" not in captured["bytes"]


def test_upload_decompression_bomb_returns_400(
    isolated: _FakeComfyClient, monkeypatch,
) -> None:
    """``Image.DecompressionBombError`` (픽셀폭탄) → 400.

    PIL 의 ``MAX_IMAGE_PIXELS`` 임계 초과 입력은 ``DecompressionBombError``
    를 raise. 이 예외는 ``OSError`` / ``ValueError`` 어디에도 안 들어가므로
    명시적으로 except 절에 포함되지 않으면 HTTP 500 으로 새어나간다.
    """
    # 임계값을 작게 강제 — 8x8=64 pixels 도 bomb 처리되도록.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 16)
    bomb = _png_bytes(size=(8, 8))  # 64 pixels > 16 → bomb 판정
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("bomb.png", bomb, "image/png")},
        )
    assert r.status_code == 400
    assert "디코딩 실패" in r.json().get("detail", "")
    assert isolated.calls == []


# ----------------------------------------------------------------------------
# 추가 회귀 — 경계값, 응답 shape, env-var
# ----------------------------------------------------------------------------


def test_upload_exact_boundary_at_max_input_bytes_passes(
    isolated: _FakeComfyClient, monkeypatch,
) -> None:
    """``len(bytes) == MAX_INPUT_BYTES`` (off-by-one boundary) 는 통과.

    가드는 ``> MAX_INPUT_BYTES`` 이므로 정확히 같으면 OK 여야.
    """
    png = _png_bytes(size=(16, 16))
    monkeypatch.setattr(server, "MAX_INPUT_BYTES", len(png))  # exactly 같게
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", png, "image/png")},
        )
    assert r.status_code == 200, r.text


def test_upload_one_byte_over_max_input_bytes_returns_413(
    isolated: _FakeComfyClient, monkeypatch,
) -> None:
    """``len(bytes) == MAX_INPUT_BYTES + 1`` → 413."""
    png = _png_bytes(size=(16, 16))
    monkeypatch.setattr(server, "MAX_INPUT_BYTES", len(png) - 1)
    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", png, "image/png")},
        )
    assert r.status_code == 413
    assert isolated.calls == []


def test_max_input_bytes_default_is_20mb() -> None:
    """env-var 미설정 시 디폴트 20MB. 운영 회귀 가드."""
    assert server.MAX_INPUT_BYTES == 20 * 1024 * 1024


def test_upload_comfy_response_missing_name_returns_502(
    monkeypatch, tmp_path: Path,
) -> None:
    """ComfyUI 가 200 응답인데 ``name`` 누락 → 502 (downstream contract 깨짐).

    원래는 우리가 응답을 그대로 forward 하면 후속 ``load_images.<label>`` 에 박을
    값이 없어 generate 가 의미없는 LoadImage 를 받게 된다. 502 로 빠르게 fail.
    """
    db = Database(tmp_path / "wf.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "api_key", None)
    # ``name`` 키 없는 응답 — 일종의 ComfyUI 측 회귀 시나리오
    bad_fake = _FakeComfyClient(response={"subfolder": "x", "type": "input"})
    monkeypatch.setattr(server, "comfyui_client", bad_fake)

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs",
            files={"file": ("c.png", _png_bytes(), "image/png")},
        )
    assert r.status_code == 502
    assert "예상 shape" in r.json().get("detail", "") or "name" in r.json().get("detail", "")


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


def test_from_asset_polyglot_in_disk_file_is_stripped_before_forward(
    isolated_with_data, tmp_path: Path, monkeypatch,
) -> None:
    """디스크 파일이 polyglot (real PNG + appended ZIP) 이어도 동일 정화 패스 거침.

    ``upsert_scanned_asset`` 가 사용자 임의 디렉토리를 스캔해 등록한 PNG 가
    polyglot 일 가능성 — 자체 생성 이미지 가정이 약하므로 ``from-asset`` 도
    ``_decode_and_reencode_image`` 적용해야 한다 (P1 보안 갭).
    """
    captured: dict[str, bytes] = {}

    class _CapturingFake(_FakeComfyClient):
        async def upload_input_image(  # type: ignore[override]
            self, image_bytes: bytes, filename: str,
            subfolder: str = "asset-factory", overwrite: bool = True,
        ) -> dict[str, str]:
            captured["bytes"] = image_bytes
            return await super().upload_input_image(
                image_bytes, filename, subfolder, overwrite,
            )

    fake = _CapturingFake()
    monkeypatch.setattr(server, "comfyui_client", fake)

    real_png = _png_bytes(size=(16, 16))
    polyglot_path = isolated_with_data["data"] / "polyglot.png"
    polyglot_path.write_bytes(real_png + b"PK\x03\x04EVIL_FROM_ASSET")
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat", asset_key="polyglot",
        image_path=polyglot_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 200, r.text
    assert b"EVIL_FROM_ASSET" not in captured["bytes"]
    assert b"PK\x03\x04" not in captured["bytes"]


def test_from_asset_decompression_bomb_returns_400(
    isolated_with_data, monkeypatch,
) -> None:
    """디스크 파일이 픽셀폭탄이라도 동일 정화 패스 거쳐 400."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 16)
    bomb_path = isolated_with_data["data"] / "bomb.png"
    bomb_path.write_bytes(_png_bytes(size=(8, 8)))
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat", asset_key="bomb",
        image_path=bomb_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 400
    assert isolated_with_data["fake"].calls == []


def test_from_asset_image_path_is_directory_returns_500(
    isolated_with_data,
) -> None:
    """``image_path`` 가 디렉토리로 잘못 등록된 경우 (DB 손상) → 500."""
    dir_path = isolated_with_data["data"] / "isadir"
    dir_path.mkdir()
    asset_id = _seed_asset(
        isolated_with_data["db"],
        project="cat", asset_key="badrow",
        image_path=dir_path,
    )

    with TestClient(server.app) as client:
        r = client.post(
            "/api/workflows/inputs/from-asset",
            json={"asset_id": asset_id},
        )
    assert r.status_code == 500
    assert isolated_with_data["fake"].calls == []
