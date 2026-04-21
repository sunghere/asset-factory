# Asset Factory

게임 / 픽셀아트 에셋을 **AI 생성 → 자동 검증 → 사람 리뷰 → 승인본 내보내기** 흐름으로 일괄 관리하는 FastAPI 서비스입니다. LLM(에이전트)은 “어떤 에셋이 필요한지”만 JSON 스펙으로 넘기고, 이후 SD(Stable Diffusion) WebUI 호출 / 픽셀 / 색 수 / 알파 검증 / 큐 / 이력 / 후보 슬롯 / 내보내기까지는 이 서비스가 담당합니다.

대상 SD 백엔드: **AUTOMATIC1111 WebUI** (`/sdapi/v1/...`). 환경변수 `SD_HOST`로 지정합니다.

---

## 핵심 컨셉

```
LLM → POST /api/generate(/batch)        ── 스펙(JSON)만 보냄
        │
        ▼
Asset Factory
  ├─ SD WebUI 호출 (seed/model/prompt 관리)
  ├─ validate-asset (해상도/색 수/알파 등 검증)
  ├─ DB(SQLite)에 에셋/잡/후보/이력 저장
  ├─ 정적 UI(/)에서 사람이 승인/리젝/검증/재생성
  ├─ 후보 N장 중 선택 → 메인 에셋 교체(이전 버전은 history)
  └─ 승인본 내보내기(파일 + manifest.json)
        │
        ▼
LLM은 GET /api/jobs/{id} 만 polling (토큰 최소)
```

---

## v0.1 워크플로우: agent-enqueue, human-cherry-pick

“에이전트가 밤새 N개 모델 × M개 LoRA × K개 시드 = 100~200장을 enqueue하고,
다음 날 아침 사람은 cherry-pick UI에서 1장만 골라 승인” 워크플로우를 1급 시민으로 지원합니다.

```
[Cursor / 다른 LLM 에이전트]
    │  (브리프 → guiding prompt 생성은 클라이언트 LLM이 담당)
    │
    ▼
POST /api/mcp/design_asset    POST /api/batches
    │                              │
    └──── spec(JSON: prompts × models × loras × seeds) ──→ batch_id 반환
                                   │
                                   ▼
                  generation_tasks 곱집합 expand
                                   │
                                   ▼
                  generation_worker (지수 백오프, 디스크 가드)
                                   │
                                   ▼
                  asset_candidates (batch_id 묶음, 슬롯 단위)
                                   │
                                   ▼
                  /cherry-pick UI (그리드 + 큰 미리보기 + 비교)
                                   │  키보드: j/k/x/v/c/i/Enter
                                   ▼
                  POST /api/batches/{id}/candidates/{cid}/reject   (즉시 GC 우선순위)
                  POST /api/assets/approve-from-candidate          (메인 승격, 이전 버전은 history)
```

핵심 차별점:

- **에이전트 시간 ≠ 사람 시간**: 생성은 비동기 큐, 사람은 다음 날 cherry-pick만 한다.
- **곱집합 spec**: prompts × models × loras × seeds 한 번에 enqueue → 일관성 비교가 쉽다.
- **rejected 우선 GC**: 사람이 `x` 누르면 `is_rejected=1` → GC 루프가 가장 먼저 디스크를 회수한다.
- **승인 = 이력 보존**: cherry-pick으로 메인을 교체해도 이전 버전은 `asset_history`에 자동 스냅샷된다.

---

## 주요 기능

- **단일/배치 생성**: `POST /api/generate`, `POST /api/generate/batch` (스펙 파일 또는 inline JSON)
- **후보 N장 (`candidates_per_asset`)**: 슬롯별로 저장 → UI에서 선택해 메인으로 승격
- **자동 재생성**: 기존 에셋의 `seed/model/steps/cfg/sampler/negative/expected_size/max_colors/max_retries` 메타데이터 보존
- **에셋 이력**: 메인 교체 시 이전 버전 스냅샷을 `asset_history`에 적재 → `GET /api/assets/{id}/history`
- **검증 일괄 워크플로우**: FAIL 자산만 일괄 재검증 / 일괄 재생성
- **스캔**: 기존 디렉토리를 읽어 에셋 DB로 동기화 (`POST /api/projects/scan`)
- **내보내기 + manifest**: 승인본을 `project/category/asset_key.png`로 복사하고 `asset-manifest.json`에 sha256까지 기록
- **운영 안정성**:
  - 디스크 여유 가드 (`MIN_FREE_DISK_MB`, 기본 50MB) — 생성/내보내기 전 검사, 부족 시 **507**
  - 후보 디렉토리 GC: 보관일(`CANDIDATE_GC_MAX_AGE_DAYS`, 기본 7일) + 용량(`CANDIDATE_GC_MAX_BYTES_GB`, 기본 1GB), 주기 `CANDIDATE_GC_INTERVAL_SEC`(기본 1시간)
  - SD 오류 분류: `timeout` / `oom` / `unreachable` / `sd_server_error` / `sd_client_error`
