"""후보 이미지 디렉토리 GC (보관 기간 / 총 용량 상한)."""

from __future__ import annotations

import os
import time
from pathlib import Path


def run_gc_candidates(
    data_root: Path,
    *,
    max_age_seconds: float | None = None,
    max_total_bytes: int | None = None,
) -> dict[str, int | float]:
    """
    data/candidates/ 아래 파일을 정리한다.

    환경변수:
    - CANDIDATE_GC_MAX_AGE_DAYS (기본 7)
    - CANDIDATE_GC_MAX_BYTES_GB (기본 1, 0이면 용량 상한 비활성)
    """
    if max_age_seconds is None:
        days = float(os.getenv("CANDIDATE_GC_MAX_AGE_DAYS", "7"))
        max_age_seconds = days * 86400.0
    if max_total_bytes is None:
        gb = float(os.getenv("CANDIDATE_GC_MAX_BYTES_GB", "1"))
        max_total_bytes = int(gb * (1024**3)) if gb > 0 else 0

    candidates_root = data_root / "candidates"
    if not candidates_root.is_dir():
        return {"deleted_files": 0, "freed_bytes": 0, "scanned_files": 0}

    now = time.time()
    files: list[tuple[Path, float, int]] = []

    for path in candidates_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((path, st.st_mtime, st.st_size))

    deleted_files = 0
    freed_bytes = 0

    # 1) 오래된 파일 삭제
    if max_age_seconds > 0:
        for path, mtime, size in files:
            if now - mtime > max_age_seconds:
                try:
                    path.unlink()
                    deleted_files += 1
                    freed_bytes += size
                except OSError:
                    pass

    # 2) 남은 파일이 용량 상한 초과 시 오래된 것부터 삭제
    if max_total_bytes > 0:
        remaining: list[tuple[Path, float, int]] = []
        for path in candidates_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            remaining.append((path, st.st_mtime, st.st_size))

        total = sum(s for _, _, s in remaining)
        remaining.sort(key=lambda x: x[1])  # oldest mtime first
        while total > max_total_bytes and remaining:
            path, _mtime, size = remaining.pop(0)
            try:
                path.unlink()
                deleted_files += 1
                freed_bytes += size
                total -= size
            except OSError:
                total -= size

    scanned = sum(1 for p in candidates_root.rglob("*") if p.is_file())
    return {
        "deleted_files": deleted_files,
        "freed_bytes": freed_bytes,
        "scanned_files": scanned,
    }
