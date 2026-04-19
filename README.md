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
- **순수 HTML/JS UI**: `/` (`static/index.html`) — 빌드 도구 없이 바로 동작

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

### 4) SD 헬스체크

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/health/sd
```

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

## 보안 (Path 정화 / CodeQL 운영 가이드)

사용자 입력에서 유래한 모든 파일 시스템 경로(스캔 루트, DB의 `image_path`, 후보 슬롯 쿼리, export 출력 디렉토리)는 [`server._ensure_path_allowed`](server.py)를 통해 **허용 루트 기반 정화(sanitizer)** 를 거친 뒤 사용합니다. 허용 루트는 기본값(`DATA_DIR`, `~/workspace/assets`)에 환경변수 `ASSET_FACTORY_ALLOWED_ROOTS`(콜론 구분)로 추가할 수 있고, 모든 file-serving / copy 동작은 정화 함수의 *반환 값*(`resolved`)을 사용해 path traversal을 차단합니다. 경로 세그먼트는 `_safe_segment`로 `/`, `\`, `..`을 제거합니다. CodeQL `py/path-injection` 알람이 이 함수 이후의 데이터 흐름에서 잔존하는 경우는 사용자 정의 sanitizer를 정적 분석이 인식하지 못한 false positive로 분류하고, GitHub Security 탭에서 "Won't fix"로 정리합니다. 새로 추가하는 엔드포인트는 반드시 (a) 입력 경로에 `_ensure_path_allowed`를 호출하고 (b) 반환된 `Path` 객체만 file open / copy / serve에 사용해야 합니다.

---

## 라이선스 / 상태

내부 도구 / WIP. 외부 공개 시 라이선스 파일을 별도로 추가하세요.
