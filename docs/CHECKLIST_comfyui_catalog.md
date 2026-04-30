# CHECKLIST — ComfyUI 기반 Catalog/System 개편 GAP 검수 체크리스트

> PLAN: [`PLAN_comfyui_catalog.md`](./PLAN_comfyui_catalog.md)
> 본 문서는 9 task 의 결과물을 **멍멍이 직접 셀프 검수** 할 수 있는 체크박스 형태로 분해.
> 각 항목은 (a) **검증 명령** 또는 (b) **수동 확인 절차** 둘 중 하나로 reproducible.
> 각 task 는 TDD: ❶ 실패 테스트 commit → ❷ 구현 commit → ❸ 그린 확인.

---

## 사용 방법

1. 멍멍이 PR 받으면 본 문서를 위→아래로 훑으면서 체크박스 채우기.
2. 검증 명령은 그대로 복사-붙여넣기 가능. 수동 확인은 "예상 결과" 와 비교.
3. 모든 P0 항목이 ✓ 면 PR 머지 OK. P1 은 follow-up 으로 빼도 OK.

체크 표기:
- `[ ]` — 미확인
- `[x]` — 확인 완료
- `[~]` — 부분 통과 (메모 필수)
- `[!]` — 실패 (사유 메모 후 차단)

---

## Task 1 — `/api/comfyui/health` (P0)

