# ComfyUI 워크플로우 레지스트리

asset-factory 가 ComfyUI 백엔드로 호출하는 워크플로우 모음. `registry.yml` 이
카테고리·변형·파일 경로·기본값·출력 라벨을 매핑한다.

> ⚠️ **CLI 미구현 — `af workflow ...` 명령은 아직 없음**
>
> 본 문서가 가독성을 위해 사용한 `af workflow catalog` / `af workflow gen` 같은
> 형태는 **계획된 CLI** 의 의도된 모양이며, 실제 레포에는 아직 구현되어 있지
> 않다 (`af.mjs` 부재 — 후속 작업,
> [docs/TODOS.md](../docs/TODOS.md) `cli` 섹션 참조).
>
> **현재 사용법: HTTP API 직접 호출.**
>
> ```bash
> # 카탈로그 조회 (인증 불필요)
> curl http://localhost:8000/api/workflows/catalog | jq
>
> # 생성 호출 (X-API-Key 필요)
> curl -X POST http://localhost:8000/api/workflows/generate \
>      -H "X-API-Key: $AF_API_KEY" \
>      -H "Content-Type: application/json" \
>      -d '{
>        "workflow_category": "sprite",
>        "workflow_variant": "pixel_alpha",
>        "project": "myproj",
>        "asset_key": "ksh_baby_idle",
>        "category": "character",
>        "prompt": "warrior cat",
>        "candidates_total": 1
>      }'
> ```

## 디렉토리 구조

```
workflows/
├── registry.yml                # 매니페스트 (단일 진실원)
├── README.md                   # 이 문서
├── sprite/                     # 게임 캐릭터 (V36/V37/V38)
│   ├── Sprite_Illustrious_Pro_V36_api_*.json
│   ├── Sprite_Illustrious_PoseGuided_V37_api_*.json
│   ├── Sprite_Illustrious_PoseGuided_Alpha_V38_api_*.json    ⭐ 메인
│   └── PoseExtract_V37_api.json
├── illustration/               # 일러스트 (B 시리즈)
│   ├── B1_AnimagineXL_HiRes_V36_api_*.json
│   ├── B2_PrefectPony_HiRes_V36_api_*.json
│   ├── B4_Hyphoria_HiRes_V36_api_*.json
│   ├── B5_AnythingXL_HiRes_V36_api_*.json
│   └── B6_MeinaMix_HiRes_V36_api_*.json
├── pixel_bg/                   # 픽셀 배경 (V35, API 변환 필요)
│   ├── C1_PixelDiffusionXL_V35.json    ← UI 포맷, 변환 대기
│   └── C2_RDXLPixelArt_V35.json
└── icon/                       # 앱 아이콘 (V35, API 변환 필요)
    └── D1_AppIcon_V35.json
```

원본은 `D:\DEV\ComfyUI\comfyuiImage_v34\` 에 있고, 여기에 **복사본**이 들어와
있다. ComfyUI 쪽에서 워크플로우를 수정한 경우 다시 복사 필요 (수동).

## registry.yml 매니페스트 형식

```yaml
version: 1

presets:                            # ${preset:NAME} 으로 defaults 에서 참조
  NEG_PIXEL_SPRITE: |
    (worst quality, low quality:1.4), blurry, jpeg artifacts, ...

categories:
  <category_name>:
    description: "사람이 읽는 설명"
    variants:
      <variant_name>:
        description: "변형 설명"
        file: <category>/<api_format_filename>.json    # 호출 가능 변형
        primary: true                                   # 카테고리 대표
        outputs:
          - { node_title: "Save Stage1", label: stage1 }
          - { node_title: "Save HiRes",  label: hires, primary: true }
        defaults:
          steps: 30
          cfg: 6.5
          sampler: dpmpp_2m
          scheduler: karras
          width: 1280
          height: 640
          pose_image: pose_grid_1x3_mini_2.5h_1280x640.png
          controlnet_strength: 0.85
          negative_prompt: ${preset:NEG_PIXEL_SPRITE}
