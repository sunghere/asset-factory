"""Asset Factory 이벤트 타입 단일 선언.

모든 SSE/로그 이벤트는 여기 정의된 리터럴을 쓴다. 새로운 이벤트를 추가할 때
반드시 ``EventType`` 에 명시하고 ``docs/EVENTS.md`` 를 업데이트한다.

타입 체커만 활용하면 되므로 런타임 validator 는 두지 않는다 (모든 페이로드는
여전히 ``dict[str, Any]``). 이 파일의 가치는 "emit/subscribe 가 합의한 키"
라는 문서/정적 분석 지점에 있다.
"""

from __future__ import annotations

from typing import Literal

# --- Job / task lifecycle ----------------------------------------------------
EVT_JOB_CREATED = "job_created"  # /api/generate
EVT_BATCH_JOB_CREATED = "batch_job_created"  # /api/generate/batch (legacy)
EVT_DESIGN_BATCH_CREATED = "design_batch_created"  # /api/batches (v0.2 스펙)
EVT_TASK_DONE = "task_done"
EVT_TASK_ERROR = "task_error"

# --- Candidates (cherry-pick) -----------------------------------------------
EVT_CANDIDATE_ADDED = "candidate_added"
EVT_CANDIDATE_REJECTED = "candidate_rejected"
EVT_CANDIDATE_UNREJECTED = "candidate_unrejected"
EVT_VALIDATION_UPDATED = "validation_updated"  # 후보 재검증 결과 반영 (TBD)

# --- Batch ops ---------------------------------------------------------------
EVT_BATCH_RETRY_FAILED = "batch_retry_failed"
EVT_BATCH_REVALIDATE_FAILED_DONE = "batch_revalidate_failed_done"
EVT_BATCH_REGENERATE_FAILED_QUEUED = "batch_regenerate_failed_queued"
EVT_BATCH_CANCELLED = "batch_cancelled"
EVT_BATCH_DELETED = "batch_deleted"

# --- Asset lifecycle ---------------------------------------------------------
EVT_ASSET_APPROVED_FROM_CANDIDATE = "asset_approved_from_candidate"
EVT_ASSET_APPROVE_UNDONE = "asset_approve_undone"
EVT_ASSET_CANDIDATE_SELECTED = "asset_candidate_selected"
EVT_ASSET_STATUS_CHANGED = "asset_status_changed"
EVT_ASSET_REGENERATE_QUEUED = "asset_regenerate_queued"
EVT_ASSET_HISTORY_RESTORED = "asset_history_restored"  # AssetDetail history → restore

# --- Ops / misc --------------------------------------------------------------
EVT_SCAN_COMPLETED = "scan_completed"
EVT_EXPORT_COMPLETED = "export_completed"
EVT_SD_HEALTH_CHANGED = "sd_health_changed"  # 배너용 (TBD)


EventType = Literal[
    "job_created",
    "batch_job_created",
    "design_batch_created",
    "task_done",
    "task_error",
    "candidate_added",
    "candidate_rejected",
    "candidate_unrejected",
    "validation_updated",
    "batch_retry_failed",
    "batch_revalidate_failed_done",
    "batch_regenerate_failed_queued",
    "batch_cancelled",
    "batch_deleted",
    "asset_approved_from_candidate",
    "asset_approve_undone",
    "asset_candidate_selected",
    "asset_status_changed",
    "asset_regenerate_queued",
    "asset_history_restored",
    "scan_completed",
    "export_completed",
    "sd_health_changed",
]


ALL_EVENT_TYPES: tuple[str, ...] = (
    EVT_JOB_CREATED,
    EVT_BATCH_JOB_CREATED,
    EVT_DESIGN_BATCH_CREATED,
    EVT_TASK_DONE,
    EVT_TASK_ERROR,
    EVT_CANDIDATE_ADDED,
    EVT_CANDIDATE_REJECTED,
    EVT_CANDIDATE_UNREJECTED,
    EVT_VALIDATION_UPDATED,
    EVT_BATCH_RETRY_FAILED,
    EVT_BATCH_REVALIDATE_FAILED_DONE,
    EVT_BATCH_REGENERATE_FAILED_QUEUED,
    EVT_BATCH_CANCELLED,
    EVT_BATCH_DELETED,
    EVT_ASSET_APPROVED_FROM_CANDIDATE,
    EVT_ASSET_APPROVE_UNDONE,
    EVT_ASSET_CANDIDATE_SELECTED,
    EVT_ASSET_STATUS_CHANGED,
    EVT_ASSET_REGENERATE_QUEUED,
    EVT_ASSET_HISTORY_RESTORED,
    EVT_SCAN_COMPLETED,
    EVT_EXPORT_COMPLETED,
    EVT_SD_HEALTH_CHANGED,
)
