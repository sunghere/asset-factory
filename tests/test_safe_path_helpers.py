"""``server._safe_subfolder`` / ``server._safe_input_filename`` 단위 테스트.

ComfyUI ``input/<subfolder>/<filename>`` 안전화 헬퍼 — path traversal /
NUL byte / 비-whitelist 문자 모두 디폴트로 정규화되어야 한다.
"""

from __future__ import annotations

import hashlib

import pytest

import server


# ── _safe_subfolder ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "asset-factory"),
        (None, "asset-factory"),
        ("   ", "asset-factory"),  # whitespace only → 디폴트
        ("asset-factory", "asset-factory"),
        ("user.uploads", "user.uploads"),
        ("Mixed_CASE-1", "Mixed_CASE-1"),
        ("a", "a"),  # 1글자도 OK
        ("a" * 64, "a" * 64),  # 정확히 상한
        ("a" * 65, "asset-factory"),  # 상한 초과 → 디폴트
        ("../etc", "asset-factory"),  # 명시적 ..
        ("..", "asset-factory"),
        ("foo/bar", "asset-factory"),  # 경로 구분자
        ("foo\\bar", "asset-factory"),
        ("foo\x00bar", "asset-factory"),  # NUL byte
        ("/absolute", "absolute"),  # leading / 는 strip 후 통과
        ("\\windows", "windows"),
        ("한글", "asset-factory"),  # 비-ASCII 거부
        ("foo bar", "asset-factory"),  # 공백 거부
        ("foo;bar", "asset-factory"),  # 메타 문자 거부
    ],
)
def test_safe_subfolder_normalizes(raw: str | None, expected: str) -> None:
    assert server._safe_subfolder(raw) == expected


def test_safe_subfolder_default_constant_unchanged() -> None:
    """디폴트가 우연히 바뀌면 광범위한 테스트 + 운영 데이터가 깨진다."""
    assert server._DEFAULT_INPUT_SUBFOLDER == "asset-factory"


# ── _safe_input_filename ─────────────────────────────────────────────────────


def _expected_digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def test_safe_input_filename_basic() -> None:
    out = server._safe_input_filename("foo.png", b"abc")
    assert out == f"{_expected_digest(b'abc')}_foo.png"


def test_safe_input_filename_idempotent_same_bytes() -> None:
    """같은 bytes → 같은 이름 (캐시 hit 검출)."""
    a = server._safe_input_filename("x.png", b"DATA")
    b = server._safe_input_filename("x.png", b"DATA")
    assert a == b


def test_safe_input_filename_different_bytes_different_digest() -> None:
    a = server._safe_input_filename("x.png", b"DATA1")
    b = server._safe_input_filename("x.png", b"DATA2")
    assert a != b


def test_safe_input_filename_sanitizes_non_ascii_stem() -> None:
    out = server._safe_input_filename("한글.png", b"x")
    digest = _expected_digest(b"x")
    # 한/글 각 1 codepoint → 각각 _ 로 치환 → "__"
    assert out == f"{digest}___.png"


def test_safe_input_filename_no_extension_defaults_to_png() -> None:
    out = server._safe_input_filename("noext", b"x")
    assert out == f"{_expected_digest(b'x')}_noext.png"


def test_safe_input_filename_empty_original_falls_back() -> None:
    out = server._safe_input_filename("", b"x")
    assert out == f"{_expected_digest(b'x')}_input.png"


def test_safe_input_filename_none_original_falls_back() -> None:
    out = server._safe_input_filename(None, b"x")
    # None → "input.png" 기본 → stem="input", ext="png"
    assert out == f"{_expected_digest(b'x')}_input.png"


def test_safe_input_filename_clamps_long_stem() -> None:
    long_name = "a" * 200 + ".png"
    out = server._safe_input_filename(long_name, b"x")
    digest = _expected_digest(b"x")
    assert out == f"{digest}_{'a' * 64}.png"


def test_safe_input_filename_clamps_long_extension() -> None:
    out = server._safe_input_filename("foo." + "p" * 20, b"x")
    digest = _expected_digest(b"x")
    assert out == f"{digest}_foo.{'p' * 8}"


def test_safe_input_filename_traversal_in_stem_neutralized() -> None:
    out = server._safe_input_filename("../../../etc/passwd.png", b"x")
    digest = _expected_digest(b"x")
    # / \ . . 모두 _ 또는 . 로 치환됨. 실제 출력은
    # "../../../etc/passwd" → rpartition("." )→ stem="../../../etc/passwd", ext="png"
    # stem 안의 `/` `.` 중 `/` 만 _ 로 치환 (`.` 은 whitelist)
    assert out == f"{digest}_.._.._.._etc_passwd.png"


def test_safe_input_filename_leading_dot_preserved() -> None:
    """``.`` 은 whitelist 통과 — subfolder 검증과 책임 분리. 실제 traversal 방어는
    ``_safe_subfolder`` 가 담당하고 파일명은 디스플레이 안전성만 확보."""
    # "..png" 는 rpartition(".") → (".", ".", "png") 이라 stem="."
    out = server._safe_input_filename("..png", b"x")
    digest = _expected_digest(b"x")
    assert out == f"{digest}_..png"
