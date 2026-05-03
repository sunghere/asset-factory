"""validator.py 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from validator import _count_colors, validate_asset


def test_validate_asset_passes_png_with_alpha(tmp_path: Path) -> None:
    path = tmp_path / "ok.png"
    Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(path, format="PNG")

    result = validate_asset(path, expected_size=4, max_colors=4)

    assert result.passed is True
    assert result.has_alpha is True
    assert result.color_count == 1
    assert result.message == "검증 통과"


def test_validate_asset_reports_size_palette_and_format_failures(tmp_path: Path) -> None:
    path = tmp_path / "bad.jpg"
    image = Image.new("RGB", (4, 4))
    for x in range(4):
        for y in range(4):
            image.putpixel((x, y), (x * 40, y * 40, (x + y) * 20))
    image.save(path, format="JPEG")

    result = validate_asset(path, expected_size=8, max_colors=2)

    assert result.passed is False
    assert result.has_alpha is False
    assert "크기 불일치" in result.message
    assert "팔레트 초과" in result.message
    assert "포맷 오류" in result.message


def test_validate_asset_skips_palette_check_when_max_colors_none(tmp_path: Path) -> None:
    """max_colors=None 이면 팔레트 초과 여부 무관하게 검증 통과."""
    path = tmp_path / "rich.png"
    img = Image.new("RGB", (4, 4))
    for x in range(4):
        for y in range(4):
            img.putpixel((x, y), (x * 64, y * 64, (x + y) * 32))
    img.save(path, format="PNG")

    result = validate_asset(path, expected_size=None, max_colors=None)

    assert result.passed is True
    assert "팔레트" not in result.message


class _MockImageWithExhaustedPalette:
    def convert(self, _mode: str) -> _MockImageWithExhaustedPalette:
        return self

    def getcolors(self, *, maxcolors: int) -> None:
        assert maxcolors == 1024 * 1024
        return None


def test_count_colors_caps_when_palette_exceeds_limit() -> None:
    assert _count_colors(_MockImageWithExhaustedPalette()) == 1024 * 1024


def test_validate_asset_require_alpha_fails_when_no_alpha(tmp_path: Path) -> None:
    """require_alpha=True 이고 알파 채널이 없으면 fail."""
    path = tmp_path / "no_alpha.png"
    Image.new("RGB", (4, 4), (255, 0, 0)).save(path, format="PNG")

    result = validate_asset(path, require_alpha=True)

    assert result.passed is False
    assert result.has_alpha is False
    assert "알파 채널 누락" in result.message


def test_validate_asset_require_alpha_passes_when_has_alpha(tmp_path: Path) -> None:
    """require_alpha=True 이고 알파 채널이 있으면 pass."""
    path = tmp_path / "with_alpha.png"
    Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(path, format="PNG")

    result = validate_asset(path, max_colors=None, require_alpha=True)

    assert result.passed is True
    assert result.has_alpha is True
    assert "알파 채널 누락" not in result.message


def test_validate_asset_require_alpha_false_ignores_missing_alpha(tmp_path: Path) -> None:
    """require_alpha=False (기본값) 이면 알파 없어도 통과."""
    path = tmp_path / "no_alpha.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(path, format="PNG")

    result = validate_asset(path, max_colors=None, require_alpha=False)

    assert result.passed is True
    assert "알파" not in result.message
