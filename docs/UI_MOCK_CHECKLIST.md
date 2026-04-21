# UI Mock Checklist

Use this checklist with the isolated mock environment started by:

```bash
scripts/run_ui_mock_env.sh
```

## Global Safety Checks

- Mock server startup log contains `MOCK MODE ACTIVE`.
- `GET /api/system/db` shows path under `data/mock-ui`.
- Existing non-mock DB file remains unchanged.
- Export target is under `data/mock-ui/export`.

## Route-by-Route Verification

- `/app/`
  - KPI cards render with non-zero counts.
  - Recent activity list is not empty.

- `/app/queue`
  - Mixed task states exist: queued, processing, done, failed.
  - Filter controls produce empty-after-filter edge state.

- `/app/cherry-pick`
  - At least one batch has pass/fail/rejected candidates together.
  - Candidate reject/unreject actions are visible.
  - Next-batch flow is available after selecting a candidate.

- `/app/assets`
  - Multiple projects/categories/status combinations exist.
  - Relative time and seed metadata differ across cards.
  - Multi-filter combinations reduce and restore result set.

- `/app/assets/:asset_id`
  - History carousel has 3+ versions.
  - Candidate tab has mixed validation states.
  - Prompt and metadata panels expand/collapse correctly.

- `/app/batches`
  - Rows include queued/processing/done/failed mixes.
  - Progress bar segments reflect done/active/failed counts.

- `/app/batches/:batch_id`
  - Tasks tab shows retry/error fields for failed rows.
  - Spec tab includes model/seed/lora axes values.
  - Candidate list contains rejected and non-rejected rows.

- `/app/batches/new`
  - Form previews and validation messages are visible.
  - Enqueue action can be tested without touching real data.

- `/app/catalog`
  - Model and LoRA usage counters are populated.
  - Detail panel shows compatible models/sample prompt metadata.

- `/app/export`
  - Approved assets appear in preview.
  - Export action writes to mock export root only.
  - Post-export summary includes output directory and manifest.

- `/app/system`
  - DB/worker/log cards show non-empty diagnostics.
  - GC status and run metadata are visible.

- `/app/settings`
  - Autosave indicator transitions idle -> saving -> saved.
  - API key save flow can be exercised safely in mock mode.

## Edge-State Assertions

- Failed task rows exist and include `last_error`.
- Rejected candidate rows exist.
- At least one batch remains active (queued or processing).
- At least one asset has `validation_status=fail`.
- Empty list state can be produced via strict filters.

## Re-seeding Procedure

When data drifts during UI QA:

```bash
ASSET_FACTORY_DB_PATH=./data/mock-ui/asset-factory.db \
ASSET_FACTORY_DATA_DIR=./data/mock-ui \
.venv/bin/python scripts/seed_ui_mock_all_screens.py
```
# UI Mock Checklist

Isolated mock environment smoke checks for `/app/*`.

## Start

```bash
./scripts/run_ui_mock_env.sh
```

Expected:
- Startup log contains `[MOCK MODE]`.
- `DATA_DIR` points to `data/mock-ui` (or custom mock override).
- Existing default DB at `data/asset-factory.db` is untouched.

## Screen Assertions

- `/app/dashboard`
  - Queue/asset/job cards are non-zero.
  - Recent activity panel is non-empty.
- `/app/queue`
  - Mixed statuses visible (`queued`, `processing`, `done`, `failed`).
  - Filters can produce non-empty and empty result states.
- `/app/cherry-pick/<batch_id>`
  - Batch `btc_uimock_char` has pass/fail/pending + rejected candidates.
  - At least one candidate already marked picked.
- `/app/assets`
  - Multiple categories and statuses (`approved`, `pending`, `rejected`).
  - Updated timestamps vary enough to test sorting.
- `/app/assets/<id>`
  - History shows at least two previous versions.
  - Candidate slots + validation badges render.
- `/app/batches` and `/app/batches/<id>`
  - Batches include active and completed-with-errors states.
  - Batch detail task table includes failed row with `last_error`.
- `/app/catalog`
  - Catalog usage reads non-empty model usage data.
- `/app/export`
  - Approved assets available for export preview.
  - Mock export root path is shown and writable.
- `/app/system`
  - DB path points to mock DB.
  - Logs/worker cards show non-empty runtime values.
- `/app/settings`
  - Settings API persists values without touching non-mock DB.

## Regression Guard

- Stop the mock server.
- Start normal server (`.venv/bin/uvicorn server:app --reload`) without mock env vars.
- Verify `/api/system/db.path` points back to `data/asset-factory.db`.
