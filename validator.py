"""Validator module for Asset Factory - 에셋 기술 검증."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(slots=True)
class ValidationResult:
    """에셋 검증 결과."""

    width: int
    height: int
    image_format: str
    color_count: int
    has_alpha: bool
    passed: bool
    message: str


def _count_colors(image: Image.Image) -> int:
    """이미지 색상 수를 계산한다."""
    converted = image.convert("RGBA")
    # getcolors(maxcolors=0)은 지원되지 않아 충분히 큰 상한을 사용한다.
    palette = converted.getcolors(maxcolors=1024 * 1024)
    if palette is None:
        return 1024 * 1024
    return len(palette)


def validate_asset(
    image_path: Path,
    expected_size: int | None = None,
    max_colors: int | None = 32,
) -> ValidationResult:
    """이미지를 메타데이터 기반으로 검증한다."""
    with Image.open(image_path) as image:
        width, height = image.size
        image_format = image.format or "UNKNOWN"
        has_alpha = "A" in image.mode or image.mode in {"LA", "PA"}
        color_count = _count_colors(image)

    messages: list[str] = []
    passed = True

    if expected_size is not None and (width != expected_size or height != expected_size):
        passed = False
        messages.append(f"크기 불일치: {width}x{height} (기대값 {expected_size}x{expected_size})")
    if max_colors is not None and color_count > max_colors:
        passed = False
        messages.append(f"팔레트 초과: {color_count}색 (최대 {max_colors}색)")
    if image_format.upper() != "PNG":
        passed = False
        messages.append(f"포맷 오류: {image_format} (PNG 필요)")

    if not messages:
        messages.append("검증 통과")

    return ValidationResult(
        width=width,
        height=height,
        image_format=image_format,
        color_count=color_count,
        has_alpha=has_alpha,
        passed=passed,
        message="; ".join(messages),
    )
