# PLAN — ComfyUI 기반 Catalog/System UI 개편

> SD WebUI(A1111) 의존을 제거하고 ComfyUI 를 1차 데이터 소스로 전환.
> 작성: 2026-04-29 · 작성자: Claude Code (멍멍 의뢰)
>
> 선행: PR #45 (5초 timeout 빠른 패치 — `/api/health/sd`, `/api/sd/catalog/{models,loras}`)
> 후행: 본 문서 — A1111 endpoint 자체를 deprecated 처리하고 ComfyUI 로 대체.

---

## 1. 동기 (Why)

### 1.1 현재 상황

- `/app/catalog`, `/app/system` 화면이 SD WebUI(A1111, `192.168.50.225:7860`) 의존.
- A1111 은 **deprecated** 상태로 사실상 영구 다운. 복구 계획 없음.
- ComfyUI(`192.168.50.225:8188`) 는 **유일한 활성 SD 백엔드**. 모든 신규 워크플로우가 ComfyUI 변형으로 정의됨 (`workflows/registry.yaml`).
- PR #45 가 timeout hang 은 막았지만 화면 자체는 여전히 빈 데이터 / 에러 칩 상태로 표시.

### 1.2 목표

1. **Catalog 화면이 ComfyUI 기준 모델/LoRA/VAE/ControlNet/Workflow 목록을 보여준다.**
2. **System 화면의 SD 헬스 패널이 "ComfyUI primary, A1111 deprecated" 로 표시된다.** (a1111 칩 자체 제거)
3. A1111 관련 API 표면 (`/api/sd/catalog/*`, `/api/health/sd` 의 a1111 필드) 은 **deprecated 마킹** 후 다음 메이저 버전에서 삭제.
4. 본 PR 머지 후 `/app/catalog` 가 5초 안에 의미 있는 데이터로 렌더링된다.

### 1.3 비목표 (이번 PR 에서는 안 함)

- 새로운 catalog 사용 통계 (`/api/sd/catalog/usage*`) 의 ComfyUI 화 — 별 follow-up.
- ComfyUI multi-backend (둘 이상의 ComfyUI 호스트) 지원.
- Workflow 변형별 사용 빈도 분석 화면 신설.
- A1111 API 즉시 삭제 — deprecated 만, 삭제는 다음 메이저.

---

## 2. 현재 인터페이스 진단

### 2.1 백엔드 (Python — `server.py`)

| Endpoint | 책임 | 백엔드 호출 | 상태 |
|---|---|---|---|
| `GET /api/health/sd` | 두 백엔드 헬스 묶음 | a1111 + comfyui | a1111 항상 timeout (5s) |
| `GET /api/sd/catalog/models` | A1111 checkpoint 목록 | a1111 only | 항상 503 (5s timeout) |
| `GET /api/sd/catalog/loras` | A1111 LoRA 목록 | a1111 only | 항상 503 (5s timeout) |
| `GET /api/sd/catalog/usage` | DB 기반 사용량 집계 | DB | OK (백엔드 무관) |
| `GET /api/sd/catalog/usage/batches` | 배치별 사용량 | DB | OK (백엔드 무관) |

### 2.2 프론트 (`static/app/js/`)

| 파일 | 호출 | 화면 |
|---|---|---|
| `screens/Catalog.jsx` | `api.sd.models()`, `api.sd.loras()`, `api.sd.catalogUsage*()` | `/app/catalog` |
| `screens/System.jsx` | `api.sd.healthSd()` | `/app/system` |
| `screens/Dashboard.jsx` | `api.sd.healthSd()` (top bar) | `/app/dashboard` |
| `hooks.jsx` | `/api/health/sd` 폴링 (`useSdHealthBanner`) | global banner |
| `api.jsx` | wrapper (`api.sd.models`, `api.sd.loras`, ...) | — |

### 2.3 ComfyUI 가 제공하는 것 (확인됨, 2026-04-29 측정)

`GET http://192.168.50.225:8188/object_info` — **1.94MB, 1430 node types, 220ms**

추출 가능 데이터:

| 카테고리 | 노드 | 필드 | 현재 값 (예시) |
|---|---|---|---|
| Checkpoint | `CheckpointLoaderSimple` | `ckpt_name[0]` | 10개 (AnythingXL, animagineXL, ...) |
| LoRA | `LoraLoader` | `lora_name[0]` | 6개 (Pixel_Resin, Fnaf, ...) |
| VAE | `VAELoader` | `vae_name[0]` | 2개 (xlVAEC, pixel_space) |
| Upscale | `UpscaleModelLoader` | `model_name[0]` | (확인 시점에 비어 있음, 노드 존재) |
| ControlNet | `ControlNetLoader` | `control_net_name[0]` | 3개 |
| Embedding | `CLIPTextEncode` 자체로는 X | (별도 `/embeddings` endpoint) | TBD |

