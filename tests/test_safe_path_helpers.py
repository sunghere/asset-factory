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
    """``../../../etc/passwd.png`` → ``..`` 가 ``_`` 로 추가 strip 돼 출력에 ``..`` 가
    아예 안 남는다 (defense-in-depth, P1 리뷰 권장). 실제 path traversal 방어는
    ``_safe_subfolder`` 가 책임 — 본 함수는 디스플레이 안전성 + 추가 보강."""
    out = server._safe_input_filename("../../../etc/passwd.png", b"x")
    assert ".." not in out  # 핵심 invariant
    assert out.startswith(_expected_digest(b"x") + "_")
    assert out.endswith(".png")


def test_safe_input_filename_dotdot_never_appears_in_output() -> None:
    """``..`` 가 출력에 절대 새지 않는다 (defense-in-depth invariant).

    edge: stem 끝 ``.`` 과 ext separator ``.`` 가 결합해 ``..`` 부활하는
    케이스도 ``strip(".")`` 으로 차단. ``..png`` 같이 stem 이 단일 ``.`` 라면
    strip 후 빈 문자열 → fallback ``input``.
    """
    for raw in ("..png", "..foo..png", "a..b..c.png", "....png", "...."):
        out = server._safe_input_filename(raw, b"x")
        assert ".." not in out, f"failed for {raw!r}: got {out!r}"


def test_safe_input_filename_dot_only_stem_falls_back_to_input() -> None:
    """``..png`` → stem ``.`` strip 후 빈 문자열 → fallback ``input``."""
    out = server._safe_input_filename("..png", b"x")
    digest = _expected_digest(b"x")
    assert out == f"{digest}_input.png"
