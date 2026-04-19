# Asset Factory — 설계 문서

## 컨셉

LLM(에이전트)은 "어떤 에셋이 필요한지"만 JSON으로 정의.
서비스가 **생성 → 검증 → 갤러리 표시 → 사람 승인/리젝 → 재생성**을 전부 처리.

```
LLM → POST /api/jobs (에셋 요구사항 JSON)
       ↓
Asset Factory 서비스
  ├─ SD API로 생성 (seed/model/prompt 자동 관리)
  ├─ validate-asset으로 기술 검증
  ├─ 갤러리 UI에 표시
  ├─ 사람이 승인/리젝/메모
  └─ 리젝 시 자동 재생성 (파라미터 조정)
       ↓
결과 → GET /api/jobs/{id}/status
LLM은 polling만 하면 됨 (토큰 최소)
```

## 에셋 요구사항 스키마 (Asset Spec)

```json
{
  "project": "cat-raising",
  "spec_version": "1.0",
  
  "characters": [
    {
      "id": "ksh",
      "name": "코숏 (Korean Shorthair)",
      "character_prompt": "cute fluffy orange tabby cat, big sparkling eyes, round face",
      "stages": [
        {
          "stage": "baby",
          "grid_size": 16,
          "output_size": 64,
          "head_body_ratio": "2:1",
          "actions": ["idle", "eat", "sleep", "play", "happy", "hungry", "sleepy"]
        },
        {
          "stage": "child", 
          "grid_size": 24,
          "output_size": 64,
          "head_body_ratio": "1.5:1",
          "actions": ["idle", "eat", "sleep", "play", "happy", "hungry", "sleepy"]
        },
        {
          "stage": "teen",
          "grid_size": 24,
          "output_size": 64,
          "actions": ["idle", "eat", "sleep", "play", "happy", "hungry", "sleepy"]
        },
        {
          "stage": "adult",
          "grid_size": 32,
          "output_size": 64,
          "head_body_ratio": "1:1.3",
          "actions": ["idle", "eat", "sleep", "play", "happy", "hungry", "sleepy"]
        },
        {
          "stage": "elder",
          "grid_size": 32,
          "output_size": 64,
          "actions": ["idle", "eat", "sleep", "play", "happy", "hungry", "sleepy"]
        }
      ]
    }
  ],
  
  "ui_assets": [
    {"id": "btn_feed", "category": "icon", "size": 32, "prompt_hint": "food bowl icon"},
    {"id": "btn_play", "category": "icon", "size": 32, "prompt_hint": "yarn ball icon"},
    {"id": "btn_shop", "category": "icon", "size": 32, "prompt_hint": "shop bag icon"},
    {"id": "progress_bar_fill", "category": "ui", "size": "auto", "prompt_hint": "health bar fill"},
    {"id": "speech_bubble", "category": "ui", "size": "auto", "prompt_hint": "speech bubble"}
  ],
  
  "backgrounds": [
    {"id": "room_main", "category": "background", "size": "512x256", "prompt_hint": "cozy room interior, warm lighting"}
  ],
  
  "items": [
    {"id": "food_basic", "category": "item", "size": 32, "prompt_hint": "cat food can"},
    {"id": "food_premium", "category": "item", "size": 32, "prompt_hint": "premium fish dish"}
  ],
  
  "generation_config": {
    "model_preset": "pixel",
    "genre_preset": "cat",
    "base_prompt": "pixel art, cute cat sprite, 16-bit, game sprite, transparent background",
    "negative_prompt": "blurry, smooth, 3d render, realistic, anti-aliasing, high resolution photo",
    "gen_size": "512x512",
    "steps": 20,
    "cfg": 7,
    "sampler": "DPM++ 2M",
    "max_colors": 32,
    "candidates_per_asset": 4,
    "sd_host": "192.168.50.225:7860"
  }
}
```

## 서비스 구조

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

## API 설계

### 프로젝트 관리
- `GET  /api/projects` — 스펙 파일 목록
- `POST /api/projects/scan` — 디렉토리 스캔 → 기존 에셋 자동 인식
- `GET  /api/projects/{id}/assets` — 에셋 목록 + 상태

### 에셋 생성
- `POST /api/generate` — 단일 에셋 생성 (후보 N장)
- `POST /api/generate/batch` — 스펙 기반 전체 배치 생성
- `GET  /api/jobs/{id}` — 생성 작업 상태

