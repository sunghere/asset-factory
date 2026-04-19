"""SD 모델/LoRA 카탈로그 로더.

A1111이 노출하는 /sdapi/v1/sd-models, /sdapi/v1/loras 응답에 사람이 큐레이션한
메타데이터(tags, notes, weight_default, weight_range)를 덧붙여 반환한다.

YAML 파일 형식은 [config/sd_catalog.yml](config/sd_catalog.yml) 참고.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _normalize_key(value: str | None) -> str:
    """키 매칭을 위한 정규화: 소문자화 + 확장자/공백 제거."""
    if not value:
        return ""
    name = str(value).strip().lower()
    for ext in (".safetensors", ".ckpt", ".pt", ".bin"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name


def load_catalog_yaml(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """카탈로그 YAML을 읽어 ``{"models": {...}, "loras": {...}}`` 형태로 반환.

    파일이 없거나 비어있으면 빈 dict를 반환한다 (실패하지 않음).
    키는 ``_normalize_key``로 정규화한다.
    """
    empty: dict[str, dict[str, dict[str, Any]]] = {"models": {}, "loras": {}}
    if not path.exists():
        return empty
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return empty

    result: dict[str, dict[str, dict[str, Any]]] = {"models": {}, "loras": {}}
    for bucket in ("models", "loras"):
        items = raw.get(bucket) or {}
        if not isinstance(items, dict):
            continue
        for raw_key, meta in items.items():
            if not isinstance(meta, dict):
                continue
            result[bucket][_normalize_key(raw_key)] = dict(meta)
    return result


def merge_models(
    sd_models: list[dict[str, Any]],
    catalog: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """A1111 모델 목록 + YAML 모델 메타 병합."""
    overlay = catalog.get("models", {})
    merged: list[dict[str, Any]] = []
    for item in sd_models:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name") or item.get("title") or item.get("name")
        meta = overlay.get(_normalize_key(name), {})
        merged.append(
            {
                "name": name,
                "title": item.get("title"),
                "hash": item.get("hash") or item.get("sha256"),
                "filename": item.get("filename"),
                "tags": list(meta.get("tags") or []),
                "notes": meta.get("notes"),
                "has_metadata": bool(meta),
            }
        )
    return merged


def merge_loras(
    sd_loras: list[dict[str, Any]],
    catalog: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """A1111 LoRA 목록 + YAML LoRA 메타 병합."""
    overlay = catalog.get("loras", {})
    merged: list[dict[str, Any]] = []
    for item in sd_loras:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("alias")
        meta = overlay.get(_normalize_key(name), {})
        weight_range = meta.get("weight_range")
        if isinstance(weight_range, list) and len(weight_range) == 2:
            weight_range_out = [float(weight_range[0]), float(weight_range[1])]
        else:
            weight_range_out = [0.0, 1.0]
        merged.append(
            {
                "name": name,
                "alias": item.get("alias"),
                "path": item.get("path"),
                "weight_default": float(meta.get("weight_default", 0.7)),
                "weight_range": weight_range_out,
                "tags": list(meta.get("tags") or []),
                "notes": meta.get("notes"),
                "has_metadata": bool(meta),
            }
        )
    return merged
