# feat: project entity management — registry, auto-create, two-stage delete

> Branch: `feat/project-entity-management` → `main`
> Design doc: `~/.gstack/projects/sunghere-asset-factory/yoons-main-design-20260503-171102.md`
> Decision: Approach A (lightweight registry + auto-create + 2-stage delete)

## 증상

`project` 가 `assets / generation_tasks / asset_candidates` 의
free-form `TEXT NOT NULL` 컬럼으로만 존재했다 (`models.py:79-101, 110, 244`).
별도 `projects` 테이블이 없고 어디서도 검증되지 않아:

1. 프론트엔드에 "프로젝트 만들기" 진입점이 없음 — picker 가 `'default-project'`
   하드코딩 (`BatchNew.jsx`, `Manual.jsx`).
2. 오타 (`my-prject`), 대소문자 변종 (`MyGame` vs `mygame`), 잘못된 슬러그
   (`my project!`) 모두 그대로 INSERT.
3. project 단위 archive / cascade delete 가 부재 — disk 누적 / DB 누적 정리
   불가.
4. `/api/projects` 가 spec 디렉토리 파일만 반환 (`server.py:2303~2328`) — DB
   의 row 와 무관.

## 원인

작업 단위 (batch/asset) 와 그 작업의 컨텍스트 (project) 가 분리 안 됨.
컨텍스트가 first-class 가 아니라 라벨 스티커처럼 부유하니 lifecycle (생성→사용
→archive→purge), discovery (UI 진입점, picker), bulk 작업 (project 단위
cascade) 이 표현 자체 불가능.

## 수정

11 commit, `--merge` 머지 모드 (의미 단위 atomic — git blame 친화).

| # | Commit | 핵심 변경 |
|---|--------|--------|
| 1 | `feat(db): projects table + migration with quarantine` | 스키마 + DISTINCT 백필 + invalid quarantine + default-project seed |
| 2 | `feat(api): /api/projects CRUD endpoints (DB-backed)` | GET 목록 / POST 등록 / GET 상세 / PATCH (counts derive) |
| 3 | `feat(api): ensure_project_writable Depends + slug validation` | 6개 write 진입점에 helper 일관 적용 |
| 4 | `feat(api): worker-level archive guard` | candidate insert path 에 archived/purging 가드 |
| 5 | `feat(api): archive/unarchive endpoints` | soft drain (in-flight 자연 종료) |
| 6 | `feat(api): purge dry-run + confirm + tombstone + retry` | 2-step 흐름 + 사전조건 + best-effort disk + retry |
| 7 | `feat(frontend): /projects screen + sidebar` | 신규 화면 + 모달들 (New / Archive undo / Purge type-to-confirm) |
| 8 | `feat(frontend): project picker in BatchNew/Manual + localStorage` | lastProjectSlug + "+ New" chip |
| 9 | `chore(frontend): Assets/Export 응답 schema 정리` | 새 `{slug, display_name}` shape 직접 사용 |
| 10 | `test(e2e): playwright infra + 4 critical paths` | 인프라 + critical user flow + slug validation 4 케이스 |
| 11 | `docs(pr): feat-project-entity-management.md` | 본 문서 |

### 데이터 모델

```sql
CREATE TABLE projects (
  slug         TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description  TEXT,
  created_at   TEXT NOT NULL,
  archived_at  TEXT,
  purge_status TEXT  -- NULL | 'purging'
);
```

부속 인덱스: `idx_projects_archived`, `idx_projects_purge_status` (partial),
`idx_tasks_project`, `idx_tasks_project_status`, `idx_candidates_project`.
**FK 제약은 안 검** (Approach A — 외부 dogfood / SDK 호환). 별도 후속 PR 검토.

### Slug 검증

- 정규식 `^[a-z][a-z0-9-]{1,39}$` (2-40자, 소문자 시작, 영숫자+하이픈만).
- Reserved: `default / system / admin / api / null / undefined / _ /
  __pycache__`. **`default-project` 는 마이그레이션 호환 위해 예외**.
- 위반 시 400 `{"error": "invalid_project_slug", "suggestion": "..."}` —
  suggestion = lowercase + 비허용 문자 → `-`.

### 자동 생성 (A2)

`ensure_project_writable(slug)` helper 가 모든 write 진입점에서 호출:

```
invalid slug              → 400 (suggestion)
미등록 + valid            → INSERT OR IGNORE (auto-create) → 진행
등록됨 + archived_at NULL → 진행
archived                  → 409 project_archived
purging                   → 410 project_being_purged
```

적용 endpoint: `/api/batches`, `/api/mcp/design_asset`,
`/api/workflows/generate`, `/api/projects/scan`,
`/api/assets/approve-from-candidate`, `/api/assets/{id}/regenerate`,
`/api/batch/regenerate-failed` (project param 있을 때만). worker 의 candidate
insert 는 `models.py:insert_asset_candidate` 함수 레벨 가드 — archived 면
`WORKER_ARCHIVED_SKIP` log + `return 0` (drain 일관성).

### Archive / Purge

