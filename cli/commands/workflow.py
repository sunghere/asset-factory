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
# §1.C recommend / search
# ----------------------------------------------------------------------------


@workflow_app.command("recommend")
def cmd_recommend(
    query: str = typer.Argument(..., help='자연어 의도 (예: "RPG 캐릭터 픽셀 스프라이트")'),
    top: int = typer.Option(3, "--top", "-n", min=1, max=50),
    include_unavailable: bool = typer.Option(
        False, "--include-unavailable",
        help="status=needs_api_conversion 변형도 후보에 포함.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="raw JSON 출력 (스크립트/파이프라인용).",
    ),
) -> None:
    """자연어 query → 점수 매긴 변형 후보 top-N (spec §1.C).

    SKILL.md 의 변형 선택 4-step 1단계 — 의도 한 줄로 후보 받기.
    """
    body = request(
        "POST", "/api/workflows/recommend",
        json={
            "query": query,
            "top": top,
            "include_unavailable": include_unavailable,
        },
    )
    if json_out:
        typer.echo(json.dumps(body, ensure_ascii=False, indent=2))
        return

    # 사람 친화 출력 — table 형식.
    candidates = body.get("candidates", [])
    if not candidates:
        typer.echo("(매칭된 변형 없음)")
        return
    typer.echo(f"# scoring_method: {body.get('scoring_method', 'rule')}")
    typer.echo(f"# query: {body.get('query', '')}")
    typer.echo("")
    for i, c in enumerate(candidates, start=1):
        intent = c.get("intent", "").split("\n")[0][:80]
        typer.echo(f"{i:>2}. {c['variant']:<40} score={c['score']:.2f}")
        typer.echo(f"    intent: {intent}")
        if c.get("tags_hit"):
            typer.echo(f"    tags_hit: {', '.join(c['tags_hit'])}")
        if c.get("not_for_warnings"):
            typer.echo("    ⚠ not_for warnings:")
            for w in c["not_for_warnings"]:
                typer.echo(f"      - {w}")


@workflow_app.command("search")
def cmd_search(
    tag: list[str] = typer.Option(  # noqa: B008
        None, "--tag",
        metavar="TAG",
        help="포함해야 할 태그 (반복 사용 = AND).",
        show_default=False,
    ),
    not_: list[str] = typer.Option(  # noqa: B008
        None, "--not",
        metavar="TAG",
        help="제외할 태그 (반복 사용 = AND-NOT).",
        show_default=False,
    ),
    include_unavailable: bool = typer.Option(False, "--include-unavailable"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """tag 정확 매칭 필터 — 사용자가 정확한 tag 이름을 알 때 빠른 색인.

    예: ``af workflow search --tag pose-sheet --tag transparent-bg --not scenery``
    """
    params: dict[str, Any] = {
        "tag": tag or [],
        "not": not_ or [],
        "include_unavailable": include_unavailable,
    }
    body = request("GET", "/api/workflows/search", params=params)
    if json_out:
        typer.echo(json.dumps(body, ensure_ascii=False, indent=2))
        return
    matches = body.get("matches", [])
    if not matches:
        typer.echo("(매칭된 변형 없음)")
        return
    for m in matches:
        intent = m.get("intent", "").split("\n")[0][:80]
        tags = ", ".join(m.get("tags_hit", []))
        typer.echo(f"  {m['variant']:<40}")
        typer.echo(f"    intent: {intent}")
        if tags:
            typer.echo(f"    tags: {tags}")


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
    prompt: str = typer.Argument(
        "",
        help=(
            "positive prompt (legacy 모드). subject 모드 사용 시 빈 문자열로 두고 "
            "--subject 로 캐릭터 묘사만 전달."
        ),
    ),
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
    # §1.B subject-injection
    subject: str | None = typer.Option(
        None,
        "--subject",
        help=(
            "subject 모드 명시 — 캐릭터/객체 묘사만. 변형의 base_positive/"
            "base_negative 로 합성. SKILL.md 의 변형 선택 4-step 권장 모드."
        ),
    ),
    prompt_mode: str = typer.Option(
        "auto",
        "--prompt-mode",
        help="auto | legacy | subject. legacy = prompt 통째 입력, subject = 강제 합성.",
    ),
    style_extra: str | None = typer.Option(
        None, "--style-extra",
        help="subject 모드의 base_positive 뒤에 추가될 사용자 prose (선택).",
    ),
    wait: bool = typer.Option(False, "--wait", help="job 완료까지 폴링."),
    wait_timeout: float = typer.Option(300.0, "--wait-timeout"),
) -> None:
    """ComfyUI 워크플로우 변형을 호출한다.

    SKILL.md 의 변형 선택 4-step 권장 — ``--subject "<캐릭터 묘사만>"`` 사용.
    legacy 호출 (``prompt`` 통째 입력) 도 지원.

    ``--input <label>=<value>`` 는 반복 가능. 로컬 파일은 ``@`` prefix:
    ``--input pose_image=@./pose.png --input source_image=asset:<id>``
    """
    if not prompt and not subject:
        typer.echo("prompt 또는 --subject 중 하나는 필수입니다.", err=True)
        raise typer.Exit(code=2)

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
        "prompt": prompt or "",
        "candidates_total": candidates,
        "approval_mode": "bypass" if bypass_approval else "manual",
        "prompt_mode": prompt_mode,
    }
    if subject is not None:
        body["subject"] = subject
    if style_extra is not None:
        body["style_extra"] = style_extra
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