```

### 필드 의미

- **`file`**: API JSON 경로 (workflows/ 기준 상대). 호출 가능 변형 필수.
- **`ui_file`**: UI 포맷 JSON 경로 (변환 안내용, 호출엔 안 씀)
- **`status`**: `ready` (default) 또는 `needs_api_conversion`. 후자는 file: null.
- **`primary`**: 변형 레벨에서 카테고리 대표 표시 (false 면 자동 fallback)
- **`outputs[*].node_title`**: ComfyUI 워크플로우 안의 SaveImage 노드 `_meta.title` 정확 일치
- **`outputs[*].label`**: cherry-pick UI / extra_outputs metadata 에 노출되는 의미적 라벨
- **`outputs[*].primary`**: 메인 출력 (cherry-pick UI의 대표 이미지). 없으면 첫 번째 자동 승격
- **`defaults`**: variant 기본값. task 명시값 > workflow_params > defaults 우선순위로 patcher 적용

## V35 C/D 시리즈 → V36 패턴 자동화 ⭐

C1/C2/D1 은 V35 단계라 `_api_*.json` 이 없어 호출 불가였는데, **자동화 스크립트
2개로 V36 패턴(HiRes 옵션 + D1 알파 자동 합성)까지 한 번에 끌어올림**.

### 1. ComfyUI 측에서 워크플로우 일괄 생성

```bash
cd D:\DEV\ComfyUI\comfyuiImage_v34
python generate_cd_workflows.py
```

산출물 (총 8개):
- `C1_PixelDiffusionXL_V36.json` (UI) + `_api_stage1.json`, `_api_hires.json`
- `C2_RDXLPixelArt_V36.json` (UI) + `_api_stage1.json`, `_api_hires.json`
- `D1_AppIcon_V36.json` (UI, Stage1+Alpha 두 SaveImage) + `_api_default.json`

내부적으로 `generate_v36_workflows.py` 의 `ui_to_api/build_workflow/make_variants` 를
재활용. D1 은 V38 의 WAS 알파 그룹(`foreground / threshold=245 / tolerance=5`) 도
자동 합성해 raw + alpha 를 한 실행에 동시 출력.

### 2. asset-factory 로 import

```bash
cd D:\DEV\asset-factory
.venv\Scripts\python scripts\import_v36_workflows.py
```

이 스크립트가:
- `D:\DEV\ComfyUI\comfyuiImage_v34\` 의 C/D `_api_*.json` 5개를
  `workflows/{pixel_bg,icon}/` 로 복사
- `registry.yml` 의 `pixel_bg` / `icon` 카테고리 블록을
  4 + 1 = 5 변형 (모두 `available=true`) 으로 자동 갱신

환경변수: 다른 ComfyUI 경로면 `COMFYUI_WORKFLOWS_DIR=...` 로 override.

### 3. 활성화 검증

```bash
.venv\Scripts\python -m pytest tests\test_workflow_registry.py -q
# 22 passed (+ 새 변형 sanity 도 검증)

# 서버 재시작 후 (CLI 미구현이라 curl 직접 호출)
curl http://localhost:8000/api/workflows/catalog \
  | jq '.categories.pixel_bg.variants | keys'
# ["sdxl_stage1", "sdxl_hires", "pony_stage1", "pony_hires"]

curl http://localhost:8000/api/workflows/catalog \
  | jq '.categories.icon.variants.flat.outputs'
# [{"label": "stage1", ...}, {"label": "alpha", "primary": true}]
```

### 4. smoke 호출

```bash
# pixel_bg/sdxl_hires 한 슬롯
curl -X POST http://localhost:8000/api/workflows/generate \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "workflow_category": "pixel_bg",
       "workflow_variant": "sdxl_hires",
       "project": "myproj",
       "asset_key": "cave_bg",
       "category": "background",
       "prompt": "pixel art, dark cave background, torch lighting",
       "seed": 100,
       "candidates_total": 1
     }'
# → {"job_id": "...", "candidates_total": 1, "primary_output": "hires", ...}
# 진행/결과는 GET /api/jobs/{job_id} 폴링 또는 SSE /api/events 구독.

# icon/flat → 2장 (stage1 + alpha) 한 슬롯
curl -X POST http://localhost:8000/api/workflows/generate \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "workflow_category": "icon",
       "workflow_variant": "flat",
       "project": "myproj",
       "asset_key": "btn_settings",
       "category": "ui",
       "prompt": "settings icon, gear icon",
       "seed": 200,
       "candidates_total": 1
     }'

# illustration/hyphoria_single_alpha → warm-paper hero illustration + alpha
# (예: timecapsule 시리즈, wax seal 등 painterly hero 자산)
curl -X POST http://localhost:8000/api/workflows/generate \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "workflow_category": "illustration",
       "workflow_variant": "hyphoria_single_alpha",
       "project": "myproj",
       "asset_key": "timecapsule_seal_stamp_rose",
       "category": "illustration",
       "prompt": "wax seal stamp, deep rose, embossed flower motif",
       "seed": 300,
       "candidates_total": 1
     }'