ComfyUI 는 **헬스/시스템 정보** 도 풍부:
- `GET /system_stats` → comfyui_version, python_version, device list (GPU)
- `GET /queue` → running/pending prompt 수
- `GET /history` (optional limit) → 최근 처리 이력

### 2.4 워크플로우 변형 (asset-factory 자체 데이터)

`workflow_registry.WorkflowRegistry` 가 이미 25개 변형 로드 중 (`workflows/registry.yaml`).
이건 ComfyUI 와 무관하게 우리 자체 메타데이터 — Catalog 의 **상위 그룹** 으로 노출하는 게 자연스럽다.

```
워크플로우 (25)
 ├ logo:flat-color (variants 3)
 ├ sprite:character-stack (variants 4)
 └ ...
모델 (10)         ← ComfyUI checkpoint
LoRA (6)         ← ComfyUI lora
VAE (2)          ← ComfyUI vae
ControlNet (3)   ← ComfyUI controlnet
```

---

## 3. 미래 인터페이스 (Red Phase — 이상적 모양)

> **skill-tdd-future-interface 적용**: 아래 인터페이스를 "이미 존재한다"고 가정하고
> 프론트를 작성한다. 백엔드 구현은 §4 의 task 로 분리.

### 3.1 새 endpoint — `/api/comfyui/*`

```
GET /api/comfyui/health
GET /api/comfyui/catalog
GET /api/comfyui/queue
```

#### 3.1.1 `GET /api/comfyui/health`

```json
{
  "ok": true,
  "comfyui_version": "0.19.3",
  "python_version": "3.10.11 ...",
  "host": "192.168.50.225:8188",
  "devices": [
    {"name": "cuda:0 NVIDIA GeForce RTX 4080 SUPER", "type": "cuda"}
  ],
  "queue": {"running": 0, "pending": 0},
  "workflows_available": 25,
  "workflows_needing_conversion": 0,
  "fetched_at": "2026-04-29T01:30:00Z"
}
```

**실패 시** (ComfyUI down):
```json
{"ok": false, "error": "timeout: ComfyUI did not respond within 5.0s", "host": "192.168.50.225:8188"}
```

HTTP status: 항상 200. `ok` 필드로 판단. (헬스 체크가 200/503 둘 다 내면 프론트 배너 로직이 깨져서.)

#### 3.1.2 `GET /api/comfyui/catalog`

```json
{
  "fetched_at": "2026-04-29T01:30:00Z",
  "stale": false,
  "checkpoints": [
    {"name": "AnythingXL_xl.safetensors", "family": "sdxl", "used_by_workflows": ["sprite:char-stack", ...]}
  ],
  "loras": [
    {"name": "Pixel_Resin_x16_pixel_art_witches.safetensors", "used_by_workflows": [...]}
  ],
  "vaes": [{"name": "xlVAEC_g102.safetensors"}],
  "controlnets": [{"name": "control-lora-openposeXL2-rank256.safetensors"}],
  "upscalers": [],
  "workflows": [
    {
      "id": "sprite:character-stack",
      "category": "sprite",
      "label": "Character Stack (4 variants)",
      "variants": 4,
      "uses_models": ["AnythingXL_xl.safetensors"],
      "uses_loras": ["Pixel_Resin_x16_..."]
    }
  ]
}
```

캐시: 60초 in-memory (ComfyUI 재시작이 잦은 환경 아님).
실패 시: `{"ok": false, "error": "..."}` — 200 응답, `stale: true` + 마지막 성공 페이로드 첨부 (있으면).

#### 3.1.3 `GET /api/comfyui/queue` (System 화면용)

```json
{
  "ok": true,
  "running": [{"prompt_id": "abc-123", "client_id": "...", "started_at": null}],
  "pending": 2,
  "fetched_at": "..."
}
```

### 3.2 프론트 wrapper (`api.jsx`)

```js
api.comfyui = {
  health: () => request('GET', '/api/comfyui/health'),
  catalog: () => request('GET', '/api/comfyui/catalog'),
  queue: () => request('GET', '/api/comfyui/queue'),
};

// deprecated — 다음 메이저에서 제거
api.sd = {
  // health/sd 는 그대로 유지 (a1111 + comfyui 모두 보여줌, 호환성)
  ...
};
```

