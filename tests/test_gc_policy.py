"""후보 디렉토리 GC."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from candidate_gc import (
    _load_bypass_candidate_paths,
    _load_rejected_paths,
    get_bypass_retention_days,
    run_gc_candidates,
)


def test_gc_removes_files_older_than_max_age(tmp_path: Path) -> None:
    root = tmp_path / "data"
    cand = root / "candidates" / "proj" / "asset"
    cand.mkdir(parents=True)
    old_file = cand / "stale.png"
    old_file.write_bytes(b"old")
    ancient = time.time() - 10 * 86400
    os.utime(old_file, (ancient, ancient))

    result = run_gc_candidates(root, max_age_seconds=86400, max_total_bytes=0)
    assert result["deleted_files"] == 1
    assert not old_file.exists()


def test_gc_trims_by_total_size(tmp_path: Path) -> None:
    root = tmp_path / "data"
    cand = root / "candidates" / "p"
    cand.mkdir(parents=True)
    a = cand / "a.bin"
    b = cand / "b.bin"
    a.write_bytes(b"x" * 400)
    b.write_bytes(b"y" * 400)
    now = time.time()
    os.utime(a, (now - 100, now - 100))
    os.utime(b, (now, now))

    result = run_gc_candidates(root, max_age_seconds=0, max_total_bytes=500)
    assert result["deleted_files"] >= 1
    total_left = sum(p.stat().st_size for p in cand.iterdir() if p.is_file())
    assert total_left <= 500


def test_load_rejected_paths_returns_empty_on_schema_error(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    conn = sqlite3.connect(root / "asset-factory.db")
    try:
        conn.execute("CREATE TABLE asset_candidates (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    assert _load_rejected_paths(root) == set()


def test_gc_deletes_rejected_files_only_inside_candidates_dir(tmp_path: Path) -> None:
    root = tmp_path / "data"
    candidates = root / "candidates" / "proj"
    candidates.mkdir(parents=True)
    rejected = candidates / "rejected.png"
    rejected.write_bytes(b"reject")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    conn = sqlite3.connect(root / "asset-factory.db")
    try:
        conn.execute("CREATE TABLE asset_candidates (image_path TEXT, is_rejected INTEGER)")
        conn.execute(
            "INSERT INTO asset_candidates(image_path, is_rejected) VALUES (?, 1), (?, 1)",
            (str(rejected), str(outside)),
        )
        conn.commit()
    finally:
        conn.close()

    result = run_gc_candidates(root, max_age_seconds=0, max_total_bytes=0)

    assert result["deleted_files"] == 1
    assert rejected.exists() is False
    assert outside.exists() is True


# ----------------------------------------------------------------------------
# Bypass 모드 GC (P0-1)
# ----------------------------------------------------------------------------


def _seed_candidates_table(db_path: Path, rows: list[tuple[str, str]]) -> None:
    """테스트 DB 에 (image_path, approval_mode) 행을 박는다."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE asset_candidates (image_path TEXT, is_rejected INTEGER NOT NULL DEFAULT 0, "
            "approval_mode TEXT NOT NULL DEFAULT 'manual')"
        )
        conn.executemany(
            "INSERT INTO asset_candidates(image_path, is_rejected, approval_mode) VALUES (?, 0, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_load_bypass_candidate_paths(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    candidates = root / "candidates"
    candidates.mkdir()
    bypass_path = candidates / "bypass.png"
    manual_path = candidates / "manual.png"
    bypass_path.write_bytes(b"b")
    manual_path.write_bytes(b"m")
    _seed_candidates_table(
        root / "asset-factory.db",
        [(str(bypass_path), "bypass"), (str(manual_path), "manual")],
    )

    paths = _load_bypass_candidate_paths(root)
    assert paths == {str(bypass_path)}


def test_load_bypass_paths_handles_missing_column(tmp_path: Path) -> None:
    """approval_mode 컬럼 없는 구 DB → 빈 set, GC 는 계속 동작."""
    root = tmp_path / "data"
    root.mkdir()
    conn = sqlite3.connect(root / "asset-factory.db")
    try:
        conn.execute("CREATE TABLE asset_candidates (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    assert _load_bypass_candidate_paths(root) == set()


def test_gc_bypass_uses_short_retention(tmp_path: Path) -> None:
    """bypass 후보는 bypass_max_age 로 청소되고, manual 은 일반 max_age 로."""
    root = tmp_path / "data"
    cand = root / "candidates"
    cand.mkdir(parents=True)
    bypass_old = cand / "bypass_old.png"
    bypass_recent = cand / "bypass_recent.png"
    manual_old = cand / "manual_old.png"
    bypass_old.write_bytes(b"a")
    bypass_recent.write_bytes(b"b")
    manual_old.write_bytes(b"c")

    now = time.time()
    # bypass: 5일 전 (bypass cutoff 1일 → 삭제), 1시간 전 (보존)
    os.utime(bypass_old, (now - 5 * 86400, now - 5 * 86400))
    os.utime(bypass_recent, (now - 3600, now - 3600))
    # manual: 5일 전 (manual cutoff 30일 → 보존)
    os.utime(manual_old, (now - 5 * 86400, now - 5 * 86400))

    _seed_candidates_table(
        root / "asset-factory.db",
        [
            (str(bypass_old), "bypass"),
            (str(bypass_recent), "bypass"),
            (str(manual_old), "manual"),
        ],
    )

    run_gc_candidates(
        root,
        max_age_seconds=30 * 86400,  # manual 30일
        bypass_max_age_seconds=86400,  # bypass 1일
        max_total_bytes=0,
    )

    assert bypass_old.exists() is False, "오래된 bypass 는 삭제돼야 함"
    assert bypass_recent.exists() is True, "최근 bypass 는 보존"
    assert manual_old.exists() is True, "manual 은 manual cutoff 적용 → 보존"


def test_gc_bypass_also_deletes_dangling_db_rows(tmp_path: Path) -> None:
    """bypass GC 가 파일 unlink 후 asset_candidates 행도 삭제 (dangling 방지)."""
    root = tmp_path / "data"
    cand = root / "candidates"
    cand.mkdir(parents=True)
    bypass_old = cand / "bypass_old.png"
    bypass_old.write_bytes(b"a")
    now = time.time()
    os.utime(bypass_old, (now - 5 * 86400, now - 5 * 86400))

    _seed_candidates_table(
        root / "asset-factory.db",
        [(str(bypass_old), "bypass")],
    )

    run_gc_candidates(
        root,
        max_age_seconds=30 * 86400,
        bypass_max_age_seconds=86400,
        max_total_bytes=0,
    )

    assert bypass_old.exists() is False
    # DB 행도 삭제됐는지 확인
    conn = sqlite3.connect(root / "asset-factory.db")
    try:
        cur = conn.execute("SELECT COUNT(*) FROM asset_candidates")
        count = cur.fetchone()[0]
    finally:
        conn.close()
    assert count == 0, "bypass GC 후 asset_candidates 행도 삭제돼야 함"


def test_gc_bypass_db_cleanup_handles_missing_db_gracefully(tmp_path: Path) -> None:
    """DB 없는 환경 (data_root 만 존재) — silent skip, GC 자체는 동작."""
    root = tmp_path / "data"
    cand = root / "candidates"
    cand.mkdir(parents=True)
    f = cand / "x.png"
    f.write_bytes(b"x")
    now = time.time()
    os.utime(f, (now - 100 * 86400, now - 100 * 86400))

    # DB 미존재
    result = run_gc_candidates(root, max_age_seconds=86400, max_total_bytes=0)
    assert result["deleted_files"] == 1


def test_get_bypass_retention_days_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AF_BYPASS_RETENTION_DAYS", raising=False)
    assert get_bypass_retention_days() == 7.0


def test_get_bypass_retention_days_env_override(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AF_BYPASS_RETENTION_DAYS", "2")
    assert get_bypass_retention_days() == 2.0
