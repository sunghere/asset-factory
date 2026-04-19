# Asset Factory — 핸드오프 문서

> **대상:** hermes 프로파일 (개발-운영 전담)
> **작성일:** 2025-01-20
> **목적:** Asset Factory 서비스 구현을 위한 컨텍스트 전달

---

## 1. 프로젝트 개요

**Asset Factory**는 게임 에셋 생성 파이프라인을 완전 자동화하는 서비스다.

- LLM(에이전트)이 **에셋 요구사항 JSON만 작성**하면, 서비스가 **SD 생성 → 검증 → 갤러리 → 승인/리젝 → 재생성**을 전부 자동 처리
- 기존: 에이전트가 bash로 `generate-asset` 직접 호출 → **REST API 서비스로 전환**
- 핵심 가치: **토큰 비용 0**으로 에셋 파이프라인 운영 (에이전트는 JSON 제출 + polling만)

```
LLM → POST /api/generate/batch (에셋 스펙 JSON)
       ↓
Asset Factory가 전부 처리
       ↓
사람이 웹 UI에서 리뷰 (승인/리젝)
       ↓
LLM ← GET /api/jobs/{id}/status (polling, 토큰 ≈ 0)
```

---

## 2. 기존 인프라 정보

### 로컬 머신 (개발/서비스 호스트)
| 항목 | 값 |
|------|-----|
| 장비 | Mac mini (16GB RAM, Apple Silicon) |
| OS | macOS |
| LAN IP | `192.168.50.95` |
| Python | `/opt/homebrew/bin/python3.11` (homebrew) |

### Stable Diffusion 서버
| 항목 | 값 |
|------|-----|
| 주소 | `192.168.50.225:7860` |
| 위치 | LAN 내 Windows 머신 |
| 소프트웨어 | AUTOMATIC1111 WebUI v1.10.1 |
| GPU | RTX 4080 SUPER (16GB VRAM) |

### 설치된 SD 모델
| 모델명 | 용도/비고 |
|--------|-----------|
| `pixelArtDiffusionXL_spriteShaper` | 픽셀아트 SDXL — 메인 픽셀아트 모델 |
| `ponyDiffusionV6XL` | SDXL 기반 |
| `dreamshaper_8` | SD 1.5 범용 |
| `meinamix_v12Final` | SD 1.5 애니메이션 |
| `v1-5-pruned-emaonly` | SD 1.5 베이스 |

### 도구
| 도구 | 경로/설정 |
|------|-----------|
| Aseprite CLI | `~/workspace/tools/aseprite/build/bin/Aseprite.app/Contents/MacOS/aseprite -b` |
| generate-asset | `~/.local/bin/generate-asset` (SD+Aseprite 래퍼 스크립트) |
| validate-asset | `~/.local/bin/validate-asset` (기술 검증 스크립트) |

### 저장소/플랫폼
| 항목 | 경로/주소 |
|------|-----------|
| 공용 에셋 저장소 | `~/workspace/assets/` |
| 에셋 설정 Lock | `~/workspace/assets/.asset-config.json` |
| Paperclip | `localhost:3100` (에이전트 관리 플랫폼) |

---

## 3. 설계 문서 참조

> 📄 **전체 설계:** `~/workspace/asset-factory/DESIGN.md`

### 핵심 아키텍처
- **FastAPI** 서버 + **SQLite** (파일 DB) + **단일 HTML** 프론트엔드
- 포트: **3200** (LAN 접속 가능하게 `0.0.0.0` 바인드)

### 디렉토리 구조 (설계 기준)
```
asset-factory/
├── server.py              # FastAPI 메인
├── models.py              # DB 모델 (SQLite)
├── generator.py           # SD API 호출 + Aseprite 후처리
├── validator.py           # validate-asset 로직 (내장)
├── scanner.py             # 디렉토리 스캔 → 에셋 목록화
├── specs/                 # 프로젝트별 에셋 스펙 JSON
│   └── cat-raising.json
├── static/                # 프론트엔드
│   ├── index.html         # SPA
│   ├── app.js
│   └── style.css
└── data/
    └── asset-factory.db   # SQLite (상태 관리)
```