**Archive (soft drain):** `archived_at` 세팅 → 새 enqueue / generate / scan /
approve / regen 모두 409. 진행 중 task 는 자연 종료. read 정상.

**Purge:**
- `?dry_run=true` — counts (assets/tasks/candidates/files/bytes) +
  `blocking.active_task_count` (>0 일 때).
- `?confirm=true` — 사전조건 (archived AND `active_task_count==0` AND
  `purge_status IS NULL`) → tombstone → DB cascade DELETE 단일 transaction →
  `shutil.rmtree` best-effort (실패 시 `files_failed=true` + `purging` 유지)
  → `DELETE FROM projects` (별도 transaction).
- `/purge/retry` — `purge_status='purging'` 만 대상, 디스크만 재시도.
- `purge_target_dirs(slug)` 단일 source: `DATA_DIR/approved/{slug}`,
  `DATA_DIR/candidates/{slug}`. 새 디렉토리 추가 시 여기에만 등록.

### UI

- `/projects` 화면 — 단일 컬럼 row 리스트 + ⋯ 메뉴 (archive / unarchive /
  purge / retry-purge). active task chip 라이브.
- "+ New Project" 모달 (Projects.jsx) — `window.NewProjectModal` 로 export
  하여 BatchNew/Manual 의 "+ New" chip 도 같은 모달 재사용.
- Purge 모달 — type-to-confirm input (slug 정확히 일치 시에만 destructive
  버튼 enable, GitHub/AWS 표준 패턴) + active task 라이브 폴링 5s.
- BatchNew/Manual picker — `localStorage.lastProjectSlug` 자동 선택, archived
  자동 제외, "+ New" chip 으로 inline 등록 → 자동 picker 선택.

## AGENTS.md 적용

- **§1 (로컬 검증):** push 전 `.venv/bin/python -m ruff check .` +
  `.venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q`
  443 → 487 (+49 신규 테스트, 모두 baseline 통과).
- **§3 (PR body 컨벤션):** 본 파일 — 증상 / 원인 / 수정 / 검증 / scope 밖
  구조. `gh pr create --body-file` 로 첨부.
- **§5 (워크트리):** `git worktree add ../asset-factory-projects -b
  feat/project-entity-management main`.
- **§6 (FE↔BE 계약):** `_project_to_response` 가 응답 키들을 명시 derive.
  `test_projects_api_response_shape` 가 키 셋 hard assert.
- **§7 (import scope):** 신규 컴포넌트들이 모두 raw React hook 사용 — 같은
  파일에 alias 패턴 없음 (확인 후). `window.NewProjectModal` 도 export 후 다
  른 화면에서 `window.` 경유로만 사용해 alias 충돌 회피.
- **§8 (file↔DB 정합성):** purge 흐름은 DB cascade 단일 transaction → file
  unlink best-effort 순. 응답에 file path 노출 안 함.

## 검증

### 로컬

```
$ .venv/bin/python -m ruff check .
All checks passed!

$ .venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q
487 passed in 5.84s
```

기존 438 테스트 + 신규 49 (`test_projects_migration` 11 +
`test_projects_crud` 11 + `test_projects_validation` 7 +
`test_worker_respects_archived` 4 + `test_project_lifecycle` 7 +
`test_project_purge` 9). 추가로 e2e (Playwright) 4 critical paths + 4
slug validation 케이스는 CI 의 e2e job 에서 실행.

### 마이그레이션 호환

- 운영 DB 의 free-form project 값들은 `_backfill_projects_registry` 가
  DISTINCT 추출 → valid 만 INSERT, invalid 는 `PROJECT_QUARANTINE` log +
  read-only 보존.
- `default-project` 는 빈 DB 에서도 항상 seed → 외부 dogfood 스크립트 호환.
- ComfyUI dogfood 스크립트 변경 없음.

## Scope 밖 / 후속

- **FK 강제 (Approach B)** — 별도 마이그레이션 PR.
- **Per-project ACL / 권한** — 현재 사용자 시스템 부재.
- **Project rename (slug 변경)** — 모든 row update 필요, 복잡도 큼. v0.4
  검토.
- **Spec-file → DB project 동기화 도구** — spec 시스템 자체 deprecation
  (workflow 로 이전 중) 이라 노력 보류.
- **`sincerity-skills/asset-factory-api/SKILL.md`** — `/api/projects` 응답
  schema + 신규 8 endpoint 명세 업데이트는 별도 skill repo PR (본 PR 머지
  직후 push).
- **Archive 90일 → auto purge** — 본 PR 은 manual flow 만. cron 확장은 옵션.

## Decision Notes

- Strict (FK + 400) 보다 Lenient (auto-create + suggestion) 채택 — 사용자/
  팀 friction 우선시. 단, 삭제는 2-stage (archive + purge) 로 무게중심 보존.
- Outside Voice (Codex) 가 제안한 PR split (각 commit 별 PR) 은 거절. 본
  feature 의 의미 단위가 강하게 결합되어 있어 single PR + multi-commit 이
  bisect 친화 + 리뷰어 부담 둘 다 더 낫다 (T9). 대신 commit 메시지를 회고
  가능한 형태로 정밀화.