- **API Key**: `.env`의 `API_KEY` 설정 시 변경 계열 엔드포인트(`POST/PATCH/DELETE`)에 `x-api-key` 필요
- **SSE 이벤트**: `GET /api/events`로 작업/검증/내보내기/후보 선택 이벤트 스트림
- **React+Babel SPA (빌드 도구 없음)**: `/` 는 `/app/` 으로 리다이렉트, SPA 셸은 `static/app/index.html`

---

## 기술 스택

- Python 3.11+ / FastAPI / Uvicorn / aiosqlite / aiohttp / Pillow
- SQLite (단일 파일, `data/asset-factory.db`)
- 외부: AUTOMATIC1111 SD WebUI

---

## 빠른 시작

### 1) 설치

```bash
git clone <repo-url> asset-factory
cd asset-factory
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2) 환경변수 (`.env` 권장)

```env
SD_HOST=192.168.50.225:7860       # AUTOMATIC1111 WebUI 호스트
API_KEY=                          # 비워두면 인증 없이 동작
ASSET_FACTORY_DATA_DIR=           # 기본: ./data (mock 분리 실행 시 별도 루트)
ASSET_FACTORY_DB_PATH=            # 기본: $ASSET_FACTORY_DATA_DIR/asset-factory.db
ASSET_FACTORY_EXPORT_ROOT=        # 기본: ~/workspace/assets
ASSET_FACTORY_MOCK_MODE=0         # 1이면 시작 시 mock 배너 로그 출력
MIN_FREE_DISK_MB=50               # 생성/내보내기 전 디스크 가드
CANDIDATE_GC_MAX_AGE_DAYS=7
CANDIDATE_GC_MAX_BYTES_GB=1
CANDIDATE_GC_INTERVAL_SEC=3600
```

### 3) 실행

```bash
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

브라우저로 `http://localhost:8000` 접속.

### 3-1) 전체 화면 mock 환경 (격리 DB/파일)

```bash
./scripts/run_ui_mock_env.sh
```

이 명령은 `data/mock-ui` 루트에 분리 DB/후보/승인/export를 만들고, 서버를 mock 모드로 띄운 뒤 화면 검증용 시드(`ui-mock` 프로젝트)를 주입합니다. 체크 포인트는 `docs/UI_MOCK_CHECKLIST.md`를 참고하세요.