### API 엔드포인트 요약
- `POST /api/generate` — 단일 에셋 생성
- `POST /api/generate/batch` — 스펙 기반 배치 생성
- `GET /api/jobs/{id}` — 작업 상태
- `GET /api/assets` — 갤러리 (필터: project, status, category)
- `PATCH /api/assets/{id}` — 승인/리젝
- `POST /api/assets/{id}/regenerate` — 재생성
- `POST /api/export` — 승인 에셋 내보내기

> 전체 API 설계는 DESIGN.md에 상세히 기술되어 있음

---

## 4. 기존 에셋 현황

### 고양이 키우기 프로젝트
- pixel-data JSON + sprites가 이미 생성되어 있음

| 위치 | 경로 |
|------|------|
| Head of Design 워크스페이스 | `~/.paperclip/instances/default/workspaces/b63b0c0b-4d3b-4b19-bba6-3b0b7ace8b96/assets/` |
| 공용 저장소 | `~/workspace/assets/sprites/cats/` |

### 시드 Lock 현황
`.asset-config.json`에 3개 캐릭터 시드가 Lock 됨:

| 캐릭터 ID | 설명 |
|-----------|------|
| `ksh` | 코숏 (Korean Shorthair) |
| `rbl` | 러시안블루 (Russian Blue) |
| `mck` | 먼치킨 (Munchkin) |

> ⚠️ 시드 Lock된 캐릭터는 동일 시드로 생성해야 일관성 유지

---

## 5. 구현 우선순위 (MVP)

### Phase 1: 코어 서비스 ← **먼저 구현**
- [ ] FastAPI 서버 기본 구조 (`server.py`)
- [ ] SD API 연동 — `txt2img` + `img2img` (`generator.py`)
- [ ] `validate-asset` 로직 내장 (`validator.py`)
- [ ] SQLite로 에셋 상태 관리 (`models.py`)
- [ ] 헬스체크: SD 서버 연결 확인 API

### Phase 2: 웹 UI
- [ ] 단일 HTML 갤러리 페이지 (`static/index.html`)
- [ ] 에셋 그리드 뷰 (카테고리별 그룹핑)
- [ ] 승인 / 리젝 / 재생성 버튼
- [ ] 이미지 서빙 (`/api/assets/{id}/image`)
- [ ] 에셋 상세 패널 (메타데이터, 생성 이력)

### Phase 3: 배치 생성 + 스펙 연동
- [ ] 에셋 스펙 JSON 파서 (`specs/`)
- [ ] 배치 생성 큐 (비동기 처리)
- [ ] `img2img` 기반 변형 생성
- [ ] 프로젝트 내보내기 (`/api/export`)
- [ ] 기존 디렉토리 스캔 → 에셋 자동 인식 (`scanner.py`)

---

## 6. 기술 결정

| 항목 | 선택 | 이유 |
|------|------|------|
| 런타임 | Python 3.11 (`/opt/homebrew/bin/python3.11`) | 이미 설치됨 |
| 웹 프레임워크 | FastAPI + uvicorn | 비동기, 자동 API 문서 |
| DB | SQLite (파일 DB) | 별도 서버 불필요, 단일 인스턴스 |
| 프론트엔드 | 순수 HTML + JS (또는 Jinja2) | 빌드 파이프라인 없음 |
| 이미지 처리 | Pillow | 메타데이터만 (내용 분석 안 함) |

### 의존성 (예상)
```
fastapi
uvicorn[standard]
aiohttp          # SD API 비동기 호출
aiosqlite        # SQLite 비동기
pillow           # 이미지 메타데이터
python-multipart # 파일 업로드
```

---

