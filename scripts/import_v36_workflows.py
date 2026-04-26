"""
import_v36_workflows.py
========================
generate_cd_workflows.py 가 생성한 C/D V36 패턴 _api_*.json 을
asset-factory/workflows/{pixel_bg,icon}/ 로 복사하고 registry.yml 의 C/D
섹션을 자동 갱신한다.

전제:
  사용자가 먼저 D:\\DEV\\ComfyUI\\comfyuiImage_v34\\ 에서
  `python generate_cd_workflows.py` 를 실행해 _api_*.json 산출물이 존재해야 함.

실행:
    cd D:\\DEV\\asset-factory
    .venv\\Scripts\\python scripts\\import_v36_workflows.py

환경변수:
    COMFYUI_WORKFLOWS_DIR  (default: D:\\DEV\\ComfyUI\\comfyuiImage_v34)
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"
SOURCE_DIR = Path(
    os.getenv("COMFYUI_WORKFLOWS_DIR", r"D:\DEV\ComfyUI\comfyuiImage_v34")
).resolve()


# ----------------------------------------------------------------------------
# Plan: 카테고리 / 변형 / 출력 매핑
# ----------------------------------------------------------------------------
PLAN: dict[str, dict[str, Any]] = {
    "pixel_bg": {
        "description": "픽셀 배경·타일·환경 자산 (V36 generate_cd_workflows 산출).",
        "variants": [
            ("sdxl_stage1", {
                "src": "C1_PixelDiffusionXL_V36_api_stage1.json",
                "description": "PixelDiffusionXL — stage1 only (1024x1024)",
                "primary": False,
                "outputs": [("Save Stage1", "stage1", True)],
                "defaults": {
                    "steps": 30, "cfg": 6.5, "sampler": "dpmpp_2m",
                    "scheduler": "karras", "width": 1024, "height": 1024,
                },
            }),
            ("sdxl_hires", {
                "src": "C1_PixelDiffusionXL_V36_api_hires.json",
                "description": "PixelDiffusionXL — HiRes (1.5x upscale + refine)",
                "primary": True,
                "outputs": [
                    ("Save Stage1", "stage1", False),
                    ("Save HiRes",  "hires",  True),
                ],
                "defaults": {
                    "steps": 30, "cfg": 6.5, "sampler": "dpmpp_2m",
                    "scheduler": "karras", "width": 1024, "height": 1024,
                },
            }),
            ("pony_stage1", {
                "src": "C2_RDXLPixelArt_V36_api_stage1.json",
                "description": "RDXL Pony pixel (Fnaf style) — stage1 only. score_9 트리거 권장",
                "primary": False,
                "outputs": [("Save Stage1", "stage1", True)],
                "defaults": {
                    "steps": 30, "cfg": 5.5, "sampler": "dpmpp_2m",
                    "scheduler": "karras", "width": 1024, "height": 1024,
                },
            }),
            ("pony_hires", {
                "src": "C2_RDXLPixelArt_V36_api_hires.json",
                "description": "RDXL Pony pixel (Fnaf style) — HiRes",
                "primary": False,
                "outputs": [
                    ("Save Stage1", "stage1", False),
                    ("Save HiRes",  "hires",  True),
                ],
                "defaults": {
                    "steps": 30, "cfg": 5.5, "sampler": "dpmpp_2m",
                    "scheduler": "karras", "width": 1024, "height": 1024,
                },
            }),
        ],
    },
    "icon": {
        "description": "앱 UI 아이콘 (V36 + WAS 알파 자동 추출).",
        "variants": [
            ("flat", {
                "src": "D1_AppIcon_V36_api_default.json",
                "description": "Animagine flat icon — 한 실행에 raw + alpha 두 출력",
                "primary": True,
                "outputs": [
                    ("Save Stage1", "stage1", False),
                    ("Save Alpha",  "alpha",  True),
                ],
                "defaults": {
                    "steps": 25, "cfg": 7.0, "sampler": "dpmpp_2m",
                    "scheduler": "karras", "width": 1024, "height": 1024,
                },
            }),
        ],
    },
}


# ----------------------------------------------------------------------------
# YAML rendering — registry.yml 형식 정확히 따라가기
# ----------------------------------------------------------------------------
def _q(s: str) -> str:
    """문자열 YAML 인용 (큰따옴표). 내부 따옴표는 escape."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render_category_yaml(category: str, spec: dict[str, Any]) -> str:
    """registry.yml 의 카테고리 한 블록을 YAML 텍스트로 출력.

    들여쓰기 / 따옴표 / outputs 인라인 형식 등은 기존 매니페스트와 일관.
    """
    INDENT = "  "  # 한 단계 들여쓰기 = 2 space
    lines: list[str] = []
    lines.append(f"{INDENT}{category}:")
    lines.append(f'{INDENT * 2}description: {_q(spec["description"])}')
    lines.append(f"{INDENT * 2}variants:")
    for vname, v in spec["variants"]:
        lines.append("")
        lines.append(f"{INDENT * 3}{vname}:")
        lines.append(f'{INDENT * 4}description: {_q(v["description"])}')
        lines.append(f"{INDENT * 4}file: {category}/{v['src']}")
        if v.get("primary"):
            lines.append(f"{INDENT * 4}primary: true")
        lines.append(f"{INDENT * 4}outputs:")
        for title, label, prim in v["outputs"]:
            primary_str = ", primary: true" if prim else ""
            lines.append(
                f'{INDENT * 5}- {{ node_title: "{title}", label: {label}{primary_str} }}'
            )
        if v["defaults"]:
            lines.append(f"{INDENT * 4}defaults:")
            for k, val in v["defaults"].items():
                rendered = _q(val) if isinstance(val, str) else str(val)
                lines.append(f"{INDENT * 5}{k}: {rendered}")
    return "\n".join(lines)


