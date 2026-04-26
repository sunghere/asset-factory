"""후보 이미지 디렉토리 GC (rejected 우선 → 보관 기간 → 총 용량 상한)."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


def _load_rejected_paths(data_root: Path) -> set[str]:
    """``asset_candidates.is_rejected=1`` 인 후보의 image_path를 모은다.

    DB가 없거나 컬럼이 누락된 환경에서는 빈 set을 반환한다 (GC는 계속 동작).
    """
    db_path = data_root / "asset-factory.db"
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT image_path FROM asset_candidates WHERE is_rejected=1"
            )
            return {str(row[0]) for row in cursor.fetchall() if row[0]}
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def _load_bypass_candidate_paths(data_root: Path) -> set[str]:
    """``approval_mode='bypass'`` 후보의 image_path를 모은다.

    bypass 후보는 별도의 (보통 더 짧은) retention 으로 청소된다.
    DB/컬럼 누락 환경에서는 빈 set 반환.
    """
    db_path = data_root / "asset-factory.db"
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(
                "SELECT image_path FROM asset_candidates WHERE approval_mode='bypass'"
            )
            return {str(row[0]) for row in cursor.fetchall() if row[0]}
        finally:
            conn.close()
    except sqlite3.Error:
        return set()


def _delete_candidate_rows(data_root: Path, image_paths: list[str]) -> int:
    """``asset_candidates.image_path IN (...)`` 행을 삭제. 삭제된 행 수 반환.

    bypass GC 가 파일을 unlink 한 직후 호출 — DB 의 dangling 행 (file 없는데
    asset_candidates 에 남아 image fetch 가 FS 에러내는 케이스) 을 막는다.
    DB/컬럼 누락 환경은 silent skip (GC 자체는 계속 동작).
    """
    if not image_paths:
        return 0
    db_path = data_root / "asset-factory.db"
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            placeholders = ",".join("?" * len(image_paths))
            cursor = conn.execute(
                f"DELETE FROM asset_candidates WHERE image_path IN ({placeholders})",
                image_paths,
            )
            conn.commit()
            return cursor.rowcount or 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def get_bypass_retention_days() -> float:
    """Bypass candidate 보관 일수. ``AF_BYPASS_RETENTION_DAYS`` env (기본 7)."""
    return float(os.getenv("AF_BYPASS_RETENTION_DAYS", "7"))


def run_gc_candidates(
    data_root: Path,
    *,
    max_age_seconds: float | None = None,
    max_total_bytes: int | None = None,
    bypass_max_age_seconds: float | None = None,
) -> dict[str, int | float]:
    """
    data/candidates/ 아래 파일을 정리한다.

    삭제 우선순위:
    1) ``asset_candidates.is_rejected=1`` 로 마킹된 후보 (cherry-pick에서 사람이 거른 것)
    2) bypass 후보 (approval_mode='bypass') 중 ``AF_BYPASS_RETENTION_DAYS`` 보다 오래된 것
    3) ``CANDIDATE_GC_MAX_AGE_DAYS`` 보다 오래된 파일
    4) 총 용량이 ``CANDIDATE_GC_MAX_BYTES_GB`` 초과 시 mtime이 오래된 것부터

    환경변수:
    - CANDIDATE_GC_MAX_AGE_DAYS (기본 7)
    - AF_BYPASS_RETENTION_DAYS (기본 7) — bypass 후보 전용 보관일
    - CANDIDATE_GC_MAX_BYTES_GB (기본 1, 0이면 용량 상한 비활성)
    """
    if max_age_seconds is None:
        days = float(os.getenv("CANDIDATE_GC_MAX_AGE_DAYS", "7"))
        max_age_seconds = days * 86400.0
    if bypass_max_age_seconds is None:
        bypass_max_age_seconds = get_bypass_retention_days() * 86400.0
    if max_total_bytes is None:
        gb = float(os.getenv("CANDIDATE_GC_MAX_BYTES_GB", "1"))
        max_total_bytes = int(gb * (1024**3)) if gb > 0 else 0

    candidates_root = data_root / "candidates"
    if not candidates_root.is_dir():
        return {"deleted_files": 0, "freed_bytes": 0, "scanned_files": 0}

    rejected_paths = _load_rejected_paths(data_root)
    bypass_paths = _load_bypass_candidate_paths(data_root)
    now = time.time()

    deleted_files = 0
    freed_bytes = 0

    # 1) reject된 후보 우선 삭제 (mtime/용량 무시)
    for raw in rejected_paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(candidates_root.resolve())
        except (OSError, ValueError):
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted_files += 1
            freed_bytes += size
        except OSError:
            pass

    files: list[tuple[Path, float, int]] = []
    for path in candidates_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((path, st.st_mtime, st.st_size))

    # 2) bypass 후보 — bypass 전용 보관일로 별도 처리.
    # 파일을 unlink 한 직후 같은 image_path 의 asset_candidates 행을 삭제해
    # dangling DB 참조 (file 없는데 row 남아 이미지 fetch 가 FS 에러내는 케이스)
    # 를 차단한다.
    bypass_resolved_to_raw: dict[str, str] = {}
    for raw in bypass_paths:
        try:
            bypass_resolved_to_raw[str(Path(raw).resolve())] = raw
        except OSError:
            continue
    deleted_bypass_paths: list[str] = []
    if bypass_max_age_seconds > 0:
        for path, mtime, size in files:
            try:
                resolved_str = str(path.resolve())
            except OSError:
                continue
            if resolved_str not in bypass_resolved_to_raw:
                continue
            if now - mtime > bypass_max_age_seconds:
                try:
                    path.unlink()
                    deleted_files += 1
                    freed_bytes += size
                    deleted_bypass_paths.append(bypass_resolved_to_raw[resolved_str])
                except OSError:
                    pass
    if deleted_bypass_paths:
        _delete_candidate_rows(data_root, deleted_bypass_paths)
    # bypass_resolved 별칭 — 이후 단계 ((3) 일반 retention) 에서도 bypass 패스
    # 는 건너뛰게 유지.
    bypass_resolved = set(bypass_resolved_to_raw.keys())

    # 3) 일반 (non-bypass) 후보 — CANDIDATE_GC_MAX_AGE_DAYS 적용
    if max_age_seconds > 0:
        for path, mtime, size in files:
            try:
                resolved_str = str(path.resolve())
            except OSError:
                continue
            if resolved_str in bypass_resolved:
                continue  # 이미 bypass 정책으로 처리됨
            if now - mtime > max_age_seconds:
                try:
                    path.unlink()
                    deleted_files += 1
                    freed_bytes += size
                except OSError:
                    pass

    # 3) 남은 파일이 용량 상한 초과 시 오래된 것부터 삭제
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
