"""SD 클라이언트 오류 분류."""

from __future__ import annotations

from generator import SDError, _classify_sd_failure


def test_classify_timeout() -> None:
    assert _classify_sd_failure(504, "Gateway timeout") == "timeout"
    assert _classify_sd_failure(500, "upstream timeout") == "timeout"


def test_classify_oom() -> None:
    assert _classify_sd_failure(500, "CUDA out of memory") == "oom"
    assert _classify_sd_failure(500, "RuntimeError: OOM") == "oom"


def test_classify_server_error_default() -> None:
    assert _classify_sd_failure(500, "generic") == "sd_server_error"


def test_sd_error_string_includes_code() -> None:
    err = SDError("bad", code="unreachable", http_status=None)
    assert "[unreachable]" in str(err)
