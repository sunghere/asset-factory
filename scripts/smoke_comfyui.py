"""ComfyUI 백엔드 통합 PoC 스모크 스크립트.

asset-factory 서버 통합 (Phase 3) 전에 standalone 으로
**WorkflowRegistry → patcher → ComfyUIClient → 이미지 저장** 흐름을
end-to-end 검증한다.

Usage::

    # 1) ComfyUI 호출 없이 패치 결과만 보기 (logic 검증)
    python scripts/smoke_comfyui.py \\
        --variant sprite/pixel_alpha \\
        --prompt "1girl, silver hair" \\
        --seed 42 \\
        --dry-run

    # 2) 실제 ComfyUI 호출 (ComfyUI 가 localhost:8188 에서 떠있어야 함)
    python scripts/smoke_comfyui.py \\
        --variant sprite/pixel_alpha \\
        --prompt "1girl, silver hair, school uniform" \\
        --seed 42 \\
        --out scripts/_out_smoke

    # 3) health check 만
    python scripts/smoke_comfyui.py --health --host localhost:8188

성공 기준:
- dry-run: 패치 적용 키 + 결과 JSON 의 노드별 변경 출력
- 실제 호출: out/ 에 SaveImage 노드 수만큼 PNG 저장 + 각 파일 크기 보고
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Repo root 를 import path 에 추가 (scripts/ 에서 직접 실행 가능하게)
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from generator_comfyui import ComfyUIClient  # noqa: E402
from workflow_patcher import patch_workflow   # noqa: E402
from workflow_registry import WorkflowRegistry  # noqa: E402


def parse_variant(value: str) -> tuple[str, str]:
    if "/" not in value:
        raise argparse.ArgumentTypeError(
            f"variant 는 'category/name' 형식이어야 함 (got {value!r})"
        )
    cat, name = value.split("/", 1)
    return cat.strip(), name.strip()


async def cmd_health(host: str) -> int:
    client = ComfyUIClient(host=host, request_timeout=10.0)
    try:
        info = await client.health_check()
    except Exception as exc:
        print(f"[ERROR] ComfyUI health check 실패: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    cat, vname = args.variant
    reg = WorkflowRegistry(root=REPO_ROOT / "workflows")

    try:
        variant = reg.variant(cat, vname)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not variant.available:
        print(
            f"[ERROR] variant {cat}/{vname} 는 호출 불가 (status={variant.status})",
            file=sys.stderr,
        )
        return 2

    # ---- 1) 워크플로우 로드 + 기본값 적용 + CLI 인자 오버라이드 ---------------
    wf = variant.load_api_json()

    # variant.defaults 는 매니페스트가 가진 기본값. CLI 인자가 있으면 그걸 우선.
    defaults = variant.defaults
    patch_kwargs: dict = {
        "prompt": args.prompt,
        "negative_prompt": args.negative_prompt or defaults.get("negative_prompt"),
        "seed": args.seed,
        "steps": args.steps if args.steps is not None else defaults.get("steps"),
        "cfg": args.cfg if args.cfg is not None else defaults.get("cfg"),
        "sampler_name": args.sampler or defaults.get("sampler"),
        "scheduler": args.scheduler or defaults.get("scheduler"),
        "pose_image": args.pose_image or defaults.get("pose_image"),
        "controlnet_strength": (
            args.controlnet_strength
            if args.controlnet_strength is not None
            else defaults.get("controlnet_strength")
        ),
        "width": args.width or defaults.get("width"),
        "height": args.height or defaults.get("height"),
    }

    patched, report = patch_workflow(wf, **patch_kwargs)

    print(f"\n=== variant {cat}/{vname} ===")
    print(f"  description : {variant.description}")
    print(f"  file        : {variant.file}")
    print(f"  outputs     : {[o.label for o in variant.outputs]}")
    print(f"  primary     : {variant.primary_output.label if variant.primary_output else None}")

    print(f"\n=== patch report ===")
    for k, ids in report.applied.items():
        print(f"  applied  {k:20s} -> nodes {ids}")
    for k in report.skipped:
        print(f"  skipped  {k}")

    # ---- 2) dry-run 분기 ---------------------------------------------------
    out_dir = Path(args.out).resolve() if args.out else REPO_ROOT / "scripts" / "_out_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        dump_path = out_dir / f"{cat}_{vname}_patched.json"
        dump_path.write_text(
            json.dumps(patched, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n[DRY-RUN] 패치된 워크플로우 저장: {dump_path}")
        return 0

    # ---- 3) 실제 ComfyUI 호출 ---------------------------------------------
    client = ComfyUIClient(
        host=args.host,
        request_timeout=30.0,
        completion_timeout=args.timeout,
        poll_interval=2.0,
    )

    print(f"\n=== ComfyUI submit (host={args.host}) ===")
    try:
        health = await client.health_check()
        print(f"  health: {health}")
    except Exception as exc:
        print(f"[ERROR] ComfyUI 연결 실패: {exc}", file=sys.stderr)
        print("        ComfyUI 가 떠있는지, --host 가 맞는지 확인.", file=sys.stderr)
        return 3

    try:
        result = await client.submit_and_wait(patched, timeout=args.timeout)
    except Exception as exc:
        print(f"[ERROR] 워크플로우 실행 실패: {exc}", file=sys.stderr)
        return 4

    print(f"\n=== 결과 ===")
    print(f"  prompt_id : {result.prompt_id}")
    print(f"  outputs   : {len(result.outputs)} images")

    # ---- 4) 결과 저장 (variant 의 output 라벨로 파일명 구성) ------------------
    title_to_label: dict[str, str] = {}
    for o in variant.outputs:
        # 노드 ID 는 history 에서 받지만, registry 가 가진 건 title.
        # patched 안에서 title → node_id 역매핑 만들어서 라벨 붙임.
        for nid, node in patched.items():
            if node.get("_meta", {}).get("title") == o.node_title:
                title_to_label[nid] = o.label
                break

    saved: list[Path] = []
    for img in result.outputs:
        label = title_to_label.get(img.node_id, f"node{img.node_id}")
        out_path = out_dir / f"{cat}_{vname}__{label}__{img.filename}"
        out_path.write_bytes(img.image_bytes)
        saved.append(out_path)
        print(f"  saved {len(img.image_bytes):>8} bytes -> {out_path}")

    print(f"\n=== 완료. {len(saved)} files in {out_dir} ===")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ComfyUI 백엔드 통합 PoC 스모크")
    p.add_argument("--health", action="store_true", help="ComfyUI health check 만 실행")
    p.add_argument("--host", default="localhost:8188", help="ComfyUI host:port")
    p.add_argument("--variant", type=parse_variant,
                   help="레지스트리 변형 'category/name' (예: sprite/pixel_alpha)")
    p.add_argument("--prompt", help="positive prompt (없으면 워크플로우 기본값 유지)")
    p.add_argument("--negative-prompt", dest="negative_prompt")
    p.add_argument("--seed", type=int)
    p.add_argument("--steps", type=int)
    p.add_argument("--cfg", type=float)
    p.add_argument("--sampler")
    p.add_argument("--scheduler")
    p.add_argument("--pose-image", dest="pose_image",
                   help="ComfyUI input/ 안의 pose grid 파일명")
    p.add_argument("--controlnet-strength", dest="controlnet_strength", type=float)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--timeout", type=float, default=600.0,
                   help="워크플로우 완료 대기 timeout (초)")
    p.add_argument("--out", help="결과 저장 디렉토리 (기본 scripts/_out_smoke/)")
    p.add_argument("--dry-run", action="store_true",
                   help="ComfyUI 호출 없이 패치된 워크플로우만 dump")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.health:
        return asyncio.run(cmd_health(args.host))
    if not args.variant:
        print("[ERROR] --variant 가 필요합니다 (--health 단독 사용 외)", file=sys.stderr)
        return 2
    return asyncio.run(cmd_run(args))


if __name__ == "__main__":
    sys.exit(main())