## 7. 제약 사항

### 🚫 절대 하지 말 것
1. **이미지를 AI로 분석하지 않음** — 토큰 낭비 방지. 메타데이터(크기, 색상수, 포맷)만 체크
2. **Aseprite 심링크로 실행 금지** — `gui.xml` 크래시. 반드시 전체 경로 사용:
   ```bash
   ~/workspace/tools/aseprite/build/bin/Aseprite.app/Contents/MacOS/aseprite -b
   ```

### ⚠️ 주의 사항
3. **SD 서버 접속 불가 대비** — IP 고정(`192.168.50.225`)이지만 Windows 머신이 꺼져있을 수 있음 → 타임아웃 + 재시도 + 명확한 에러 메시지 필수
4. **SDXL 모델 해상도** — `1024x1024` 네이티브. `512x512` 넣으면 품질 심각하게 저하
5. **SD 1.5 모델** — `512x512` 네이티브. 모델별 적정 해상도 분기 필요

---

## 8. 연동 포인트

### Paperclip Agent → Asset Factory
```
POST /api/generate/batch
Content-Type: application/json

{에셋 스펙 JSON — DESIGN.md의 스키마 참조}
```

### 생성 완료 알림
- **디스코드 웹훅**: 환경변수 `DISCORD_ASSET_WEBHOOK`
- 생성/승인/내보내기 완료 시 알림 전송

### 에셋 저장 경로
```
~/workspace/assets/           # 최종 저장소
  ├── sprites/cats/           # 프로젝트별 하위 디렉토리
  └── ...
```
- 완료된 에셋은 `~/workspace/assets/`에 저장
- 프로젝트 디렉토리에 심링크 생성

---

## 9. 스킬/참조 문서

| 문서 | 경로 | 설명 |
|------|------|------|
| SD API 스킬 | `~/.hermes/skills/mlops/stable-diffusion-api/SKILL.md` | SD WebUI API 전체 레퍼런스 |
| 설계 문서 | `~/workspace/asset-factory/DESIGN.md` | 전체 설계, API, UI 와이어프레임 |
| generate-asset | `~/.local/bin/generate-asset` | 기존 래퍼 스크립트 (참고용) |
| validate-asset | `~/.local/bin/validate-asset` | 기존 검증 스크립트 (참고용) |
| 에셋 설정 | `~/workspace/assets/.asset-config.json` | 시드 Lock, 모델 설정 |

---

## 10. 첫 세션 체크리스트

시작할 때 아래 순서로 진행 권장:

1. **DESIGN.md 정독** — 전체 설계 파악
2. **SD 서버 연결 테스트** — `curl http://192.168.50.225:7860/sdapi/v1/sd-models`
3. **기존 스크립트 분석** — `generate-asset`, `validate-asset` 코드 리딩
4. **SD API 스킬 읽기** — `~/.hermes/skills/mlops/stable-diffusion-api/SKILL.md`
5. **Phase 1 구현 시작** — `server.py` + `generator.py` + `validator.py` + `models.py`

### 빠른 SD 연결 테스트
```bash
# 모델 목록
curl -s http://192.168.50.225:7860/sdapi/v1/sd-models | python3 -m json.tool

# 간단한 txt2img 테스트
curl -s -X POST http://192.168.50.225:7860/sdapi/v1/txt2img \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"test","steps":1,"width":64,"height":64}' | python3 -c "
import json, base64, sys
data = json.load(sys.stdin)
img = base64.b64decode(data['images'][0])
with open('/tmp/sd-test.png','wb') as f: f.write(img)
print('OK:', len(img), 'bytes')
"
```

---

> 💡 **핵심 원칙:** 이 서비스는 "에이전트의 토큰 소비 없이 에셋 파이프라인을 돌린다"가 존재 이유다.
> 에이전트는 JSON 한 번 던지고, 사람이 UI에서 리뷰하고, 결과만 polling하면 된다.
