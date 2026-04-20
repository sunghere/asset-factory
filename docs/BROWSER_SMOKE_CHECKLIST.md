# Browser smoke checklist (post-seed)

Manual pass after `scripts/seed_cat_raising.py` and running the FastAPI app.
Use Chromium DevTools: no console errors, `/api/events` stays connected on SPA routes.

## Prep

1. `.venv/bin/python scripts/seed_cat_raising.py`
2. Start server (project default, e.g. `uv run uvicorn server:app --reload`)
3. Open `http://127.0.0.1:8000/app/` (or your host)

## Routes (order)

| Step | Path | Check |
|------|------|--------|
| 1 | `/app/` | Dashboard loads; queue/summary cards; no red ErrorPanel |
| 2 | `/app/queue` | Rows for seeded batches; Enter or link opens cherry-pick |
| 3 | `/app/cherry-pick/<seed_batch_id>` | Grid + side panel; pick/reject; compare `v`/`c` if enabled |
| 4 | `/app/assets` | Filters; grid; hash query updates when filters change |
| 5 | `/app/assets/<id>` | Detail tabs; SSE: approve elsewhere or worker touch if available |
| 6 | `/app/batches` | List filters |
| 7 | `/app/batches/<id>` | Tasks / Candidates / Spec; retry-failed when failed tasks exist |
| 8 | `/app/batches/new` | Wizard through step 3 |
| 9 | `/app/catalog` | Models/LoRAs tabs |
| 10 | `/app/export` | Manifest preview; run export (needs API key if enforced) |
| 11 | `/app/system` | Health, GC, DB, worker, logs blocks refresh |
| 12 | `/app/settings` | API key field; toggles persist |

## Redirects

- `GET /` → 302 → `/app/`
- `GET /cherry-pick?batch_id=<valid>` → `/app/cherry-pick/<id>`
- Invalid `batch_id` → `/app/queue`

## Optional

- With `API_KEY` set: confirm 401 on write without `x-api-key`, then success with header.