### 3.3 화면 변화

#### Catalog (`/app/catalog`)

```
┌─ 워크플로우 (25)              ──── ComfyUI 0.19.3, RTX 4080 SUPER
│ ▸ logo:flat-color  (3 variants)
│ ▸ sprite:character-stack (4 variants)
│ ...
├─ Checkpoints (10)
│   ┌────────────────────────────┬───────┬───────────────┐
│   │ AnythingXL_xl.safetensors  │ sdxl  │ used by 4 wf  │
│   │ animagineXL40Opt_clear     │ sdxl  │ used by 2 wf  │
│   │ ...                         │       │               │
├─ LoRAs (6)
├─ VAEs (2)
├─ ControlNets (3)
└─ Usage (last 7 days)            ──── DB 기반 (변경 없음)
```

#### System (`/app/system`)

a1111 칩 제거. SD 백엔드 칩이 "ComfyUI ✓" 1개로 단순화.
A1111 관련 표시는 **deprecated 노트** 1줄 (영구 제거 일정 명시).

#### Dashboard / Banner

`useSdHealthBanner` 가 `/api/health/sd` 의 `comfyui.ok` 만 평가. (a1111 무시.)

---

## 4. Task Breakdown

각 task 는 **독립 PR 단위**. 의존성은 명시.

### Task 0 — 사전: PR #45 머지 확인 (선행)

- [x] PR #45 OPEN (UNSTABLE check). 자동 머지 대기.
- 이후 작업은 `main` 베이스 + `#45` 머지 후 진행.

### Task 1 — 백엔드: `/api/comfyui/health` (P0, 독립)

**파일**: `server.py`, `tests/test_server_comfyui_endpoints.py` (신규)

**구현 요점**:
- `ComfyUIClient.health_check()` 가 이미 있음 (`generator_comfyui.py:145`). 재사용.
- `asyncio.timeout(5)` wrapper.
- `/queue` 도 함께 호출해서 응답에 합치기 (1번 호출로 묶음).
- 실패해도 200 + `ok=false`.

**테스트**:
- ComfyUI 정상 → 200, `ok=true`, `devices` 비어있지 않음.
- ComfyUI hang (mock) → 5초 안에 200, `ok=false, error="timeout..."`.
- ComfyUI 500 → 200, `ok=false, error="..."`.

**검증 후 머지 OK 조건**: 위 3 케이스 + `pytest -q` 회귀 0.

---

### Task 2 — 백엔드: `/api/comfyui/catalog` (P0, Task 1 후)

**파일**: `server.py`, `lib/comfyui_catalog.py` (신규), 테스트 추가.

**구현 요점**:
- `ComfyUIClient` 에 `object_info()` 메서드 추가 (`/object_info` GET, 30s timeout — 1.94MB).
- `lib/comfyui_catalog.py` — 응답을 §3.1.2 모양으로 변환. 노드 매핑 테이블 분리:
  ```python
  CATALOG_SOURCES = [
      ("checkpoints", "CheckpointLoaderSimple", "ckpt_name"),
      ("loras", "LoraLoader", "lora_name"),
      ("vaes", "VAELoader", "vae_name"),
      ("controlnets", "ControlNetLoader", "control_net_name"),
      ("upscalers", "UpscaleModelLoader", "model_name"),
  ]
  ```
- WorkflowRegistry (`workflow_registry.py`) 와 cross-reference: 각 model/lora 가 어떤 변형에서 쓰이는지 정적 분석.
  - registry variant 의 patch 정의를 훑어서 `CheckpointLoaderSimple` / `LoraLoader` 의 `ckpt_name` / `lora_name` 추출.
  - 결과를 catalog 응답의 `used_by_workflows` 에 채움.
- 60초 in-memory 캐시 (`functools` + asyncio lock).

**테스트**:
- 골든 fixture: `tests/fixtures/comfyui_object_info.json` (실제 응답 캡처본, 100KB 정도로 trim).
- catalog 변환 함수 단위 테스트 (서버 무관, 순수 함수).
- endpoint 통합: ComfyUI mock → 정상 페이로드 / hang → stale + 직전 페이로드.

**검증 후 머지 OK 조건**: catalog 변환 단위 테스트 + endpoint 3 케이스 통과 + 실서버 smoke (`curl /api/comfyui/catalog | jq .checkpoints | length` ≥ 1).

---

### Task 3 — 백엔드: `/api/comfyui/queue` (P1, 독립)

**파일**: `server.py`, 테스트.

