"""``af workflow ...`` 서브커맨드.

명령:
- ``af workflow catalog``
- ``af workflow describe <category>/<variant>``
- ``af workflow upload <path>``
- ``af workflow upload --from-asset <asset_id>``
- ``af workflow gen <category>/<variant> <project> <key> "<prompt>" [...]``

``af workflow gen --input <label>=<value>`` 의 값 파싱 규칙:
- ``@<path>`` — 로컬 파일. 내부적으로 upload 먼저 → 받은 name 을 라벨에 박음.
- ``asset:<id>`` 또는 32자+ hex — from-asset upload.
- 그 외 plain string — 이미 ComfyUI 가 알고 있는 입력 이름이라 가정, 그대로 사용.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import typer

from cli.http import request

workflow_app = typer.Typer(no_args_is_help=True)


# ----------------------------------------------------------------------------
# catalog / describe
# ----------------------------------------------------------------------------


@workflow_app.command("catalog")
def cmd_catalog() -> None:
    """전체 카탈로그 (categories → variants + input_labels) 를 JSON 으로 출력."""
    body = request("GET", "/api/workflows/catalog")
    typer.echo(json.dumps(body, ensure_ascii=False, indent=2))


@workflow_app.command("describe")
def cmd_describe(target: str = typer.Argument(..., help="<category>/<variant>")) -> None:
    """단일 변형 상세 (input_labels, defaults, outputs) 를 JSON 으로 출력."""
    category, variant = _parse_category_variant(target)
    body = request("GET", "/api/workflows/catalog")
    try:
        spec = body["categories"][category]["variants"][variant]
    except KeyError as exc:
        typer.echo(
            f"variant {category}/{variant} 가 카탈로그에 없습니다.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"category": category, "variant": variant, **spec}, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------------
# upload
# ----------------------------------------------------------------------------


@workflow_app.command("upload")
def cmd_upload(
    path: Path | None = typer.Argument(
        None, help="업로드할 로컬 파일 경로. --from-asset 와 상호 배타.",
    ),
    from_asset: str | None = typer.Option(
        None, "--from-asset", help="기존 asset id 로 업로드 (체인용).",
    ),
    subfolder: str | None = typer.Option(
        None, "--subfolder", help="ComfyUI input subfolder.",
    ),
) -> None:
    """동적 입력 이미지를 ComfyUI 에 업로드. 성공 시 파일명만 stdout 출력.

    셸 파이프 친화적: ``$(af workflow upload pose.png)`` 로 캡처해 다음 명령에
    바로 박을 수 있다.
    """
    if (path is None) == (from_asset is None):
        typer.echo("path 또는 --from-asset 중 하나만 지정해야 합니다.", err=True)
        raise typer.Exit(code=2)

    name = upload_one(path=path, from_asset=from_asset, subfolder=subfolder)
    typer.echo(name)


def upload_one(
    *,
    path: Path | None,
    from_asset: str | None,
    subfolder: str | None,
) -> str:
    """upload 명령의 핵심 — name 문자열 반환. ``af workflow gen --input`` 에서 재사용."""
    if path is not None:
        if not path.exists():
            typer.echo(f"파일이 존재하지 않습니다: {path}", err=True)
            raise typer.Exit(code=1)
        with path.open("rb") as fh:
            files = {"file": (path.name, fh.read(), _guess_content_type(path))}
        data: dict[str, Any] = {}
        if subfolder:
            data["subfolder"] = subfolder
        body = request("POST", "/api/workflows/inputs", files=files, data=data)
    else:
        json_body: dict[str, Any] = {"asset_id": from_asset}
        if subfolder:
            json_body["subfolder"] = subfolder
        body = request("POST", "/api/workflows/inputs/from-asset", json=json_body)

    name = body.get("name")
    if not isinstance(name, str) or not name:
        typer.echo(f"서버 응답에 'name' 필드 없음: {body}", err=True)
        raise typer.Exit(code=1)
    return name


# ----------------------------------------------------------------------------
# gen
# ----------------------------------------------------------------------------


_INPUT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")


@workflow_app.command("gen")
def cmd_gen(
    target: str = typer.Argument(..., help="<category>/<variant>"),
    project: str = typer.Argument(..., help="자산 프로젝트."),
    asset_key: str = typer.Argument(..., help="자산 키."),
    prompt: str = typer.Argument(..., help="positive prompt."),
    negative: str | None = typer.Option(None, "--negative", help="negative prompt."),
    seed: int | None = typer.Option(None, "--seed"),
    candidates: int = typer.Option(1, "--candidates", "-n", min=1, max=16),
    inputs: list[str] = typer.Option(  # noqa: B008
        None,
        "--input",
        "-i",
        metavar="LABEL=VALUE",
        help="동적 입력. @path / asset:<id> / plain string. 반복 사용 가능.",
        show_default=False,
    ),
    bypass_approval: bool = typer.Option(False, "--bypass-approval"),
    wait: bool = typer.Option(False, "--wait", help="job 완료까지 폴링."),
    wait_timeout: float = typer.Option(300.0, "--wait-timeout"),
) -> None:
    """ComfyUI 워크플로우 변형을 호출한다.

    ``--input <label>=<value>`` 는 반복 가능. 로컬 파일은 ``@`` prefix:
    ``--input pose_image=@./pose.png --input source_image=asset:<id>``
    """
    category, variant = _parse_category_variant(target)

    # 1) catalog 조회 — 라벨 검증.
    catalog = request("GET", "/api/workflows/catalog")
    try:
        spec = catalog["categories"][category]["variants"][variant]
    except KeyError as exc:
        typer.echo(f"variant {category}/{variant} 가 카탈로그에 없습니다.", err=True)
        raise typer.Exit(code=1) from exc
    valid_labels = {item["label"] for item in spec.get("input_labels", [])}

    # 2) --input 파싱 + 필요시 upload.
    load_images: dict[str, str] = {}
    for raw in inputs or []:
        m = _INPUT_RE.match(raw)
        if not m:
            typer.echo(
                f"--input 형식 오류 (LABEL=VALUE 필요): {raw}", err=True,
            )
            raise typer.Exit(code=2)
        label, value = m.group(1), m.group(2)
        if label not in valid_labels:
            typer.echo(
                f"variant {category}/{variant} 는 라벨 '{label}' 을 받지 않습니다. "
                f"가능: {sorted(valid_labels) or '(없음)'}",
                err=True,
            )
            raise typer.Exit(code=1)
        load_images[label] = _resolve_input_value(value)

    # 3) generate 요청.
    body: dict[str, Any] = {
        "project": project,
        "asset_key": asset_key,
        "category": category,
        "workflow_category": category,
        "workflow_variant": variant,
        "prompt": prompt,
        "candidates_total": candidates,
        "approval_mode": "bypass" if bypass_approval else "manual",
    }
    if negative is not None:
        body["negative_prompt"] = negative
    if seed is not None:
        body["seed"] = seed
    if load_images:
        body["workflow_params"] = {"load_images": load_images}

    resp = request("POST", "/api/workflows/generate", json=body)
    job_id = resp.get("job_id")
    if not isinstance(job_id, str):
        typer.echo(f"generate 응답에 job_id 없음: {resp}", err=True)
        raise typer.Exit(code=1)

    if not wait:
        typer.echo(json.dumps(resp, ensure_ascii=False))
        return

    # 4) --wait 폴링.
    final = _poll_job(job_id, timeout_seconds=wait_timeout)
    typer.echo(json.dumps(final, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------------
# 내부 헬퍼
# ----------------------------------------------------------------------------


def _parse_category_variant(target: str) -> tuple[str, str]:
    if "/" not in target:
        typer.echo(
            f"category/variant 형식이어야 합니다: {target!r}", err=True,
        )
        raise typer.Exit(code=2)
    category, _, variant = target.partition("/")
    if not category or not variant:
        typer.echo(f"category/variant 형식이어야 합니다: {target!r}", err=True)
        raise typer.Exit(code=2)
    return category, variant


def _resolve_input_value(value: str) -> str:
    """--input 값 → ComfyUI 가 인식하는 파일명. 필요 시 업로드 먼저."""
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        return upload_one(path=path, from_asset=None, subfolder=None)
    if value.startswith("asset:"):
        asset_id = value[len("asset:"):]
        return upload_one(path=None, from_asset=asset_id, subfolder=None)
    if _looks_like_asset_id(value):
        return upload_one(path=None, from_asset=value, subfolder=None)
    return value  # plain — ComfyUI 가 이미 알고 있는 입력 이름.


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _looks_like_asset_id(value: str) -> bool:
    """UUID 만 자동으로 from-asset 으로 라우팅. 비-UUID asset id 는 명시적
    ``asset:<id>`` prefix 를 강제 — 32자 이상의 hex 파일명 (예: sha256 해시
    네이밍된 ComfyUI 입력) 이 false positive 로 from-asset 호출 → 404 가
    되는 걸 막는다.
    """
    return bool(_UUID_RE.match(value))


_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _guess_content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


_TERMINAL_JOB_STATUSES = frozenset({"completed", "completed_with_errors"})


def _poll_job(job_id: str, *, timeout_seconds: float, interval: float = 1.5) -> dict[str, Any]:
    """``GET /api/jobs/{id}`` 폴링. 완료 시 응답 그대로 반환, 타임아웃이면 raise.

    서버가 emit 하는 job-level status (``models.refresh_job_status``):
    - ``queued``                 — 모든 task 가 미시작.
    - ``running``                — 일부 task active.
    - ``completed``              — 모든 task done, failed_count == 0.
    - ``completed_with_errors``  — 모든 task 종료, 일부 failed.
    """
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = request("GET", f"/api/jobs/{job_id}")
        status = last.get("status")
        if status in _TERMINAL_JOB_STATUSES:
            return last
        time.sleep(interval)
    typer.echo(
        f"job {job_id} 가 {timeout_seconds:.0f}s 내에 끝나지 않았습니다. 마지막 상태: {last}",
        err=True,
    )
    raise typer.Exit(code=1)
