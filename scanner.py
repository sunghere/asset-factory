"""Scanner module for Asset Factory - 디렉토리 스캔 → 에셋 자동 인식."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def infer_category(relative_path: Path) -> str:
    """경로를 기반으로 카테고리를 추론한다."""
    lowered = str(relative_path).lower()
    if "/ui/" in f"/{lowered}" or lowered.startswith("ui/"):
        return "ui"
    if "/background" in f"/{lowered}" or lowered.startswith("background"):
        return "background"
    if "/item" in f"/{lowered}" or lowered.startswith("item"):
        return "item"
    if "/sprite" in f"/{lowered}" or lowered.startswith("sprites/"):
        return "character"
    return "misc"


def infer_asset_key(relative_path: Path) -> str:
    """파일명 기반 asset_key를 생성한다."""
    stem = relative_path.stem.lower().replace(" ", "_").replace("-", "_")
    # {skin}_{stage}_{action} 패턴을 우선 존중한다.
    match = re.fullmatch(r"([a-z0-9]+)_([a-z0-9]+)_([a-z0-9]+)", stem)
    if match:
        skin, stage, action = match.groups()
        return f"{skin}_{stage}_{action}"
    # {name}_{variant} 패턴
    match = re.fullmatch(r"([a-z0-9]+)_([a-z0-9]+)", stem)
    if match:
        name, variant = match.groups()
        return f"{name}_{variant}"
    return stem


def scan_directory(root_path: Path) -> list[dict[str, Any]]:
    """디렉토리를 순회해 PNG 에셋 목록을 반환한다."""
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"스캔 경로를 찾을 수 없습니다: {root}")

    assets: list[dict[str, Any]] = []
    for file_path in root.rglob("*.png"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root)
        assets.append(
            {
                "asset_key": infer_asset_key(rel),
                "category": infer_category(rel),
                "image_path": str(file_path),
            }
        )
    return assets
