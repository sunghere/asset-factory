# NEXT — 세션 핸드오프

> Transient 문서. 작성/삭제 규칙은 [`CLAUDE.md`](./CLAUDE.md#세션-핸드오프-nextmd) 참조.

_작성: 2026-05-03 · 세션 종료 시점_

---

## 최근작업

### `feat/comfyui-request-paths` 브랜치 — 요청 경로 ComfyUI 마이그레이션

이전 PR (`feat/comfyui-catalog-system`) 이 **읽기** 경로 (catalog/system/dashboard) 를
ComfyUI 로 옮긴 후, 본 PR 에서 **쓰기** 경로 (배치 enqueue + 단일 generate) 도
ComfyUI workflow 기반으로 재작성. A1111 backend 잔재는 deprecation 헤더 + 410
Gone 으로 정리.

**선행 별도 커밋** (`main` 직접): `7b7c676 feat(worker): timeout-based stuck-task
recovery` — `started_at` 컬럼 + `Database.find_and_requeue_stuck_tasks(timeout)`.
본 PR 과 무관, 워커 멈춤 회복용. 본 브랜치는 그 위에서 분기.

**커밋 stack** (예정):

- **server.py — schema + helper + expand 재작성**
  - `BatchCommonParams`: `steps/cfg/sampler` 를 `int|None` (variant default
    override). `width/height` 제거 (workflow_params 슬롯).
  - `DesignBatchRequest`: `models`/`loras` 제거, `workflow_category` (단일) +
    `workflow_variants[]` + `workflow_params_overrides[dict]` 추가. `subject`/
    `prompt_mode`/`style_extra` 도 직접 노출.
  - `seeds_per_combo` 상한 64 → 256 (variants/overrides 가 줄면서 seeds 가
    주된 다양성 축).
  - 신규 `_make_workflow_task(...)` helper — `expand_design_batch` 와
    `workflows_generate` 가 task dict 모양을 공유.
  - `expand_design_batch` 재작성: `prompts × variants × overrides × seeds` 곱집합.
    backend='comfyui' 명시, workflow_category/variant/params_json 채움.
    variant 사전 검증 (등록·available) 후 곱집합 expand.
  - `_enqueue_design_batch` disk guard 가 `max(variant.outputs) × len(tasks)`
    상향.
  - `_format_lora_suffix` 삭제 (A1111 잔재). `LoraSpec` 클래스도 제거.
  - `_extract_tasks_from_spec` 삭제 (A1111 spec 파서, 사용처 없음).
  - `GenerateRequest`/`BatchGenerateRequest` 는 stub 으로 축소 (410 응답 위해
    bare validation 만 유지).

- **server.py — 410 Gone 전환**
  - `_legacy_generate_gone(successor)` helper: 410 + Deprecation/Sunset(+90d)/
    Link rel=successor-version 헤더 + 모듈 1회 logger.warning.
  - `POST /api/generate` → 410, successor `/api/workflows/generate`.
  - `POST /api/generate/batch` → 410, successor `/api/batches`.

- **models.py — list_recent_batches 워크플로우 메타 노출**
  - SELECT 에 `MIN(workflow_category)`, `MIN(backend)` 추가.
  - distinct `workflow_variant` 별도 쿼리로 모은 후 row 에 `workflow_variants`
    리스트 첨부 (한 batch 안에 여러 variant 섞일 가능성 대비).

- **frontend**
  - `static/app/js/api.jsx`: `workflowsGenerate(spec)` 메서드 추가.
    `createDesignBatch` 주석을 워크플로우 곱집합으로 갱신.
  - `static/app/js/screens/BatchNew.jsx` 재작성 (3-step 마법사 유지):
    workflow_category 드롭다운 + variants 멀티셀렉트 + overrides JSON 텍스트
    리스트. prompts × variants × overrides × seeds 곱집합 예상 카운트 라이브
    표시. prefill `?workflow_category&workflow_variant`.
  - `static/app/js/screens/Manual.jsx` 신규 (`/manual` 라우트):
    workflow_category/variant 단일 선택 + prompt + workflow_params + seeds +
    candidates_total → `POST /api/workflows/generate` 호출 후 job 상태 폴링,
    완료되면 cherry-pick 진입 버튼.
  - `static/app/js/screens/Batches.jsx`: `workflow` 컬럼 신설 (category pill +
    variants 라벨, 2 초과 시 `+N`).
  - `static/app/js/chrome.jsx`: 사이드바에 `/manual` 항목 추가
    (assets ↔ batches 사이).
  - `static/app/js/app.jsx`: `/manual` ROUTES 등록.
  - `static/app/manifest.json`: `screens/Manual.jsx` 로드 순서 등록.

- **scripts/dogfood_*.sh** 워크플로우 페이로드로 변환 (A1111 `models/loras`
  매트릭스 → `workflow_category=sprite, workflow_variants=[pixel_alpha],
  workflow_params_overrides=[{}]`). 100장 케이스는 `seeds_per_combo=100` 단일
  combo 로 등가.

- **tests**
  - `tests/test_batch_expand.py` 전면 재작성 (15 cases) — 워크플로우 곱집합
    수학, backend='comfyui' 회귀 가드, override 파라미터 직렬화, missing
    variant → 400, /api/batches 응답에 workflow_variants 노출.
  - `tests/test_auth_policy.py` — write 401/200 체크를 `/api/generate` 에서
    `/api/batches` 로 이전 (기존 410). 신규 `test_legacy_generate*_returns_410`
    2건 (Deprecation/Sunset/Link 헤더 + successor 검증).
  - `tests/test_review_fixes.py` — `test_generate_returns_507_when_disk_low`
    가 `/api/batches` 로 갈아탐 (410 된 endpoint 우회).
  - `tests/test_dogfood_scripts_schema.py` — docstring 만 ComfyUI 기준으로
    갱신 (테스트 자체는 schema validation 그대로).
  - **삭제** `tests/test_generate_batch.py`, `tests/test_spec_parser.py` —
    A1111 spec 파서 / `/api/generate/batch` enqueue 테스트, 모두 dead path.

**검증** (commit 시점):

```
pytest --ignore=tests/test_generator_comfyui.py -q
  → 433 passed
```

**의도적 scope 밖**:
- A1111 백엔드 코드 (`sd_backend.py`, `generator.py`) 의 물리적 제거 —
  CLAUDE.md 약속대로 v0.4.0 메이저에서 처리.
- BatchNew.jsx / Manual.jsx 의 단위 테스트 인프라 — 프로젝트에 jsx 테스트
  러너 자체가 없음. 별도 인프라 PR.

---

## 핸드오프

### 통합 smoke (사용자 외부, ~10min)

```bash
# 백엔드
curl -s localhost:8000/api/health | jq .ok
curl -X POST localhost:8000/api/generate -d '{}' -i | head -1     # 410 Gone
curl -X POST localhost:8000/api/generate/batch -d '{}' -i | head -1  # 410 Gone
curl -I -X POST localhost:8000/api/generate -d '{}'              # Deprecation/Sunset/Link

# 워크플로우 단일 (ComfyUI 떠있어야 함)
curl -X POST localhost:8000/api/workflows/generate \
  -H "Content-Type: application/json" \
  -d '{
    "project":"smoke","asset_key":"test_sprite",
    "workflow_category":"sprite","workflow_variant":"pixel_alpha",
    "subject":"silver hair magical girl in school uniform"
  }' | jq .

# 워크플로우 배치 (3장)
curl -X POST localhost:8000/api/batches \
  -H "Content-Type: application/json" \
  -d '{
    "asset_key":"smoke_batch","project":"smoke",
    "workflow_category":"sprite","workflow_variants":["pixel_alpha"],
    "prompts":["silver hair magical girl in school uniform"],
    "prompt_mode":"legacy","seeds":[1,2,3]
  }' | jq .

curl -s localhost:8000/api/batches?limit=5 | jq '.items[0] | {batch_id, workflow_category, workflow_variants, total}'

# 프론트 (브라우저)
# /app/manual        → workflow_category/variant 셀렉트 + prompt + 1장 생성, job 상태 폴링
# /app/batches/new   → 3-step 마법사. variants 멀티셀렉트, overrides 추가, 곱집합 카운트
# /app/batches       → workflow 컬럼에 category pill + variants 라벨
```

### 환경 이슈 (선행 PR 부터 누적)

- `tests/test_generator_comfyui.py` 는 `aioresponses` 미설치로 collect 에러 —
  본 PR 무관, `.venv/bin/pip install aioresponses==0.7.8` 로 해결.

### 후속

- `sincerity-skills/asset-factory-api/SKILL.md` 갱신 — `/api/workflows/generate`,
  `/api/batches` (DesignBatchRequest 새 schema) 사용 예시 추가, `/api/generate*`
  410 Gone 명시. 별도 PR (skill 은 별도 repo).
- A1111 backend 물리적 제거 (v0.4.0): `sd_backend.py`, `generator.py`,
  `BackendRegistry` 의 a1111 entry, `/api/sd/catalog/*` 엔드포인트, `healthSd`
  api 메서드. 본 PR 머지 후 제거 PR 으로 분리.