### 에셋 리뷰
- `GET  /api/assets` — 전체 에셋 갤러리 (필터: project, status, category)
- `GET  /api/assets/{id}/image` — 이미지 서빙
- `PATCH /api/assets/{id}` — 상태 변경 (approved/rejected/pending)
- `POST /api/assets/{id}/regenerate` — 리젝된 에셋 재생성
- `GET  /api/assets/{id}/history` — 생성 이력 (이전 버전들)

### 검증
- `POST /api/validate/{id}` — 단일 에셋 검증
- `POST /api/validate/all` — 전체 검증

### 내보내기
- `POST /api/export` — 승인된 에셋만 프로젝트 디렉토리에 복사
- `GET  /api/export/manifest` — asset-manifest.json 생성

## 디렉토리 자동 인식 로직 (scanner.py)

아무 디렉토리를 던져도 에셋으로 인식:

```python
def scan_directory(root_path):
    """
    디렉토리 구조를 자동 분석하여 에셋 목록 생성
    
    인식 패턴:
    1. sprites/{category}/{variant}/*.png
    2. ui/**/*.png  
    3. backgrounds/*.png
    4. items/*.png
    5. 플랫 구조: *.png (카테고리 = 파일명 파싱)
    
    파일명 패턴 자동 감지:
    - {skin}_{stage}_{action}.png → 캐릭터 스프라이트
    - {name}.png → 단일 에셋
    - {name}_{variant}.png → 변형 에셋
    """
```

## 프론트엔드 (단일 HTML)

```
┌─────────────────────────────────────────────────────┐
│ Asset Factory                          [192.168..:3200] │
├─────────────────────────────────────────────────────┤
│ 프로젝트: [고양이 키우기 ▼]  [+ 디렉토리 추가]         │
│ 필터: [전체|승인|리젝|미검토|생성중] 카테고리: [전체 ▼]  │
├─────────────────────────────────────────────────────┤
│ 📊 총 119개 | ✅ 0 승인 | ❌ 0 리젝 | ⬜ 119 미검토    │
│ ⚠️ 기술검증: 105/119 FAIL (팔레트 초과)               │
├─────────────────────────────────────────────────────┤
│                                                     │
│ ── KSH (코숏) ──────────────────────────────────     │
│ baby:                                               │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ...            │
│ │🐱    │ │🐱    │ │🐱    │ │🐱    │               │
│ │idle  │ │eat   │ │sleep │ │play  │               │
│ │141색 │ │237색 │ │251색 │ │242색 │               │
│ │⬜미검│ │⬜미검│ │⬜미검│ │⬜미검│               │
│ │[✅][❌]│[✅][❌]│[✅][❌]│[✅][❌]│               │
│ └──────┘ └──────┘ └──────┘ └──────┘               │
│                                                     │
│ adult:                                              │
│ ┌──────┐ ┌──────┐                                   │
│ │🐱    │ │🐱    │  ← 945색! 팔레트 심각             │
│ │idle  │ │eat   │                                   │
│ │❌FAIL│ │❌FAIL│  [전체 재생성 --indexed]           │
│ └──────┘ └──────┘                                   │
│                                                     │
│ ── 선택된 에셋 상세 ────────────────────────────     │
│ ksh_baby_idle.png                                   │
│ 크기: 64x64 | 색상: 141 | 포맷: PNG | 투명: ✅       │
│ seed: 42 | model: pixel | prompt: "idle pose..."    │
│ [승인 ✅] [리젝 ❌] [재생성 🔄] [img2img 변형 🎨]    │
│ 리젝 사유: [________________] [재생성 실행]          │
│                                                     │
│ ── 생성 이력 ──                                      │
│ v1: seed=42 → 141색 (현재)                          │
│ v0: seed=-1 → 203색 (자동 폐기)                     │
└─────────────────────────────────────────────────────┘
```

## LLM 연동 시나리오

1. **에이전트가 에셋 스펙 JSON 작성** (한 번만, 토큰 소비 최소)
2. **POST /api/generate/batch** → 서비스가 119개 자동 생성
3. **사람이 UI에서 리뷰** (승인/리젝/메모)
4. **리젝된 것 자동 재생성** (파라미터 조정)
5. **에이전트는 GET /api/jobs/{id}/status만 polling** (토큰 거의 0)
6. **전체 승인 완료 → POST /api/export** → 프로젝트 디렉토리에 복사

## 확장 가능한 구조

- 스펙 JSON만 새로 만들면 **어떤 앱이든 즉시 적용**
- RPG 앱: characters → warrior/mage/archer, items → sword/potion
- 힐링 앱: characters → plants, backgrounds → seasons
- 디렉토리 스캔으로 기존 에셋 자동 인식 → 마이그레이션 비용 0
