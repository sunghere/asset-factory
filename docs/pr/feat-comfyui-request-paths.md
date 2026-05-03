# feat: ComfyUI workflow request paths (batch + manual + ops)

> Branch: `feat/comfyui-request-paths` → `main`
> 선행: `feat/comfyui-catalog-system` (PR #46) — catalog/system 화면을 ComfyUI 로 옮긴 PR

## 요약

PR #46 이 **읽기** 경로 (catalog · system · dashboard) 를 ComfyUI 로 옮긴 후, 본
PR 에서 **쓰기** 경로 (배치 enqueue · 단일 generate · cancel/delete) 도 ComfyUI
워크플로우 기반으로 재작성. A1111 backend 잔재는 deprecation 헤더 + 410 Gone 으로
정리. UX 측면 follow-up 4건 (manual → /batches 가시성, queue 진행도 역방향 버그,
catalog 카드 다중 active, batch cancel/delete 부재) 까지 본 PR 에 포함.

## 동기

`feat/comfyui-catalog-system` 머지 후에도 다음 두 갭이 남아 있었다:
1. `/api/batches` 호출이 `models × loras × seeds` (A1111 매트릭스) 로만 expand 가능
   — backend 컬럼이 비어 DB DEFAULT `'a1111'` 로 떨어지고, A1111 서버가 deprecated
   인 운영 환경에서 task 가 처리되지 않음.
2. 단일 generate UI 자체가 부재 — `/api/workflows/generate` 는 백엔드에 있지만
   frontend 진입점이 없었음.

본 PR 은 두 갭을 동시에 해소하고, 출시 후 사용 중 발견된 4가지 부수 이슈도 함께
처리한다.

## 변경 사항

### 1. 백엔드 — 워크플로우 곱집합 + 410 Gone (`f2114cf`)

- `DesignBatchRequest` 재작성: `prompts × workflow_variants ×
  workflow_params_overrides × seeds` 매트릭스. `backend='comfyui'` 명시.
- `BatchCommonParams` 의 `steps/cfg/sampler` 를 `int|None` (variant default
  override). `width/height` 제거 (workflow_params 슬롯).
- `seeds_per_combo` 상한 64 → 256 (variants/overrides 가 줄어 seeds 가 주된
  다양성 축이 됨).
- 신규 `_make_workflow_task(...)` helper — `expand_design_batch` 와
  `workflows_generate` 가 task dict 모양 공유 (DRY + 회귀 가드).
- `_enqueue_design_batch` disk guard 가 `max(variant.outputs) × N` 상향.
- `workflow_registry.variant()` 사전 검증으로 미등록/사용불가 variant 는 부분
  enqueue 없이 400 으로 거부.
- `POST /api/generate`, `POST /api/generate/batch` → **410 Gone** +
  `Deprecation: true` + `Sunset: <today+90d>` + `Link: <successor>;
  rel="successor-version"` 헤더 + 모듈 1회 `logger.warning`.
  - successor: `/api/workflows/generate` · `/api/batches`.
- 죽은 코드 제거: `_format_lora_suffix`, `_extract_tasks_from_spec`, `LoraSpec`.
- `models.list_recent_batches` 가 `workflow_category`, `backend`, distinct
  `workflow_variants[]` 를 응답에 첨부.

### 2. 프론트엔드 — workflow-based BatchNew + 신규 Manual (`8e54d52`)

- `static/app/js/api.jsx`: `workflowsGenerate(spec)` 메서드 추가.
- `screens/BatchNew.jsx` 재작성 (3-step 마법사 유지, step 2 만 워크플로우 기반):
  workflow_category 단일 드롭다운 + variants 멀티셀렉트 + overrides JSON
  텍스트 리스트. `prompts × variants × overrides × seeds` 곱집합 카운트 라이브.
  prefill `?workflow_category & ?workflow_variant`.
- `screens/Manual.jsx` 신규 (`/manual` 라우트): workflow 단일/N 호출용 화면.
  `POST /api/workflows/generate` 호출 후 job 상태 폴링, 완료 시 cherry-pick
  진입 버튼. candidates_total > 1 이면 cherry-pick 슬롯 자동 생성.
- `screens/Batches.jsx`: workflow 컬럼 신설 (category pill + variants 라벨,
  2개 초과 시 `+N`).
- `chrome.jsx` + `app.jsx` + `manifest.json`: `/manual` 라우트 등록 + 사이드바
  항목 추가 (`assets ↔ batches` 사이).

### 3. 도그푸드 — 워크플로우 페이로드로 변환 (`c6b1b36`)

`scripts/dogfood_marine_v2.sh`, `scripts/dogfood_morning_session.sh` 의 inline
JSON 을 `workflow_category=sprite, workflow_variants=[pixel_alpha],
workflow_params_overrides=[{}]` 로 갱신. 100장 케이스는 `seeds_per_combo=100`
단일 combo 로 등가 (이전: `2 model × 2 LoRA × 25 seeds`).

### 4. 핸드오프 문서 (`a70cef7`)

`NEXT.md` 갱신 — 본 PR 의 작업 요약, smoke 가이드, A1111 v0.4.0 메이저 제거
후속 항목 정리.

### 5. fix: manual 요청을 /batches 에 노출 + project dropdown (`decc654`)

증상: 사용자가 manual 화면에서 요청을 보낸 뒤 `/app/batches` 에서 결과를 못 찾고,
project 필터링 시에도 안 나옴.

원인 1: `workflows_generate` 가 `batch_id` 를 만들지 않아
`list_recent_batches` 의 `WHERE batch_id IS NOT NULL` 필터에 걸러짐.
→ 모든 워크플로우 호출에 `btc_<uuid16>` 부여, 모든 task 에 첨부, response 에
포함, SSE `EVT_DESIGN_BATCH_CREATED` 도 함께 발행. `handle_task` line 904 의
기존 분기 (`batch_id != None & candidate_slot None → slot=task.id 자동`) 가
자연스럽게 candidate path 로 인도하여 `candidates_total=1` 케이스도
cherry-pick 에서 검토 가능.

원인 2: `Manual.jsx` 의 `project` state default 가 `'default-project'` 인데
실제 dropdown 옵션은 다른 프로젝트 ID 들이라, 사용자가 dropdown 을 직접 클릭
하지 않으면 silent default 로 submit. → projects 도착 시 state 가 옵션에 없으면
첫 항목으로 보정 (Manual + BatchNew 양쪽).

### 6. fix: /queue progress bar 역방향 + 남음 라벨 (`c08d931`)

증상: progress bar 가 `32/32` 에서 시작해 `31/32, 30/32` 식으로 거꾸로 진행.
`btc_…  남음 3` chip 도 task 잔량처럼 읽히지만 실제로는 candidate 검토 큐
크기였음.

원인: `Queue.jsx` 가 두 도메인의 값을 섞어 계산.
- `b.total` 은 task 수 (`generation_tasks GROUP BY batch_id`)
- `b.remaining` 은 candidate 검토 잔여 (`count_pending_candidates:
  candidate_total - rejected`)

이 둘을 `done = b.total - b.remaining` 로 빼면 의미 없는 숫자. candidate 1장
만들어질 때마다 `b.remaining` 0→1 → `done` 32→31 (역방향).

수정 — 도메인 분리:
- 진행 chip: `taskDone/taskTotal` — 0→N 정방향
- progress bar: `SegProgress(approved=taskDone, rejected=taskFailed,
  total=taskTotal)`. failed 빨간 segment.
- 처리중/실패 chip: `taskActive`/`taskFailed` (0 일 때 숨김)
- 검토 chip: `reviewRemaining` (cherry-pick 검토 큐, 라벨 '남음' → '검토')

### 7. fix: catalog 카드 동일 섹션 다중 active (`0435bd9`)

증상: `/app/catalog` 에서 워크플로우 1개 클릭하면 같은 섹션의 모든 워크플로우
카드가 동시에 하이라이트.

원인: `CatalogSection` 의 active 판정이
`selected.item.id === it.id || selected.item.name === it.name`. workflow row 는
`id` 만 있고 `name` 이 `undefined`. OR 의 두 번째 항이 `undefined === undefined ===
true` 로 매칭되어 동일 섹션 전체가 active.

수정: id 우선 (없으면 name) 한 키만 골라 비교, `itKey != null` 가드로 undefined
양변 매칭 차단. CatalogCard `key` prop 도 같은 itKey 로 통일.

### 8. feat: batch cancel + delete (`0d37083`)

요청: 테스트로 만든 cherry-pick batch 를 정리할 방법이 없음. 진행중 task 취소도
부재.

backend:
- `lib/events.py`: `EVT_BATCH_CANCELLED`, `EVT_BATCH_DELETED` 추가.
- `Database.cancel_batch(batch_id)` — queued/processing → cancelled,
  `last_error='cancelled by user'`. 이미 done/failed 인 task 는 history 보존.
- `Database.delete_batch(batch_id)` — `asset_candidates` + `generation_tasks`
  전부 DELETE. 파일 unlink 는 호출자 책임 (FS/DB 정합성을 위해 DB 먼저
  정리하고 image_paths 반환).
- `POST /api/batches/{id}/cancel` — 404 가드 + cancel_batch + 이벤트 발행.
- `DELETE /api/batches/{id}?force=bool` — active 태스크 남으면 409 Conflict
  (`code=batch_has_active_tasks`). force=true 로 우회 가능. delete_batch 호출
  후 image_paths 를 best-effort unlink, 실패는 카운트.

frontend:
- `api.jsx`: `cancelBatch` / `deleteBatch({force})`.
- `CherryPick.jsx`: '취소' / '삭제' 버튼 + 2-step 확인 다이얼로그
  (`BatchOps` 컴포넌트). 삭제 시 active>0 이면 409 받아서 force 재시도 prompt.

**한계 (의도적 trade-off)**: ComfyUI prompt 의 즉시 interrupt 는 별도 API
(`/interrupt`) 필요해 본 PR scope 밖. cancelled 마킹 후 ~10초 동안 워커가
candidate 1~2장 더 저장할 수 있음. 사용자가 그 시점 보통 `/queue` 로 이미
이동했을 확률이 높아 가시성 영향 미미.

## 검증

```bash
pytest --ignore=tests/test_generator_comfyui.py -q
  → 433 passed
```

`tests/test_generator_comfyui.py` 는 `aioresponses` 미설치 환경 이슈 (선행 PR
부터 누적, 본 PR 무관).

### Smoke (curl)

```bash
# 410 Gone + Deprecation 헤더
curl -X POST localhost:8000/api/generate -d '{}' -i | head -1     # 410 Gone
curl -I -X POST localhost:8000/api/generate -d '{}'              # Deprecation/Sunset/Link

# Manual 워크플로우 단일 (ComfyUI 떠있어야 함)
curl -X POST localhost:8000/api/workflows/generate \
  -H "Content-Type: application/json" \
  -d '{"project":"smoke","asset_key":"test_sprite",
       "workflow_category":"sprite","workflow_variant":"pixel_alpha",
       "subject":"silver hair magical girl in school uniform"}' | jq .
# → batch_id 응답 + GET /api/batches 에 즉시 노출 ✓

# Batch 워크플로우 (3장)
curl -X POST localhost:8000/api/batches \
  -H "Content-Type: application/json" \
  -d '{"asset_key":"smoke_batch","project":"smoke",
       "workflow_category":"sprite","workflow_variants":["pixel_alpha"],
       "prompts":["silver hair magical girl in school uniform"],
       "prompt_mode":"legacy","seeds":[1,2,3]}'

# Cancel + Delete
curl -X POST localhost:8000/api/batches/btc_xxx/cancel
curl -X DELETE localhost:8000/api/batches/btc_xxx     # 409 if active
curl -X DELETE localhost:8000/api/batches/btc_xxx?force=true
```

### 수기 UI smoke

- `/app/manual` — workflow 선택 → 1장 생성 → 결과 카드의 "batch 상세" → `/app/batches` 에서 새 row 표시
- `/app/batches/new` — variants 멀티 + overrides JSON `{}` 추가 → 곱집합 카운트 라이브
- `/app/batches` — `workflow` 컬럼에 카테고리 pill + variants 라벨
- `/app/queue` — 진행 0→N 정방향, 검토 chip 별도 표시
- `/app/catalog` — workflow 카드 1개 클릭 시 그 카드만 하이라이트
- `/app/cherry-pick/btc_*` — 우상단 "취소" / "삭제" 버튼

## 의도적 scope 밖

- A1111 backend 코드 (`sd_backend.py`, `generator.py`, `BackendRegistry` 의
  a1111 entry, `/api/sd/catalog/*` read 엔드포인트) 의 물리적 제거 —
  CLAUDE.md 약속대로 **v0.4.0 메이저** 에서 별도 PR.
- BatchNew.jsx / Manual.jsx 의 단위 테스트 인프라 — 프로젝트에 jsx 테스트
  러너 자체가 없음. 별도 인프라 PR.
- ComfyUI prompt 즉시 interrupt — 별도 API (`/interrupt`) 연동 필요.

## 후속 (v0.4.0 별도 PR)

- A1111 backend 물리적 제거.
- `sincerity-skills/asset-factory-api/SKILL.md` 갱신 — `/api/workflows/generate`,
  `/api/batches` (DesignBatchRequest 새 schema) 사용 예시 추가, `/api/generate*`
  410 Gone 명시. (별도 repo PR)

## 커밋 스택

```
0d37083 feat(batch): cancel + delete endpoints + cherry-pick UI 버튼
0435bd9 fix(catalog): workflow card 선택 시 같은 섹션 전체가 active 로 표시되는 버그
c08d931 fix(queue): progress bar reverses + 남음 mislabeled (mixed domains)
decc654 fix(workflows): expose manual requests in /batches + project dropdown
a70cef7 docs(NEXT): handoff for feat/comfyui-request-paths
c6b1b36 chore(dogfood): switch shell scripts to ComfyUI workflow schema
8e54d52 feat(frontend): workflow-based BatchNew + new Manual screen
f2114cf feat(backend): ComfyUI workflow batch enqueue + A1111 generate 410 Gone
```