**구현 요점**:
- `ComfyUIClient.queue_state()` 이미 있음 (`generator_comfyui.py:158`). 그대로 노출.
- 응답 정규화: `{"ok": true, "running": [...], "pending": int}`.
- 5초 timeout.

**테스트**: 정상 / hang / 500 3 케이스.

**우선순위 P1 사유**: System 화면 부가 정보. Catalog/Health 가 P0 통과 후 1주일 내 추가하면 충분.

---

### Task 4 — 백엔드: `/api/health/sd` 응답 정리 (P0, Task 1 후)

**파일**: `server.py`, 기존 테스트 수정.

**구현 요점**:
- 기존 응답 구조 유지 (호환성). 필드 추가:
  ```json
  {
    "primary": "comfyui",
    "deprecated_backends": ["a1111"],
    "backends": {"a1111": {...}, "comfyui": {...}}
  }
  ```
- a1111 호출 자체는 유지하되 (legacy clients), 응답에 `deprecated: true` 명시.
- 다음 메이저(v0.4.0) 에서 a1111 백엔드 코드 삭제.

**검증**: `curl /api/health/sd | jq .primary` == `"comfyui"`.

---

### Task 5 — 프론트: `api.jsx` 에 `api.comfyui` 추가 (P0, Task 1-2 후)

**파일**: `static/app/js/api.jsx`.

**구현 요점**:
- `api.comfyui = { health, catalog, queue }`.
- `api.sd.*` 는 그대로 (deprecated 주석 + JSDoc 만 추가).

**테스트**: jsx 자동 테스트 없음 — manual smoke.

---

### Task 6 — 프론트: Catalog.jsx 재작성 (P0, Task 2/5 후)

**파일**: `static/app/js/screens/Catalog.jsx`.

**구현 요점**:
- 데이터 소스: `api.comfyui.catalog()` (1회) + `api.sd.catalogUsage()` (변경 없음).
- 섹션 구성: 워크플로우 → Checkpoints → LoRAs → VAEs → ControlNets → Usage.
- 빈 카테고리는 "—" 표시 후 숨김 옵션.
- 로딩 상태: skeleton row.
- 에러: ComfyUI down 이면 "ComfyUI 연결 불가, catalog 조회 불가" 배너 + 재시도 버튼.

**검증 후 머지 OK 조건**: `/app/catalog` 5초 안에 데이터 렌더, 모든 섹션 클릭 가능, 워크플로우 → 모델 cross-link 동작.

---

### Task 7 — 프론트: System.jsx + hooks.jsx (P0, Task 4/5 후)

**파일**:
- `static/app/js/screens/System.jsx`
- `static/app/js/hooks.jsx` (`useSdHealthBanner`)
- `static/app/js/screens/Dashboard.jsx` (탑바)

**구현 요점**:
- System 화면: SD 백엔드 카드 1개 (ComfyUI). a1111 카드는 작은 deprecated 박스로 축소.
- `useSdHealthBanner`: `comfyui.ok` 만 평가. a1111 down 은 배너 띄우지 않음.
- Dashboard top bar: SD 칩이 ComfyUI 기준으로 ✓ 또는 ✗.

---

### Task 8 — Cleanup: A1111 catalog endpoint deprecate (P1, Task 6 후)

**파일**: `server.py` (a1111 catalog 핸들러).

**구현 요점**:
- `/api/sd/catalog/models`, `/api/sd/catalog/loras` 응답 헤더에 `Deprecation: true` + `Sunset: <day+90>` 추가.
- 응답 본문에 `deprecated: true` 필드 추가.
- 로그에 deprecation warning.
- 다음 메이저에서 410 Gone 으로 전환.

**우선순위 P1**: 호환성 확보 후 처리해도 됨.

---

### Task 9 — Docs: 변경 기록 (P0, 마지막)

**파일**:
- `docs/NEXT.md` — 본 작업 요약 항목 추가.
- `CLAUDE.md` — Catalog/System 화면 데이터 출처를 "ComfyUI" 로 갱신.
- `DESIGN.md` — backend 스택 다이어그램에서 a1111 박스 deprecated 처리.
- `~/.hermes/skills/devops/asset-factory-api/SKILL.md` — agent 가 catalog 조회 시 사용하는 endpoint 갱신.

---

## 5. 의존성 그래프

