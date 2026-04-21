# SSE Event Types

Single source of truth lives in `lib/events.py`. Any new event type added to the
backend **must** be declared there and documented in this table before being
emitted, otherwise frontend screens using `useSSE` will silently drop it.

All events are JSON objects with `type` plus event-specific fields. Payloads are
small — they're signals, not full state. Clients refetch the canonical API
on receipt.

| `type` | Emitted by | Key fields | Consumers |
| --- | --- | --- | --- |
| `job_created` | `POST /api/generate` | `job_id` | Dashboard |
| `batch_job_created` | `POST /api/generate/batch` (legacy) | `job_id`, `task_count` | Dashboard |
| `design_batch_created` | `POST /api/batches` | `batch_id`, `job_id`, `asset_key` | Dashboard / Queue / Batches |
| `task_done` | worker on success | `job_id`, `asset_key`, `validation_status` | Dashboard / Queue / BatchDetail |
| `task_error` | worker on failure | `job_id`, `asset_key`, `error`, `retry`, `next_attempt_at` | Dashboard / Errors |
| `candidate_added` | worker after inserting candidate row (design batch) | `batch_id`, `candidate_id`, `slot_index` | CherryPick / BatchDetail |
| `candidate_rejected` | `POST …/candidates/{id}/reject` | `batch_id`, `candidate_id` | CherryPick |
| `candidate_unrejected` | `POST …/candidates/{id}/unreject` | `batch_id`, `candidate_id` | CherryPick |
| `validation_updated` | revalidate flows | `asset_id` or `candidate_id`, `validation_status` | CherryPick / BatchDetail |
| `batch_retry_failed` | `POST …/retry-failed` | `batch_id`, `retried_count` | BatchDetail |
| `batch_revalidate_failed_done` | `POST /api/batch/revalidate-failed` | `count`, `project` | Batches |
| `batch_regenerate_failed_queued` | `POST /api/batch/regenerate-failed` | `jobs`, `project` | Batches / BatchDetail |
| `asset_approved_from_candidate` | approve-from-candidate | `asset_id` | Assets |
| `asset_approve_undone` | undo-approve | `asset_id` | Assets |
| `asset_candidate_selected` | select-candidate | `asset_id`, `job_id`, `slot_index` | AssetDetail |
| `asset_status_changed` | status PATCH | `asset_id`, `status` | Assets |
| `asset_regenerate_queued` | regenerate | `asset_id`, `job_id` | Assets |
| `asset_history_restored` | `POST /api/assets/{id}/restore-history` | `asset_id`, `version`, `new_version` | AssetDetail |
| `scan_completed` | scan flow | `project`, `count`, `root_path` | Scan screen |
| `export_completed` | export flow | `count`, `output_dir`, `manifest_path` | Export |
| `sd_health_changed` | periodic SD health sampling | `healthy: bool` | Persistent banner (Phase B.3) |

## Conventions

- `type` values are **lower_snake_case** past tense verbs or `subject_verb`.
- Include the minimum ID set needed for a client to fetch the canonical resource.
- Never include secrets, raw prompts (too large), or file system paths.
- Clients MUST handle unknown `type` values gracefully (ignore) so adding new events is non-breaking.
