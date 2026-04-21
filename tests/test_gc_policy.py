"""후보 디렉토리 GC."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from candidate_gc import _load_rejected_paths, run_gc_candidates


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