```
[#45 PR merge]   ← 선행
       │
       ├─→ Task 1 (health)  ─┐
       ├─→ Task 3 (queue)   ─┤
       ├─→ Task 4 (sd merge)─┤
       │                      │
       └─→ Task 2 (catalog) ─┤
                              │
       Task 5 (api.jsx) ◀────┤
              │
              ├─→ Task 6 (Catalog.jsx)
              ├─→ Task 7 (System / hooks / Dashboard)
              └─→ Task 8 (deprecate)
                     │
                     └─→ Task 9 (docs)
```

병렬 가능: 1/3/4 동시. 2 는 1 의 ComfyUI client 패턴 재사용해야 깔끔하므로 1 후.

---

## 6. 커밋/PR 슬라이싱

| PR # | 범위 | 추정 LOC |
|---|---|---|
| 1 | Task 1+4 (health 통합) | ~150 |
| 2 | Task 2 (catalog 백엔드 + fixture) | ~300 |
| 3 | Task 3 (queue) | ~80 |
| 4 | Task 5+6 (frontend Catalog) | ~250 |
| 5 | Task 7 (System / hooks / Dashboard) | ~120 |
| 6 | Task 8+9 (deprecate + docs) | ~80 |

PR 1·2 는 백엔드 only — 프론트 깨지지 않음 (a1111 endpoint 남아 있음).
PR 4 부터 프론트 보임. PR 4 머지 시점에 화면 정상화.

---

## 7. ⚠️ 미래 인터페이스 freshness — 합류 전 fallback

**본 PLAN 의 §3 인터페이스가 다 구현되기 전 까지는 아래 fallback 적용.**

| 미래 인터페이스 | 현재 fallback |
|---|---|
| `GET /api/comfyui/health` | `GET /api/health/sd` 의 `backends.comfyui` 필드 사용 |
| `GET /api/comfyui/catalog` | 없음 — 화면은 빈 상태 (PR #45 의 5초 fail-fast 가 마지노선) |
| `GET /api/comfyui/queue` | ComfyUI 직접 호출 (`192.168.50.225:8188/queue`) — admin 만 가능 |
| `api.comfyui.*` | `api.sd.healthSd()` 응답에서 추출 |
| Catalog 화면 워크플로우 섹션 | 없음 — 현재는 모델/LoRA 만 표시 |

각 PR 머지 시 해당 row 삭제.

---

## 8. 위험 / 롤백

### 위험
- **ComfyUI `/object_info` 응답 크기**: 1.94MB. 매 호출마다 풀로딩하면 부하. → 60s 캐시 + 200ms 응답 시간으로 충분.
- **WorkflowRegistry cross-ref 정확성**: variant patch 가 동적 표현식이면 정적 분석 한계. → 첫 PR 에선 단순 매칭만, 누락은 "사용처 미상" 으로 표시.
- **a1111 deprecated 가 외부 클라이언트 깨뜨림**: 우리 자체 사용만 — 외부 영향 없음. 그래도 90일 sunset 적용.
- **ComfyUI 가 다른 호스트로 이동**: env `COMFYUI_HOST` 로 이미 추상화됨. 재배치 시 env 만 변경.

### 롤백
- 각 PR 독립이라 PR 단위 revert 가능.
- 가장 위험한 것은 PR 4 (Catalog.jsx 재작성). git revert 후 a1111 endpoint 가 살아있는 한 이전 화면 복구 가능 (PR #45 timeout 동작도 OK).

---

## 9. Definition of Done

- [ ] `/app/catalog` 가 5초 안에 ComfyUI 기준 모델/LoRA/VAE/ControlNet/Workflow 목록을 표시.
- [ ] `/app/system` 의 SD 헬스 카드가 ComfyUI primary 기준으로 표시. a1111 칩 사라짐.
- [ ] `useSdHealthBanner` 가 ComfyUI down 일 때만 배너 표시.
- [ ] `pytest -q` 통과 (신규 테스트 포함).
- [ ] manual smoke: `curl /api/comfyui/health`, `curl /api/comfyui/catalog | jq` 모두 5초 안에 의미 있는 페이로드.
- [ ] `docs/NEXT.md`, `CLAUDE.md`, `DESIGN.md`, `asset-factory-api` 스킬 모두 갱신.
- [ ] `/api/sd/catalog/*` 응답에 `Deprecation` 헤더 + `deprecated: true` 필드.

---

## 10. 후속 (다음 메이저 v0.4.0)

- A1111 백엔드 코드 (`sd_backend.py` 의 A1111Backend, `lib/a1111_*` 모듈) 전부 삭제.
- `/api/sd/catalog/*` 410 Gone.
- env `SD_HOST` 등 a1111 변수 제거.
- ComfyUI multi-host (load balancing) 검토.