### 4) SD 헬스체크

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/health/sd
```

### 5) Isolated UI mock mode (all screens)

Use a fully isolated DB/data root for spec-level UI review:

```bash
chmod +x scripts/run_ui_mock_env.sh
scripts/run_ui_mock_env.sh
```

Default mock roots:

- DB: `data/mock-ui/asset-factory.db`
- Data: `data/mock-ui/`
- Export: `data/mock-ui/export`

The runner script seeds deterministic data for all `/app/*` routes, then starts
the server on `http://127.0.0.1:18000`.
Detailed visual QA steps: `docs/UI_MOCK_CHECKLIST.md`.

---

## 사용 예시

### 단일 생성

```bash
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "project": "cat-raising",
    "asset_key": "ksh_baby_idle",
    "category": "character",
    "prompt": "pixel art, cute orange tabby kitten, idle pose, transparent background",
    "expected_size": 64,
    "max_colors": 32
  }'
```

### 배치 생성 (스펙 파일 기반)

`specs/cat-raising.json` 형태의 스펙을 두고:

```bash
curl -X POST http://localhost:8000/api/generate/batch \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"spec_id": "cat-raising"}'
```

스펙에 `"generation_config.candidates_per_asset": 4`를 두면 자산마다 4장 후보를 생성하고, UI 상세 패널에서 슬롯을 골라 메인으로 적용할 수 있습니다.

### 승인본 내보내기

```bash
curl -X POST http://localhost:8000/api/export \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{"project": "cat-raising", "save_manifest": true}'
```

결과물: `~/workspace/assets/<project>/<category>/<asset_key>.png` + `asset-manifest.json`.

---

## API 개요

### 헬스 / 메타
- `GET /api/health` — 서비스 상태
- `GET /api/health/sd` — SD WebUI 연결 점검
- `GET /api/projects` — `specs/*.json` 목록
- `GET /api/projects/{project_id}/spec` — 스펙 원본
- `GET /api/projects/{project_id}/assets` — 프로젝트 에셋 목록 (`status`, `category`, `validation_status` 필터)

### 생성 / 잡
- `POST /api/generate` — 단일 생성 큐잉
- `POST /api/generate/batch` — 스펙 기반 배치 큐잉 (`spec` 또는 `spec_id`)
- `GET /api/jobs/{job_id}` — 작업 상태
- `GET /api/jobs/recent?limit=10` — 최근 작업 타임라인
- `GET /api/events` — SSE 이벤트 스트림

### 에셋
- `GET /api/assets?project=&status=&category=&validation_status=`
- `GET /api/assets/summary?project=` — 상태/검증/카테고리 집계
- `GET /api/assets/{id}/image` — 원본 이미지 파일
- `GET /api/assets/{id}/detail` — 메타데이터 단건
- `GET /api/assets/{id}/history` — 이전 버전 스냅샷 목록
- `GET /api/assets/{id}/candidates?job_id=` — 후보 슬롯 목록
- `GET /api/asset-candidates/image?project=&asset_key=&job_id=&slot_index=` — 후보 슬롯 파일
- `PATCH /api/assets/{id}` — `status`(`approved` / `rejected` / `pending`) 변경
- `POST /api/assets/{id}/regenerate` — 기존 메타데이터 보존 재생성
- `POST /api/assets/{id}/select-candidate` — 후보 슬롯을 메인으로 승격(이전 메인은 history에 저장)

### 검증 / 일괄
- `POST /api/validate/{id}` — 단일 재검증
- `POST /api/validate/all?project=` — 전체 재검증
- `POST /api/batch/revalidate-failed?project=` — FAIL 자산 일괄 재검증
- `POST /api/batch/regenerate-failed?project=` — FAIL 자산 일괄 재생성 큐잉

### 스캔 / 내보내기
- `POST /api/projects/scan` — 디렉토리 → DB 동기화
- `POST /api/export` — 승인본 복사 + 선택적 manifest 저장
- `GET /api/export/manifest?project=` — 현재 승인본 manifest 미리보기

### v0.1 — 카탈로그 / 배치 / 체리픽
- `GET /api/sd/catalog/models` — A1111의 모델 목록을 `config/sd_catalog.yml` 메타와 머지
- `GET /api/sd/catalog/loras` — A1111의 LoRA 목록을 `config/sd_catalog.yml` 메타와 머지(`weight_default`/`weight_range`/`tags`)
- `POST /api/batches` — `prompts × models × loras × seeds` spec을 곱집합으로 `generation_tasks`에 enqueue, `batch_id` 반환
- `POST /api/mcp/design_asset` — 위와 동일한 enqueue를 MCP 친화적인 응답 포맷으로 노출
- `GET /api/batches?since=<ISO8601>` — 최근 batch 목록 (어제 밤 돌린 묶음 빠르게 찾기)
- `GET /api/batches/{id}/candidates` — batch 내 모든 후보(이미지 URL + 메타 포함)
- `POST /api/batches/{id}/candidates/{cid}/reject` — 사람이 X 친 후보를 즉시 GC 우선 대상으로 마킹
- `POST /api/assets/approve-from-candidate` — 후보를 메인 에셋으로 승격(기존 메인은 `asset_history`에 스냅샷)

> 변경(POST/PATCH) 계열은 `API_KEY`가 설정돼 있으면 `x-api-key` 헤더가 필요합니다.

---

## 데이터 / 파일 레이아웃

```
asset-factory/
├── server.py              # FastAPI 앱 (라우트, 워커, lifespan, GC 루프)
├── models.py              # SQLite 스키마/쿼리 (Database 클래스)
├── generator.py           # SDClient(txt2img), 후보 슬롯 저장, SDError 분류
├── validator.py           # 이미지 메타/색상/알파 검증
├── scanner.py             # 디렉토리 → 에셋 후보 추출
├── candidate_gc.py        # 후보 디렉토리 GC (보관일/용량)
├── specs/                 # 프로젝트 스펙 JSON
│   └── cat-raising.json
├── static/                # SPA (index.html / app.js / style.css)
├── data/
│   ├── asset-factory.db   # SQLite (gitignored)
│   └── candidates/        # 생성 결과 + 후보 슬롯
│       └── <project>/<asset_key>/<job_id>/slot_<n>.png
└── tests/                 # pytest
```

### DB 테이블 요약

- `jobs` — 단일/배치/재생성 작업 단위
- `generation_tasks` — 잡 안의 큐 항목 (재시도, 후보 슬롯 정보 포함)
- `assets` — 프로젝트별 메인 에셋 (`UNIQUE(project, asset_key)`)
- `asset_candidates` — 후보 슬롯 (`UNIQUE(project, asset_key, slot_index, job_id)`)
- `asset_history` — 메인 교체 시 이전 버전 스냅샷 (버전 번호 자동 증가)

---

## 테스트

```bash
.venv/bin/python -m pytest tests/ -q
```

다루는 시나리오:

- 인증 정책 (`test_auth_policy.py`)
- 배치 생성 / 스펙 파서 (`test_generate_batch.py`, `test_spec_parser.py`)
- 스캔 흐름 / 스캐너 (`test_scan_flow.py`, `test_scanner.py`)
- 내보내기 / manifest (`test_export_flow.py`)
- 잡 타임라인 / 요약 (`test_jobs_timeline.py`, `test_models_summary.py`)
- 전체 재검증 (`test_validate_all.py`)
- **에셋 이력 적재 / API** (`test_asset_history.py`)
- **후보 슬롯 선택 / 프로젝트별 조회** (`test_candidate_selection.py`)
- **후보 GC 보관일·용량 정책** (`test_gc_policy.py`)
- **SD 에러 코드 분류** (`test_error_mapping.py`)
- **v0.1 batch 곱집합 expand + batch_id 부여** (`test_batch_expand.py`)
- **v0.1 카탈로그 API: A1111 mock + YAML merge** (`test_catalog_api.py`)
- **v0.1 cherry-pick e2e: list → reject → approve → asset_history** (`test_cherry_pick_flow.py`)

---

## 운영 메모

- **API Key**: `.env`의 `API_KEY`가 있으면 변경 계열에 필수, 없으면 모두 공개. SSE(`/api/events`)는 현재 인증 없이 열려 있습니다.
- **GC**: 서비스 시작 후 5초 뒤부터 주기 실행. 선택된 후보도 GC 대상에 포함되므로, 보관 정책을 길게 잡거나 따로 백업하세요. `GET /api/system/gc/status`로 마지막 실행 결과(`deleted_files`, `freed_bytes`, `scanned_files`, `last_run_at`, `last_error`, `run_count`)를 확인하고, 운영 디버깅 시 `POST /api/system/gc/run`으로 즉시 1회 실행할 수 있습니다(`API_KEY` 보호).
- **디스크 여유**: `MIN_FREE_DISK_MB` 미만이면 생성/내보내기에서 `RuntimeError → 500/507`로 응답합니다.
- **SD 실패 분류**: `SDError.code`가 `timeout`, `oom`, `unreachable`, `sd_server_error`, `sd_client_error` 중 하나로 노출되어 `task.last_error`에 기록됩니다.
- **재시도 백오프**: 재시도 가능한 SD 오류는 task-level 지수 백오프(2^n + 25% 지터, `TASK_BACKOFF_BASE_SEC`/`TASK_BACKOFF_MAX_SEC`로 조정)로 다음 시도가 예약되며, `oom`/`sd_client_error` 같은 비복구 오류는 즉시 `failed`로 처리됩니다. 워커는 다음 due 시각까지(최대 2초) 잠들어 폴링 비용을 줄입니다.
- **SSE keep-alive**: `/api/events`는 idle 상태에서 `SSE_KEEPALIVE_SEC`(기본 15초)마다 `: keep-alive` 코멘트 프레임을 보내고 `X-Accel-Buffering: no` 헤더로 프록시 버퍼링을 차단합니다.
- **로컬 모델 변경**: 단일/스펙 요청에서 `model_name`을 보내면 `override_settings.sd_model_checkpoint`로 SD에 전달.
- **재현성**: 재생성 시 기존 에셋의 `metadata_json`을 그대로 사용하므로, 동일 프롬프트/스텝/CFG/샘플러로 다시 만들 수 있습니다.

---

## SD 카탈로그 메타데이터 (`config/sd_catalog.yml`)

A1111이 알려주는 모델/LoRA 목록은 **이름만** 알 수 있어서, 어느 모델이 픽셀아트에 강한지·LoRA 권장 weight가 얼마인지 같은 *현장 노하우* 는 사람이 직접 적어둬야 합니다. Asset Factory는 `config/sd_catalog.yml` 파일에 그 메타를 보관하고, `/api/sd/catalog/*` 응답에 머지해 에이전트가 spec을 짤 때 참고할 수 있도록 합니다.

```yaml
# config/sd_catalog.yml
models:
  pixelart_xl_v1.5:                # ← A1111 model_name과 동일 (대소문자 무시, .safetensors 제거)
    tags: [pixel_art, character, sdxl]
    notes: "도트 캐릭터 idle/walk에 강함. 배경엔 약함. 권장 size 1024x1024."
  deliberate_v3:
    tags: [general, character, sd1.5]
    notes: "SD1.5 기반 범용. 캐릭터/풍경 모두 무난. 권장 size 512x512."

loras:
  marine_uniform_v2:               # ← A1111 lora name과 동일
    weight_default: 0.7            # spec에서 weight 생략 시 기본값
    weight_range: [0.4, 1.0]       # UI 슬라이더용 권장 범위
    tags: [character, military, uniform]
    notes: "weight 0.6 이하로 가면 군복 디테일 사라짐. SDXL 호환."
  sad_face_v1:
    weight_default: 0.5
    weight_range: [0.3, 0.8]
    tags: [face, expression]
    notes: "표정 LoRA. 다른 캐릭터 LoRA와 stack 가능."
```

규칙:

- 키 매칭은 **lowercase + 확장자(`.safetensors`/`.ckpt`/`.pt`) 제거** 기준입니다. A1111 UI에 보이는 이름을 그대로 적되 케이스만 맞추면 됩니다.
- YAML에 없는 모델/LoRA도 응답에는 포함되지만 `has_metadata: false`, `weight_default: 0.7`, `weight_range: [0.0, 1.0]` 같은 안전 기본값으로 채워집니다.
- 파일이 없거나 비어 있어도 서버는 정상 동작합니다(에이전트가 메타 없이 spec을 짜야 할 뿐).

---

## Cursor / MCP 클라이언트 연결 가이드

`POST /api/mcp/design_asset` 가 MCP wrapper입니다. Cursor 같은 클라이언트에서 LLM이 brief → spec 변환을 담당하고, 변환된 spec을 이 한 엔드포인트로 보내면 batch가 enqueue됩니다.

권장 흐름:

1. 클라이언트 LLM이 사용자 brief(예: "marine 캐릭터 idle 8장")를 받는다.
2. 클라이언트 LLM이 `GET /api/sd/catalog/models`, `GET /api/sd/catalog/loras` 로 카탈로그를 받아 어떤 모델/LoRA를 쓸지 결정한다.
3. 클라이언트 LLM이 spec(JSON: `prompts × models × loras × seeds`)을 만들어 `POST /api/mcp/design_asset` 으로 enqueue.
4. 응답에서 `batch_id`를 받아 사용자에게 "내일 아침 `/cherry-pick?batch=<id>` 에서 골라주세요"라고 안내한다.

`POST /api/mcp/design_asset` 요청 예시:

```json
{
  "project": "cat-raising",
  "asset_key": "marine_v2_idle",
  "category": "character",
  "prompts": ["pixel art marine character idle pose, transparent background"],
  "models": ["pixelart_xl_v1.5", "deliberate_v3"],
  "loras": [
    {"name": "marine_uniform_v2", "weight": 0.7},
    {"name": "sad_face_v1", "weight": 0.5}
  ],
  "seeds_per_combo": 8,
  "common": {"steps": 28, "cfg_scale": 7.0, "expected_size": 1024, "max_colors": 64}
}
```

응답 (`structuredContent`) 에는 `batch_id`, `job_id`, `expanded_count`, 거친 `estimated_eta_seconds`가 들어 있고, MCP 클라이언트는 `content[0].text`에서 `batch_id=...` 한 줄 요약을 볼 수 있습니다. 사용자에게는 `/cherry-pick?batch=<batch_id>` 로 안내하면 됩니다. `seeds_per_combo`는 64까지 (서버에서 검증), `seeds`를 명시하면 그 시드들이 그대로 쓰입니다.

### Dogfooding 스크립트

`scripts/dogfood_marine_v2.sh` 를 실행하면 위와 동일한 spec(2 모델 × 2 LoRA × 25 seed = 100장)을 enqueue하고 cherry-pick URL을 출력합니다. `seeds_per_combo`가 25라 batch당 100 task가 만들어지므로 v0.1 “100장→1장” 시나리오를 그대로 재현할 수 있습니다.

```bash
AF_HOST=http://localhost:8000 API_KEY=$API_KEY ./scripts/dogfood_marine_v2.sh
```

측정은 사람 손 시간 기준입니다: cherry-pick URL을 연 시각 → 1장 승인 시각이 5분 이하면 wedge 검증 PASS.

API_KEY가 설정된 경우 `x-api-key` 헤더를 동일하게 보내면 됩니다.

#### v0.1.5 morning session (200장 × 5 batches)

`scripts/dogfood_morning_session.sh` 는 v0.1.5 cherry-pick UI의 "오늘 큐 → spacer → 자동 다음 batch" 흐름을 dogfood하기 위한 다중 batch enqueue 스크립트입니다. 5개 batch (100 + 40 + 40 + 20 + 20 = 220장)를 한꺼번에 큐에 넣어 morning session을 시뮬레이션합니다.

```bash
AF_HOST=http://localhost:8000 API_KEY=$API_KEY ./scripts/dogfood_morning_session.sh
```

목표: 다음 날 아침 cherry-pick UI를 열고 "오늘 cherry-pick 완료" 화면이 뜰 때까지 약 30분 (1장당 ~9초). 측정 결과는 아래 표에 누적 기록합니다.

| 측정일 | 총 장수 | batches | T2 - T1 (분) | rejected | approved | undo 사용 | 메모 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| _YYYY-MM-DD_ | 220 | 5 | _TBD_ | _TBD_ | _TBD_ | _TBD_ | 첫 dogfood |

---

## 보안 (Path 정화 / CodeQL 운영 가이드)

사용자 입력에서 유래한 모든 파일 시스템 경로(스캔 루트, DB의 `image_path`, 후보 슬롯 쿼리, export 출력 디렉토리)는 [`server._ensure_path_allowed`](server.py)를 통해 **허용 루트 기반 정화(sanitizer)** 를 거친 뒤 사용합니다. 허용 루트는 기본값(`DATA_DIR`, `~/workspace/assets`)에 환경변수 `ASSET_FACTORY_ALLOWED_ROOTS`(콜론 구분)로 추가할 수 있고, 모든 file-serving / copy 동작은 정화 함수의 *반환 값*(`resolved`)을 사용해 path traversal을 차단합니다. 경로 세그먼트는 `_safe_segment`로 `/`, `\`, `..`을 제거합니다. CodeQL `py/path-injection` 알람이 이 함수 이후의 데이터 흐름에서 잔존하는 경우는 사용자 정의 sanitizer를 정적 분석이 인식하지 못한 false positive로 분류하고, GitHub Security 탭에서 "Won't fix"로 정리합니다. 새로 추가하는 엔드포인트는 반드시 (a) 입력 경로에 `_ensure_path_allowed`를 호출하고 (b) 반환된 `Path` 객체만 file open / copy / serve에 사용해야 합니다.

---

## 라이선스 / 상태

내부 도구 / WIP. 외부 공개 시 라이선스 파일을 별도로 추가하세요.