# → primary 출력은 alpha (Save Alpha, WAS rembg foreground extraction).
# Stage1 raw 도 동일 호출에서 함께 저장.
```

### 5. spec 추가/변경하고 싶을 때

`generate_cd_workflows.py` 의 `WORKFLOWS = [...]` 에 dict 추가/수정 후 두 스크립트
재실행. registry.yml 은 idempotent (같은 변형명이면 덮어씀).

### (Legacy) 수동 export 절차

자동화 스크립트가 안 맞는 경우 (기존 V35 그대로 보존하고 싶거나, V36 generate
패턴이 안 맞는 새 워크플로우) — ComfyUI UI 에서 직접 export:

1. ComfyUI 띄우기: `python main.py --listen 0.0.0.0 --port 8188`
2. UI 에서 워크플로우 로드
3. 출력 노드 title 정리 (`Save Stage1` 등 정확히), Positive/Negative Prompt 노드도 동일
4. Settings ⚙ → "Enable Dev mode Options" → `Save (API Format)` 버튼 → export
5. `workflows/<category>/` 에 복사
6. `registry.yml` 에 변형 수동 추가 (file 경로, outputs, defaults)

## 새 카테고리/변형 추가하기

같은 절차. `workflows/<new_category>/` 디렉토리 만들고 API JSON 복사, `registry.yml` 에
새 카테고리 블록 추가. 자동으로 `GET /api/workflows/catalog` 응답에 노출
(추후 `af workflow catalog` CLI 도 동일 응답을 wrap 할 예정).

## 동적 입력 이미지 업로드 (PoseExtract / i2i 시나리오)

LoadImage 노드가 참조할 임의 이미지를 ComfyUI `input/<subfolder>/` 에 올리는
2-step 흐름. 사용자 캐릭터 이미지 → PoseExtract → 다른 변형의 ControlNet 입력
chain 의 시작점.

### Step 1 — 이미지 업로드

```bash
# 사용자 PC 의 PNG 를 ComfyUI input/asset-factory/ 에 직업로드
curl -X POST http://localhost:8000/api/workflows/inputs \
     -H "X-API-Key: $AF_API_KEY" \
     -F file=@my_character.png \
     -F subfolder=asset-factory
# → {"name": "abc123def456_my_character.png",
#    "subfolder": "asset-factory",
#    "type": "input"}
```

응답의 `name` 이 다음 단계에서 LoadImage 입력으로 사용된다. 멱등 — 같은 bytes
재업로드 시 동일 `name` 반환 (sha256[:12] prefix).

방어:
- content-type whitelist: `image/png`, `image/jpeg`, `image/webp`
- 크기 상한: 20MB → 413. env-var `ASSET_FACTORY_MAX_INPUT_BYTES` 로 운영 override
- PIL `Image.load()` 로 픽셀 디코딩 후 같은 포맷으로 재인코딩 → trailing
  payload (PNG 헤더 + ZIP/PHP polyglot) 자동 strip + EXIF/ICC 메타 정화. 400
- `DecompressionBombError` 캐치 → 픽셀폭탄 입력도 400
- `subfolder` 가 `[a-zA-Z0-9._-]{1,64}` 위반 시 `asset-factory` 로 정규화
- 파일명도 sanitize 후 `<sha256[:12]>_<safe_original>.<ext>` 형태로 저장
  (`..` 도 추가 strip, sha 는 *재인코딩된* bytes 기준)
- `from-asset` 도 동일 정화 패스 거침 — `upsert_scanned_asset` 으로 임의 사용자
  디렉토리 스캔 결과가 등록되는 케이스 방어

### Step 2 — 업로드 결과를 generate 호출에 박기

`workflow_params.load_images` 의 라벨 (`pose_image` / `source_image`) 로 dispatch:

```bash
# PoseExtract 변형 — source_image 라벨로 사용자 이미지 박음
curl -X POST http://localhost:8000/api/workflows/generate \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "workflow_category": "sprite",
       "workflow_variant": "pose_extract",
       "project": "myproj",
       "asset_key": "char_pose_v1",
       "category": "character",
       "prompt": "openpose extraction",
       "candidates_total": 1,
       "workflow_params": {
         "load_images": {"source_image": "abc123def456_my_character.png"}
       }
     }'
```

```bash
# ControlNet 변형 (pose_guided) — 사용자 정의 grid 박음
curl -X POST http://localhost:8000/api/workflows/generate \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "workflow_category": "sprite",
       "workflow_variant": "pixel_alpha",
       "project": "myproj",
       "asset_key": "warrior_idle",
       "category": "character",
       "prompt": "warrior cat, pixel art",
       "candidates_total": 1,
       "workflow_params": {
         "load_images": {"pose_image": "<업로드_응답의_name>.png"}
       }
     }'
```

지원 라벨: `pose_image` / `source_image`. 미등록 라벨은 silent skip
(`PatchReport.skipped`). 새 라벨 추가 시 `workflow_patcher._LOAD_IMAGE_RULES`
한 줄.

### Step 3 — 이전 generate 결과를 다음 입력으로 chain (수동)

```bash
# PoseExtract 가 만든 asset 을 다시 ComfyUI input/ 으로 업로드해 다음 변형의 입력으로
curl -X POST http://localhost:8000/api/workflows/inputs/from-asset \
     -H "X-API-Key: $AF_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"asset_id": "myproj:char_pose_v1", "subfolder": "asset-factory"}'
