"""``af workflow ...`` CLI 테스트.

httpx 호출은 ``respx`` 로 mock 한다. 서버는 띄우지 않음 — CLI 가 catalog/upload/
generate 엔드포인트를 올바른 형태로 부르는지, ``--input`` 라벨 검증이 동작하는지,
``AF_API_KEY`` 우선순위가 맞는지를 단위로 검증.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from cli.http import DEFAULT_BASE_URL, get_api_key, get_base_url
from cli.main import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _err_text(result) -> str:  # noqa: ANN001
    """typer/click 버전에 따라 stderr 가 별도/혼합. 둘 다 시도."""
    try:
        return result.stderr or result.output
    except (ValueError, AttributeError):
        return result.output


@pytest.fixture()
def base_url(monkeypatch) -> str:  # noqa: ANN001
    monkeypatch.setenv("AF_BASE_URL", "http://test.local")
    monkeypatch.delenv("AF_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    return "http://test.local"


def _catalog_body() -> dict:
    return {
        "version": 1,
        "categories": {
            "sprite": {
                "description": "테스트",
                "primary_variant": "v",
                "variants": {
                    "v": {
                        "description": "테스트 변형",
                        "available": True,
                        "status": "ready",
                        "primary": True,
                        "outputs": [{"label": "pixel_alpha", "primary": True}],
                        "defaults": {"steps": 30},
                        "input_labels": [
                            {"label": "pose_image", "required": False,
                             "default": "pose.png", "description": ""},
                        ],
                        "file": "sprite/v.json",
                        "has_file": True,
                    }
                },
            }
        },
    }


# ----------------------------------------------------------------------------
# api key 환경변수 우선순위
# ----------------------------------------------------------------------------


def test_api_key_env_var_precedence(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AF_API_KEY", "primary")
    monkeypatch.setenv("API_KEY", "fallback")
    assert get_api_key() == "primary"


def test_api_key_falls_back_to_legacy_env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AF_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "fallback")
    assert get_api_key() == "fallback"


def test_api_key_none_when_neither_set(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AF_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    assert get_api_key() is None


def test_base_url_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AF_BASE_URL", raising=False)
    assert get_base_url() == DEFAULT_BASE_URL


# ----------------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------------


def test_catalog_command_emits_json(runner: CliRunner, base_url: str) -> None:
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        result = runner.invoke(app, ["workflow", "catalog"])
    assert result.exit_code == 0, _err_text(result)
    out = json.loads(result.stdout)
    assert out["categories"]["sprite"]["variants"]["v"]["input_labels"][0]["label"] == "pose_image"


# ----------------------------------------------------------------------------
# upload
# ----------------------------------------------------------------------------


def test_upload_local_file_outputs_name(
    runner: CliRunner, base_url: str, tmp_path: Path
) -> None:
    img = tmp_path / "pose.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)

    with respx.mock(base_url=base_url) as mock:
        route = mock.post("/api/workflows/inputs").mock(
            return_value=httpx.Response(200, json={"name": "asset-factory_abc_pose.png"})
        )
        result = runner.invoke(app, ["workflow", "upload", str(img)])
    assert result.exit_code == 0, _err_text(result)
    assert result.stdout.strip() == "asset-factory_abc_pose.png"
    assert route.called
    # multipart 본문에 파일이 들어갔는지 헤더 검사
    sent = route.calls.last.request
    assert sent.headers["content-type"].startswith("multipart/form-data")


def test_upload_from_asset(runner: CliRunner, base_url: str) -> None:
    asset_id = "11111111-2222-3333-4444-555555555555"
    with respx.mock(base_url=base_url) as mock:
        route = mock.post("/api/workflows/inputs/from-asset").mock(
            return_value=httpx.Response(200, json={"name": "asset-factory_xyz.png"})
        )
        result = runner.invoke(
            app, ["workflow", "upload", "--from-asset", asset_id]
        )
    assert result.exit_code == 0, _err_text(result)
    assert result.stdout.strip() == "asset-factory_xyz.png"
    body = json.loads(route.calls.last.request.content)
    assert body == {"asset_id": asset_id}


def test_upload_requires_path_or_from_asset(runner: CliRunner, base_url: str) -> None:
    result = runner.invoke(app, ["workflow", "upload"])
    assert result.exit_code == 2
    assert "path 또는 --from-asset" in _err_text(result)


def test_upload_rejects_both_path_and_from_asset(
    runner: CliRunner, base_url: str, tmp_path: Path
) -> None:
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = runner.invoke(
        app, ["workflow", "upload", str(img), "--from-asset", "abc"]
    )
    assert result.exit_code == 2


def test_upload_passes_api_key_header(
    runner: CliRunner, base_url: str, tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setenv("AF_API_KEY", "secret-key")
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    with respx.mock(base_url=base_url) as mock:
        route = mock.post("/api/workflows/inputs").mock(
            return_value=httpx.Response(200, json={"name": "p.png"})
        )
        runner.invoke(app, ["workflow", "upload", str(img)])
    assert route.calls.last.request.headers.get("x-api-key") == "secret-key"


# ----------------------------------------------------------------------------
# gen
# ----------------------------------------------------------------------------


def test_gen_input_local_file_uploads_first(
    runner: CliRunner, base_url: str, tmp_path: Path
) -> None:
    img = tmp_path / "pose.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        upload_route = mock.post("/api/workflows/inputs").mock(
            return_value=httpx.Response(200, json={"name": "uploaded_pose.png"})
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={
                "job_id": "job-1",
                "workflow_category": "sprite",
                "workflow_variant": "v",
                "candidates_total": 1,
                "primary_output": "pixel_alpha",
                "approval_mode": "manual",
            })
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "proj", "key",
                "test prompt",
                "--input", f"pose_image=@{img}",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    assert upload_route.called, "로컬 파일 입력은 generate 전에 upload 되어야 함"
    body = json.loads(gen_route.calls.last.request.content)
    assert body["workflow_params"]["load_images"]["pose_image"] == "uploaded_pose.png"
    assert body["approval_mode"] == "manual"


def test_gen_input_unknown_label_errors_before_request(
    runner: CliRunner, base_url: str, tmp_path: Path
) -> None:
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "should-not-be-called"})
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "proj", "key", "p",
                "--input", f"unknown_label=@{img}",
            ],
        )
    assert result.exit_code == 1
    assert "unknown_label" in _err_text(result)
    assert "pose_image" in _err_text(result)  # 가능 라벨 알려줌
    assert not gen_route.called


def test_gen_bypass_approval_sets_request_field(
    runner: CliRunner, base_url: str
) -> None:
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-bp"})
        )
        result = runner.invoke(
            app,
            ["workflow", "gen", "sprite/v", "tmp", "k", "p", "--bypass-approval"],
        )
    assert result.exit_code == 0, _err_text(result)
    body = json.loads(gen_route.calls.last.request.content)
    assert body["approval_mode"] == "bypass"


# ----------------------------------------------------------------------------
# §1.B — --subject / --prompt-mode / --style-extra
# ----------------------------------------------------------------------------


def test_gen_subject_mode_flags_propagated(runner: CliRunner, base_url: str) -> None:
    """``--subject`` + ``--style-extra`` 가 generate body 에 그대로 전달."""
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={
                "job_id": "job-subj",
                "prompt_resolution": {
                    "mode": "subject",
                    "user_slot": "subject",
                    "user_input": "1girl, silver hair",
                    "final_positive": "pixel art, ..., 1girl, silver hair, cinematic",
                    "final_negative": "blurry, ...",
                },
            })
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "tmp", "k", "",
                "--subject", "1girl, silver hair",
                "--style-extra", "cinematic",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    body = json.loads(gen_route.calls.last.request.content)
    assert body["subject"] == "1girl, silver hair"
    assert body["style_extra"] == "cinematic"
    assert body["prompt_mode"] == "auto"     # default


def test_gen_prompt_mode_legacy_flag(runner: CliRunner, base_url: str) -> None:
    """``--prompt-mode legacy`` 가 body 에 들어감."""
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-leg"})
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "tmp", "k",
                "user-written prompt with all keywords",
                "--prompt-mode", "legacy",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    body = json.loads(gen_route.calls.last.request.content)
    assert body["prompt_mode"] == "legacy"
    assert body["prompt"] == "user-written prompt with all keywords"


def test_gen_requires_prompt_or_subject(runner: CliRunner, base_url: str) -> None:
    """prompt 와 --subject 모두 빈 입력이면 exit 2."""
    result = runner.invoke(
        app,
        ["workflow", "gen", "sprite/v", "tmp", "k"],
    )
    assert result.exit_code == 2
    assert "prompt 또는 --subject" in _err_text(result)


def test_gen_plain_string_input_passes_through(
    runner: CliRunner, base_url: str
) -> None:
    """ComfyUI 가 이미 알고 있는 파일명은 upload 없이 그대로 박힘."""
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        upload_route = mock.post("/api/workflows/inputs").mock(
            return_value=httpx.Response(500, json={"detail": "should not be called"})
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-2"})
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "p", "k", "x",
                "--input", "pose_image=already_in_comfyui.png",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    assert not upload_route.called, "plain string 입력은 upload 가 호출되면 안 됨"
    body = json.loads(gen_route.calls.last.request.content)
    assert body["workflow_params"]["load_images"]["pose_image"] == "already_in_comfyui.png"


def test_gen_invalid_input_format_rejected(runner: CliRunner, base_url: str) -> None:
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        result = runner.invoke(
            app,
            ["workflow", "gen", "sprite/v", "p", "k", "x", "--input", "no_equals"],
        )
    assert result.exit_code == 2
    assert "LABEL=VALUE" in _err_text(result)


def test_gen_target_must_be_category_slash_variant(
    runner: CliRunner, base_url: str
) -> None:
    result = runner.invoke(app, ["workflow", "gen", "no_slash", "p", "k", "x"])
    assert result.exit_code == 2
    assert "category/variant" in _err_text(result)


# ----------------------------------------------------------------------------
# 친절 에러 메시지
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# 32자 hex 파일명은 asset 이 아니라 plain 입력으로 처리 (리뷰 fix M1)
# ----------------------------------------------------------------------------


def test_gen_input_long_hex_filename_is_plain_string(
    runner: CliRunner, base_url: str
) -> None:
    """sha256-style 64-char hex 파일명을 ComfyUI input 이름으로 plain 사용.

    이전에는 32자 이상 hex 가 _looks_like_asset_id true → from-asset 호출 →
    404 로 깨졌다. UUID-only 로 좁힌 fix 가 회귀하지 않게 방어.
    """
    plain_name = "a" * 64  # extension-less hex blob (해시 네이밍)
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        from_asset = mock.post("/api/workflows/inputs/from-asset").mock(
            return_value=httpx.Response(404, json={"detail": "should not be called"})
        )
        gen_route = mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-3"})
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "p", "k", "x",
                "--input", f"pose_image={plain_name}",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    assert not from_asset.called, "64자 hex 파일명은 from-asset 호출되면 안 됨"
    body = json.loads(gen_route.calls.last.request.content)
    assert body["workflow_params"]["load_images"]["pose_image"] == plain_name


def test_gen_input_explicit_asset_prefix_for_non_uuid(
    runner: CliRunner, base_url: str
) -> None:
    """비-UUID asset id 는 'asset:<id>' prefix 로 명시적 라우팅."""
    short_id = "asset123"
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        from_asset = mock.post("/api/workflows/inputs/from-asset").mock(
            return_value=httpx.Response(200, json={"name": "uploaded.png"})
        )
        mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-4"})
        )
        result = runner.invoke(
            app,
            [
                "workflow", "gen", "sprite/v", "p", "k", "x",
                "--input", f"pose_image=asset:{short_id}",
            ],
        )
    assert result.exit_code == 0, _err_text(result)
    body = json.loads(from_asset.calls.last.request.content)
    assert body["asset_id"] == short_id


# ----------------------------------------------------------------------------
# --wait 폴링 (리뷰 fix C1)
# ----------------------------------------------------------------------------


def test_wait_recognizes_completed_status(runner: CliRunner, base_url: str) -> None:
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-w1"})
        )
        # 첫 폴링은 running, 둘째는 completed
        mock.get("/api/jobs/job-w1").mock(
            side_effect=[
                httpx.Response(200, json={"status": "running"}),
                httpx.Response(200, json={"status": "completed", "completed_count": 1}),
            ]
        )
        # interval 짧게 (테스트 빠르게)
        import cli.commands.workflow as wf_mod
        result = runner.invoke(
            app,
            ["workflow", "gen", "sprite/v", "p", "k", "x", "--wait",
             "--wait-timeout", "5"],
            obj=None,
        )
    # interval 의 sleep 때문에 살짝 지연 — exit 코드만 검증
    assert result.exit_code == 0, _err_text(result)


def test_wait_recognizes_completed_with_errors_status(
    runner: CliRunner, base_url: str
) -> None:
    """job 이 일부 task 실패해 'completed_with_errors' 로 끝나도 CLI 는 정상 종료."""
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(200, json=_catalog_body())
        )
        mock.post("/api/workflows/generate").mock(
            return_value=httpx.Response(200, json={"job_id": "job-w2"})
        )
        mock.get("/api/jobs/job-w2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "completed_with_errors",
                    "completed_count": 1,
                    "failed_count": 1,
                },
            )
        )
        result = runner.invoke(
            app,
            ["workflow", "gen", "sprite/v", "p", "k", "x", "--wait",
             "--wait-timeout", "5"],
        )
    assert result.exit_code == 0, _err_text(result)
    out = json.loads(result.stdout)
    assert out["status"] == "completed_with_errors"


# ----------------------------------------------------------------------------
# 422 detail 친절 렌더링 (리뷰 NIT)
# ----------------------------------------------------------------------------


def test_422_detail_list_renders_first_msg(runner: CliRunner, base_url: str) -> None:
    """FastAPI validation 에러는 list-of-dict — 첫 msg 만 깔끔하게 보이게."""
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(
                422,
                json={"detail": [
                    {"loc": ["body", "x"], "msg": "field required", "type": "missing"},
                    {"loc": ["body", "y"], "msg": "value error", "type": "value"},
                ]},
            )
        )
        result = runner.invoke(app, ["workflow", "catalog"])
    assert result.exit_code == 1
    err = _err_text(result)
    assert "field required" in err
    assert "+1 more" in err  # 추가 항목 수 노출


def test_http_error_surfaces_detail(runner: CliRunner, base_url: str) -> None:
    with respx.mock(base_url=base_url) as mock:
        mock.get("/api/workflows/catalog").mock(
            return_value=httpx.Response(503, json={"detail": "ComfyUI 다운"})
        )
        result = runner.invoke(app, ["workflow", "catalog"])
    assert result.exit_code == 1
    assert "ComfyUI 다운" in _err_text(result)
    assert "503" in _err_text(result)