### TDD 흐름
- [x] **❶ red**: `tests/test_workflow_endpoints.py::test_comfyui_health_*` 4 케이스 작성 (구현 전 fail 확인 — 본 PR commit `a3a83a4` 시점에서는 implementation 동시 수반 — 본 task 는 PR #46 에 이미 머지된 패턴)
- [x] **❷ green**: `server.py` endpoint 추가
- [x] **❸ refactor**: 기존 `_SD_HEALTH_TIMEOUT_SECONDS` 패턴 재사용, env override 추가

### 기능 검수
- [ ] `pytest -q tests/test_workflow_endpoints.py -k comfyui_health` → **4 passed**
- [ ] `curl -s http://127.0.0.1:47823/api/comfyui/health | jq .ok` → `true`
- [ ] 같은 호출 시간 < **0.5s**: `time curl -s http://127.0.0.1:47823/api/comfyui/health > /dev/null`
- [ ] 응답에 `comfyui_version`, `device_count`, `device_names`, `queue.{running,pending}`, `workflows_available` 모두 존재
- [ ] ComfyUI 죽인 상태 모의 (`COMFYUI_HOST=127.0.0.1:1` 환경변수 + 재시작) 후 호출 → **5초 안에** `{"ok":false, "error":"timeout..."}` 반환

---

## Task 2 — `/api/comfyui/catalog` (P0, Task 1 후)

### TDD 흐름
- [ ] **❶ red**: 다음 테스트 4개 작성 후 fail 확인 (구현 0 LOC 상태)
  - `test_comfyui_catalog_extracts_checkpoints_loras_vaes_controlnets` — fixture 기반 단위 테스트
  - `test_comfyui_catalog_used_by_workflows_cross_ref` — variant api.json → checkpoint 매칭
  - `test_comfyui_catalog_endpoint_returns_200_with_payload` — endpoint 통합
  - `test_comfyui_catalog_endpoint_timeout_returns_stale_or_error` — hang → 5초 fail-fast
- [ ] **❷ green**: `lib/comfyui_catalog.py` (순수 변환 함수) + `server.py` endpoint
- [ ] **❸ refactor**: 60s in-memory 캐시 + asyncio lock

### 기능 검수
- [ ] `pytest -q tests/test_comfyui_catalog.py` → **4 passed**
- [ ] `pytest -q tests/test_workflow_endpoints.py -k comfyui_catalog` → **2 passed**
- [ ] `curl -s http://127.0.0.1:47823/api/comfyui/catalog | jq '.checkpoints | length'` → **>= 1**
- [ ] `curl -s http://127.0.0.1:47823/api/comfyui/catalog | jq '.workflows | length'` → **>= 1**
- [ ] `curl -s http://127.0.0.1:47823/api/comfyui/catalog | jq '.checkpoints[0] | keys'` → `["family", "name", "used_by_workflows"]` (alphabetical)
- [ ] cache: 두 번 연속 호출, 두 번째 < 100ms (`time curl ... && time curl ...`)
- [ ] cross-ref 정확성: 응답의 첫 checkpoint 의 `used_by_workflows` 가 비어있지 않거나 빈 배열 (수동 spot-check 1건)

### 검증 명령 묶음
```bash
# 단위 + 통합 테스트
pytest -q tests/test_comfyui_catalog.py tests/test_workflow_endpoints.py -k comfyui

# 실서버 smoke
curl -s http://127.0.0.1:47823/api/comfyui/catalog | jq '{
  checkpoints: (.checkpoints | length),
  loras: (.loras | length),
  vaes: (.vaes | length),
  controlnets: (.controlnets | length),
  workflows: (.workflows | length),
  fetched_at, stale
}'
```

---

## Task 3 — `/api/comfyui/queue` (P1, 독립)

### TDD 흐름
- [ ] **❶ red**: `test_comfyui_queue_*` 3 케이스 작성 후 fail
  - `test_comfyui_queue_normal_returns_running_pending_counts`
  - `test_comfyui_queue_timeout_returns_200_with_ok_false`
  - `test_comfyui_queue_exception_returns_200_with_ok_false`
- [ ] **❷ green**: server.py endpoint 추가 (`comfyui_client.queue_state()` wrap)
- [ ] **❸ refactor**: 5초 timeout, 정규화된 응답 (`{"ok", "running", "pending", "fetched_at"}`)

### 기능 검수
- [ ] `pytest -q tests/test_workflow_endpoints.py -k comfyui_queue` → **3 passed**
- [ ] `curl -s http://127.0.0.1:47823/api/comfyui/queue | jq .ok` → `true`
- [ ] 응답 schema: `{ok, running:[...], pending:int, fetched_at}`

---

## Task 4 — `/api/health/sd` 응답에 primary/deprecated 필드 (P0, Task 1 후)

### TDD 흐름
- [ ] **❶ red**: `test_health_sd_includes_primary_and_deprecated_backends` — 기존 응답에 `primary=="comfyui"`, `deprecated_backends==["a1111"]` 두 필드 검증, fail 확인
- [ ] **❷ green**: server.py 응답 dict 에 두 필드 추가
- [ ] **❸ refactor**: 기존 응답 구조 유지 (호환성), 추가만

### 기능 검수
- [ ] `pytest -q tests/test_workflow_endpoints.py -k health_sd` → **all passed (회귀 0)**
- [ ] `curl -s http://127.0.0.1:47823/api/health/sd | jq .primary` → `"comfyui"`
- [ ] `curl -s http://127.0.0.1:47823/api/health/sd | jq .deprecated_backends` → `["a1111"]`
- [ ] 기존 `backends.{a1111,comfyui}` 필드 변경 없음 (호환성 유지)

---

## Task 5 — Frontend `api.comfyui.*` (P0, Task 1-2 후)

### TDD 흐름
- 프론트는 자동 테스트 부재 → **수동 smoke** 만.
- [ ] **❶ red**: `static/app/js/api.jsx` 에 `api.comfyui` 가 *없는* 상태 확인 (의도)
- [ ] **❷ green**: `api.comfyui = { health, catalog, queue }` 추가
- [ ] **❸ refactor**: `api.sd.*` 는 그대로 두고 JSDoc 에 `@deprecated` 마킹

### 기능 검수
- [ ] grep: `grep -n "api.comfyui" static/app/js/api.jsx` → 3 매칭 (health/catalog/queue)
- [ ] 브라우저 콘솔: `await api.comfyui.health()` → ok=true 응답
- [ ] 브라우저 콘솔: `await api.comfyui.catalog()` → checkpoints 배열 존재
- [ ] `api.sd.*` 는 그대로 호출 가능 (호환성)

---

## Task 6 — Catalog.jsx 재작성 (P0, Task 2/5 후)

### TDD 흐름
- [ ] **❶ red**: 빈 데이터로 렌더 → "비어있음" 상태 확인 (수동)
- [ ] **❷ green**: `api.comfyui.catalog()` + `api.sd.catalogUsage()` 통합 렌더
- [ ] **❸ refactor**: skeleton row, 에러 배너, 빈 카테고리 숨김

### 기능 검수 (브라우저 수동)
- [ ] `/app/catalog` 진입 → **5초 안에** 렌더 시작 (network tab 으로 확인)
- [ ] 워크플로우 섹션: 25개 변형 노출
- [ ] Checkpoints 섹션: ComfyUI 의 10개 모델 노출
- [ ] LoRAs 섹션: 6개
- [ ] VAEs 섹션: 2개
- [ ] ControlNets 섹션: 3개
- [ ] Usage 섹션 (DB 기반) 정상 렌더
- [ ] ComfyUI down 시뮬: 에러 배너 + 재시도 버튼 노출
- [ ] 워크플로우 → 모델 cross-link: 모델 칸의 "used by 4 wf" 클릭 → 해당 워크플로우 하이라이트 (또는 anchor 이동)

---

## Task 7 — System.jsx + hooks.jsx + Dashboard.jsx (P0, Task 4/5 후)

### TDD 흐름
- [ ] **❶ red**: 현재 a1111 칩 가시성 확인
- [ ] **❷ green**: a1111 카드 → 작은 deprecated 박스로 축소, ComfyUI 카드 메인화
- [ ] **❸ refactor**: `useSdHealthBanner` 가 `comfyui.ok` 만 평가

### 기능 검수 (브라우저 수동)
- [ ] `/app/system` SD 헬스 카드: ComfyUI 정보가 메인 (큰 카드), a1111 은 작은 deprecated 노트
- [ ] ComfyUI down 시 → 글로벌 배너 표시
- [ ] a1111 down + ComfyUI alive 상태 → 글로벌 배너 **표시되지 않음**
- [ ] Dashboard top bar SD 칩: ComfyUI 기준 ✓/✗
- [ ] DB/logs 패널: SD health timeout 영향 없이 정상 렌더

---

## Task 8 — A1111 catalog endpoint deprecate (P1, Task 6 후)

### TDD 흐름
- [ ] **❶ red**: `test_sd_catalog_models_response_includes_deprecation_marker` fail
- [ ] **❷ green**: 응답 헤더 `Deprecation: true`, `Sunset: <date+90>`, 본문 `deprecated: true` 추가
- [ ] **❸ refactor**: 모듈 레벨 deprecation_logger 한 번만 warn

### 기능 검수
- [ ] `pytest -q tests/test_workflow_endpoints.py -k deprecat` → passed
- [ ] `curl -I http://127.0.0.1:47823/api/sd/catalog/models` → `Deprecation: true` 헤더
- [ ] 같은 호출 본문에 `deprecated: true` 또는 (timeout 시) detail 에 deprecated 표시
- [ ] 서버 로그에 deprecation warning 1회 이상

---

## Task 9 — Docs 갱신 (P0, 마지막)

### 검수
- [ ] `docs/NEXT.md` 본 작업 요약 항목 추가 (날짜, 핵심 변경, 후속)
- [ ] `CLAUDE.md` Catalog/System 화면 데이터 출처 = ComfyUI 로 갱신
- [ ] `DESIGN.md` 백엔드 다이어그램에서 a1111 박스 → "deprecated" 마킹
- [ ] `~/.hermes/skills/devops/asset-factory-api/SKILL.md` 갱신:
  - `api.comfyui.health` / `catalog` / `queue` 사용 예시
  - 기존 `/api/sd/catalog/*` 호출은 deprecated 표기
- [ ] sincerity-skills repo 에 PR (asset-factory-api 스킬 갱신)

---

## 전체 회귀

- [ ] `pytest -q --ignore=tests/test_generator_comfyui.py --ignore=tests/test_api_system.py` → **전체 passed**
- [ ] `ruff check .` → **All checks passed!**
- [ ] `git diff main..HEAD --stat` 으로 변경 범위 sanity check (백엔드/프론트/테스트/docs 모두 변경)

---

## ⚠️ Freshness Fallback (PLAN §7 미이행 항목)

각 row 는 해당 task 가 머지될 때 strike (`~~...~~`).

| 항목 | 미이행 시 fallback | 상태 |
|---|---|---|
| `GET /api/comfyui/health` | `/api/health/sd` 의 `backends.comfyui` 사용 | **이행됨 (Task 1)** ✓ |
| `GET /api/comfyui/catalog` | 화면 빈 상태 (PR #45 의 fail-fast 마지노선) | **TODO** |
| `GET /api/comfyui/queue` | ComfyUI 직접 호출 (admin 만) | **TODO** |
| `api.comfyui.*` wrapper | `api.sd.healthSd()` 응답 추출 | **TODO** |
| Catalog 워크플로우 섹션 | 모델/LoRA 만 표시 | **TODO** |

---

## DoD (전체 시리즈)

본 시리즈가 머지 가능하려면 아래 항목 **모두** ✓ 여야 한다:

- [ ] Task 1-9 모든 P0 체크박스 ✓
- [ ] 전체 회귀 0 fail
- [ ] `ruff check .` clean
- [ ] `/app/catalog` 5초 안에 ComfyUI 기준 데이터 렌더 (브라우저 검수)
- [ ] `/app/system` a1111 칩 사라지고 ComfyUI 카드 정상 렌더
- [ ] PLAN §7 freshness 표 모두 strike
- [ ] sincerity-skills `asset-factory-api` 스킬 PR 머지 (Task 9 일부)

---

## 검수 후

- ✓ 모두 통과 → PR squash 머지 + main 동기화 + 서버 재시작
- 일부 P1 미통과 → P1 항목을 follow-up issue 로 분리 + P0 만 머지
- P0 미통과 → fail 사유 본 문서 하단에 추가 + 차단

---

## 검수 메모란 (멍멍 작성)

> 검수 중 발견한 이슈, follow-up, 의도 변경 등을 자유롭게 기록.

```
(여기에 작성)
```