# → 응답 name 을 다음 generate 의 load_images.pose_image 에 사용
```

1차에선 chain 자동화 안 함 (사용자가 명시적으로 asset_id 전달). 자동화는
[docs/TODOS.md](../docs/TODOS.md) 의 후속 항목.

### GC

1차 PR 에 미구현 — `input/<subfolder>/` 의 옛 업로드 파일 누적은 사용자가
ComfyUI 호스트에서 수동 청소. 후속에 `task.workflow_params_json` 참조 추적
cron 추가 예정 ([docs/TODOS.md](../docs/TODOS.md) `comfyui-inputs-gc`).

## patcher 가 인지하는 키

`workflow_patcher.patch_workflow()` 호출 시 사용 가능한 인자 — `defaults` 와
`workflow_params` (request payload) 양쪽에서 같은 이름 사용:

| 키 | 매칭 노드 (class_type) | 매칭 title | 적용 input 필드 |
|---|---|---|---|
| `prompt` | CLIPTextEncode | `^Positive Prompt$` | `text` |
| `negative_prompt` | CLIPTextEncode | `^Negative Prompt$` | `text` |
| `seed` | KSampler (모두) | — | `seed` |
| `steps` | KSampler (모두) | — | `steps` |
| `cfg` | KSampler (모두) | — | `cfg` |
| `sampler_name` | KSampler (모두) | — | `sampler_name` |
| `scheduler` | KSampler (모두) | — | `scheduler` |
| `pose_image` | LoadImage | `Pose grid` (regex, ci) | `image` |
| `controlnet_strength` | ControlNetApply | — | `strength` |
| `checkpoint` | CheckpointLoaderSimple | — | `ckpt_name` |
| `width` | EmptyLatentImage | — | `width` |
| `height` | EmptyLatentImage | — | `height` |
| `lora_strengths` | LoraLoader (lora_name 매칭) | — | `strength_model`/`strength_clip` 동시 |
| `load_images` (dict) | LoadImage (라벨별) | — | `image` |

`load_images` 는 LoadImage 노드 dispatch 채널 — `{<label>: <filename>}`. 라벨은
`workflow_patcher._LOAD_IMAGE_RULES` 의 키:

| 라벨 | 매칭 title | 용도 |
|---|---|---|
| `pose_image` | `Pose grid` (loose, ci) | ControlNet 변형의 pose grid (V37/V38 sprite) |
| `source_image` | `^Load source image$` (anchored) | PoseExtract 의 사용자 입력 이미지 |

기존 `pose_image` 스칼라 kwarg 는 backward-compat 별칭 — `load_images` 가 같은
라벨 가지면 `load_images` 가 우선.

매칭 노드가 없으면 **조용히 스킵** (`PatchReport.skipped`). V36 Pro 처럼
ControlNet 없는 변형에 `controlnet_strength` 가 와도 에러 안 남.

새 노드 타입을 patch 대상에 추가하려면 `workflow_patcher._RULES` (스칼라) 또는
`_LOAD_IMAGE_RULES` (LoadImage 라벨) 에 한 줄 추가.

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `af workflow catalog` 응답에 변형이 `available: false` | file 경로의 JSON 이 디스크에 없음 | `file:` 경로 정확한지, 실제 파일 존재 확인 |
| `호출 불가 (status=needs_api_conversion)` | UI 포맷만 등록됨 | 위 §5 순서대로 API export 후 `file:` 채움 |
| `unknown variant: cat/v` | registry.yml 에 변형 미등록 | `af workflow catalog` 로 정확한 이름 확인 |
| Positive Prompt 가 안 바뀜 | 워크플로우의 CLIPTextEncode `_meta.title` 이 다름 | UI 에서 노드 title 을 정확히 `Positive Prompt` 로 변경 후 재 export |
| `매칭되는 SaveImage 결과 없음` | registry outputs 의 `node_title` 과 워크플로우 SaveImage `_meta.title` 불일치 | 둘 정확히 일치시킴 (대소문자·공백 포함) |
| pose grid 파일 못 찾음 | ComfyUI input/ 에 없음 | `D:\DEV\ComfyUI\input\` 에 png 복사 |

## 관련 문서

- `D:\DEV\sincerity-skills\sd-generator\HANDOFF.md` — 워크플로우 V35→V38 진화사·결정 근거
- `D:\DEV\asset-factory\sd_backend.py` — ComfyUIBackend 가 task → registry → patcher → ComfyUIClient 호출하는 본문
- `D:\DEV\asset-factory\workflow_patcher.py` — 패치 규칙 정의
