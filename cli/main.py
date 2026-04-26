"""`af` CLI 루트.

서브 그룹:
- ``af workflow`` — ComfyUI 워크플로우 변형 호출 / 카탈로그 / 동적 입력 업로드.

향후 ``af list``, ``af get``, ``af export`` 추가 예정 (현 PR 범위 밖 — 사용자
는 기존 REST API 또는 web UI 로 접근).
"""

from __future__ import annotations

import typer

from cli.commands.workflow import workflow_app

app = typer.Typer(
    name="af",
    help="asset-factory CLI — 워크플로우 변형 호출 + 동적 입력 업로드.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(workflow_app, name="workflow", help="ComfyUI 워크플로우 변형 명령들.")


if __name__ == "__main__":  # pragma: no cover
    app()