def replace_category_block(text: str, category: str, new_block: str) -> str:
    """registry.yml 에서 ``  <category>:`` 부터 다음 top-level 카테고리 또는 파일 끝까지를
    new_block 으로 교체.

    매니페스트의 "categories:" 아래는 모두 2-space 들여쓰기 카테고리들이라는
    가정. 다음 top-level pattern 을 lookahead 로 탐지.
    """
    # 다음 카테고리 시작: 줄 시작 + 정확히 2-space + 식별자 + 콜론
    pat = re.compile(
        rf"^( {{2}}{re.escape(category)}:.*?)(?=^ {{2}}[A-Za-z_]\w*:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if not pat.search(text):
        raise RuntimeError(f"registry.yml 에 카테고리 '{category}' 섹션이 없음")
    return pat.sub(new_block.rstrip() + "\n\n", text)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> None:
    if not SOURCE_DIR.exists():
        raise SystemExit(f"[ERROR] SOURCE_DIR 없음: {SOURCE_DIR}")

    print(f"[i] SOURCE: {SOURCE_DIR}")
    print(f"[i] TARGET: {WORKFLOWS_DIR}")

    # ---- 1) 산출물 존재 확인 ----
    missing: list[Path] = []
    for _category, spec in PLAN.items():
        for _, v in spec["variants"]:
            src = SOURCE_DIR / v["src"]
            if not src.exists():
                missing.append(src)
    if missing:
        print("[ERROR] 다음 _api_*.json 산출물이 없습니다:")
        for m in missing:
            print(f"  - {m}")
        raise SystemExit(
            "\n  사용자 측에서 먼저 실행:\n"
            "    cd D:\\DEV\\ComfyUI\\comfyuiImage_v34\n"
            "    python generate_cd_workflows.py"
        )

    # ---- 2) registry.yml 합성 (메모리에서만, 디스크 미반영) ----
    # P1.g — atomicity. 모든 카테고리의 새 블록을 미리 합성한 뒤
    # 1) tmp 파일에 한 번 write,
    # 2) 파일 복사 → 성공 시,
    # 3) tmp → registry.yml 로 rename (또는 부분 실패 시 tmp 삭제).
    registry_path = WORKFLOWS_DIR / "registry.yml"
    if not registry_path.exists():
        raise SystemExit(f"[ERROR] registry.yml 없음: {registry_path}")
    original_text = registry_path.read_text(encoding="utf-8")
    new_text = original_text
    for category, spec in PLAN.items():
        new_block = render_category_yaml(category, spec)
        new_text = replace_category_block(new_text, category, new_block)

    # ---- 3) 파일 복사 (이전 dst 백업 → 실패 시 복구) ----
    backups: list[tuple[Path, bytes | None]] = []  # (dst, 기존 bytes 또는 None)
    copied = 0
    try:
        for category, spec in PLAN.items():
            target = WORKFLOWS_DIR / category
            target.mkdir(parents=True, exist_ok=True)
            for _, v in spec["variants"]:
                src = SOURCE_DIR / v["src"]
                dst = target / v["src"]
                # 기존 파일 보존 (롤백용)
                backups.append(
                    (dst, dst.read_bytes() if dst.exists() else None)
                )
                shutil.copy2(src, dst)
                print(f"  copy  {category}/{v['src']}")
                copied += 1
    except Exception as exc:    # noqa: BLE001
        print(f"[ERROR] 파일 복사 중 실패: {exc} → 롤백 시작")
        for dst, prev in backups:
            try:
                if prev is None:
                    if dst.exists():
                        dst.unlink()
                else:
                    dst.write_bytes(prev)
            except Exception as rb_exc:    # noqa: BLE001
                print(f"  [WARN] 롤백 실패 {dst}: {rb_exc}")
        raise SystemExit("[ABORT] 파일 복사 실패 — registry.yml 변경 안 됨") from exc

    # ---- 4) registry.yml 원자적 교체 (tmp 파일 → rename) ----
    tmp_path = registry_path.with_suffix(registry_path.suffix + ".tmp")
    try:
        tmp_path.write_text(new_text, encoding="utf-8")
        # YAML 파싱 사전 검증 — 깨진 YAML 을 main 파일로 옮기지 않음
        import yaml
        yaml.safe_load(tmp_path.read_text(encoding="utf-8"))
        tmp_path.replace(registry_path)
        print("\n[i] registry.yml updated (atomic rename)")
    except Exception as exc:    # noqa: BLE001
        if tmp_path.exists():
            tmp_path.unlink()
        # 파일 복사는 이미 끝났지만 registry 만 안 바뀐 상태로 남음
        # — 사용자가 다음 실행에서 복구 가능
        raise SystemExit(f"[ABORT] registry.yml 갱신 실패: {exc}") from exc

    print(f"\n[OK] {copied} files copied + registry.yml 갱신.")
    print("\n다음 단계:")
    print("  1) git diff workflows/registry.yml 로 변경 확인")
    print("  2) .venv\\Scripts\\python -m pytest tests/test_workflow_registry.py -q")
    print("  3) (서버 재시작 후) af workflow catalog | jq '.categories.pixel_bg.variants'")


if __name__ == "__main__":
    main()
