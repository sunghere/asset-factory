# NEXT — Asset Factory 후속 작업 (Spec)

> **이 문서의 위상**: 운영 회고 + **구현 명세**.
> 다른 개발자가 이 문서만 보고 §1.A/B/C 구현을 시작할 수 있는 것이 목표다.
> 스킬 관점 갭(`docs/TODOS_for_SKILL.md`)은 PR #18 머지로 흡수돼 폐기됐고, 그 후속이 본 문서다.
>
> **독자**: asset-factory 백엔드/CLI 를 모디파이할 차기 개발자.
> **선행 지식**: ComfyUI 워크플로우 구조, FastAPI/Pydantic, `workflow_registry.WorkflowRegistry`, `workflow_patcher.patch_workflow` 의 역할.

---

## 0. 큰 그림 — 지금 시스템과 갭

### 0.1 현재 데이터/제어 흐름 (구현돼 있는 것)

```
                    ┌──────────────────────────────┐
사용자/Agent  ──▶  │ POST /api/workflows/generate │
"prompt 통째로"     │  WorkflowGenerateRequest     │
                    └──────────────┬───────────────┘
                                   │
                ┌──────────────────▼──────────────────┐
                │ server.py · workflows_generate()    │
                │   1) variant 조회                   │
                │      WorkflowRegistry.get()         │
                │   2) Job/Task DB 행 생성            │
                │   3) ComfyUIBackend 에 dispatch     │
                └──────────────────┬──────────────────┘
                                   │
                ┌──────────────────▼──────────────────┐
                │ generator_comfyui.ComfyUIBackend    │
                │   1) variant.load_api_json()        │
                │   2) workflow_patcher.patch_workflow│
                │      ─ "prompt" 키:                 │
                │        request.prompt 그대로 → 노드 │
                │      ─ "negative_prompt" 키:        │
                │        request.negative_prompt 그대로│
                │   3) ComfyUIClient.queue_prompt()   │
                │   4) poll → outputs 저장            │
                │   5) validator.validate_pixel(...)  │
                └─────────────────────────────────────┘
```

핵심 모듈/타입:

| 모듈 | 역할 | 주요 타입 |
|---|---|---|
| `workflow_registry.py` | `workflows/registry.yml` 로드, `/api/workflows/catalog` 응답 소스 | `WorkflowRegistry`, `CategorySpec`, `VariantSpec`, `OutputSpec`, `InputLabelSpec` |
| `workflow_patcher.py` | 변형 JSON 위에 prompt/seed/cfg/... 패치 | `_RULES` dict, `patch_workflow()`, `PatchReport` |
| `generator_comfyui.py` | ComfyUI 클라이언트 (queue/poll/output fetch) | `ComfyUIClient`, `ComfyUIBackend` |
| `server.py` | API 핸들러 + 모델 (Pydantic) | `WorkflowGenerateRequest`, `workflows_generate()` |
| `cli/commands/workflow.py` | typer CLI (`af workflow {catalog,describe,upload,gen}`) | — |

### 0.2 이번 NEXT 가 메우는 갭 한 줄 요약

| § | 갭 | 한 줄 |
|---|---|---|
| 1.A | 카탈로그 정보 부족 | 변형의 *의도/제약* 이 yaml 안에만 있고 사용자에게 안 노출 → 에이전트가 잘못 매칭 |
| 1.B | prompt 통째 입력 | 사용자가 prompt 통째 작성 → 워크플로우 의도(grid/pixel/alpha) 가 prompt 에 0% 반영 |
| 1.C | 검색 인터페이스 부재 | 변형 늘어나면 LLM 가 카탈로그 통째로 받아 고르는 구조 → 토큰 낭비 + 오선택 |

§A 는 **데이터 노출**, §B 는 **데이터 강제 적용**, §C 는 **데이터 검색**. 셋 다 *변형 메타데이터* 한 yaml 에서 단일 진실 원천(SSOT)으로 흐른다.

---

## 1. smoke 회고 — 왜 워크플로우를 제대로 못 썼나

### 1.0 사건

- `sprite/pixel_alpha` 로 단일 시연 이미지를 뽑으려 했는데, **이 변형은 1280×640 가로 시트에 1×3 포즈 그리드(=3방향 캐릭터)** 를 그리는 용도다. 단일 이미지 변형이 아니다.
- 결과물도 의도된 3컷이 아니라 **학교 복도에 캐릭터 2명이 있는 씬 일러스트** 로 빠짐 (vision 분석 확인).
- 즉 카테고리 인지 실패 → 부적절한 prompt → 워크플로우 의도와 동떨어진 산출물.

### 1.0.1 근본 원인 (catalog API의 정보 부족)

증거를 카탈로그에서 *간접 추론* 해야 했고, 결정적인 한 줄이 어디에도 없었다:

| 단서 | 위치 | 가독성 |
|---|---|---|
| `pose_image: pose_grid_1x3_mini_2.5h_1280x640.png` | `defaults` | "1x3 grid" 가 *3 포즈 시트* 를 의미한다는 걸 별도로 알아야 해석 가능 |
| `1280×640` 해상도 | `defaults.width/height` | 가로 비율이 시트라는 직접 신호 X |
| `controlnet_strength: 0.85` | `defaults` | ControlNet 사용은 알지만 *무엇을 강제하는지* 안 보임 |
| negative 의 `different sizes, varying scale, oversized character, height variation` | `defaults.negative_prompt` | "여러 캐릭터를 같은 크기로" 의미인데 prose 로만 |
| `description: "픽셀 그리드 + 투명배경 (게임 엔진용)"` | `variants.pixel_alpha.description` | "그리드" 가 *픽셀 그리드* 인지 *포즈 그리드* 인지 모호 (둘 다 맞음) |
| `input_labels[].description` | catalog | **빈 문자열** — 라벨 의미 설명 없음 |

ComfyUI 쪽엔 `pose_grid_1x3_*`, `pose_grid_1x4_*`, `pose_grid_3x3_*` 같이 다양한 grid 가 깔려 있고, 변형마다 어떤 포즈 grid 가 박혀 있는지가 *변형의 정체성* 이다. 그런데 카탈로그는 이걸 "파일명 한 줄"로만 노출.

---

## 1.A 카탈로그 API 풍부화 (P0)

### A.1 기획 의도

**문제**: 카탈로그 사용자(에이전트/CLI/사람)가 변형을 *고를 때* 필요한 정보가 흩어져 있거나 빠져 있다. `description` 한 줄로는 부족.

**해결 가설**: 변형마다 *의도/용도/금기/출력 레이아웃/프롬프트 레시피* 를 구조화 필드로 노출하면, 매칭 정확도가 오르고 §B/§C 의 데이터 원천이 된다.

**비기능 요구**:
1. 기존 카탈로그 응답과 **forward-compatible** — 새 필드는 옵셔널, 비어 있으면 빈 객체.
2. 데이터 출처는 **단일 yaml** (per-variant). 코드 분기 X.
3. SSOT — §B 의 `prompt_template` 과 §C 의 인덱싱이 같은 데이터를 본다.

### A.2 데이터 모델 (yaml 스키마)

#### 위치
변형마다 `workflows/<category>/<variant>.meta.yaml`. 워크플로우 JSON과 같은 디렉토리. `registry.yml` 변경은 *없음* — registry 가 이 파일을 사이드카로 자동 로드.

> 대안 검토: registry.yml 을 거대화하기. **기각** — registry.yml 은 *호출 경로 매니페스트* 책임이고, 이 메타는 *사용자 노출용 설명* 책임. 분리해야 한 yaml 이 비대해지지 않고 변형 PR 단위가 작아진다.

#### 스키마 (JSON Schema 수준)

```yaml
# workflows/sprite/pixel_alpha.meta.yaml
schema_version: 1

# §A 핵심 ────────────────────────────────────────────────────────
intent: |
  3-pose character sheet (1×3 grid, transparent BG) for 2D game engine import.

use_cases:
  - "RPG character with idle / walk-side / walk-front 3-pose set"
  - "Top-down RPG sprite atlas seed (one row of poses, alpha-cut)"
  - "Card-game character sprite with consistent design across 3 angles"

not_for:
  - "single character portrait — use illustration/animagine_hires"
  - "scene/background — use pixel_bg/*"
  - "logo or app icon — use icon/flat"
  - "character with varying scale/age/outfit between cells"

output_layout:
  kind: pose_grid           # one_of: single | pose_grid | tile_grid | character_sheet
  rows: 1
  cols: 3
  per_cell_size: [426, 640] # 정수 [w, h] — 검수자 편의
  alpha: true
  notes: |
    Output is a single 1280×640 PNG with alpha. Each cell is one pose;
    consumers split by `pose_image` template grid lines.

# 자연어 검색용 태그 (§C 룰 기반 검색에 사용)
tags:
  - pixel-art
  - transparent-bg
  - pose-sheet
  - controlnet-pose
  - 1x3-grid
  - rpg-character
  - 2d-game-asset

# §B 핵심 ────────────────────────────────────────────────────────
prompt_template:
  base_positive: |
    pixel art, retro game sprite, sprite sheet, 1x3 pose grid layout,
    same character three poses side view, transparent background,
    consistent character design, sharp pixel edges, limited palette,
    centered character, no scenery, no background props
  base_negative: |
    (worst quality, low quality:1.4), blurry, jpeg artifacts, deformed, bad anatomy,
    watermark, text, signature, photorealistic, 3d, realistic, photo,
    smooth shading, anti-aliasing, gradient background, complex background, scenery,
    different sizes, varying scale, oversized character, height variation,
    close-up, upper body, portrait, cropped character, headshot
  user_slot:
    label: subject
    description: "캐릭터 묘사만 (외형/복장/무기). 스타일·구도·배경 묘사 금지."
    examples:
      - "1girl, silver hair twin tails, navy school uniform, holding a notebook"
      - "1boy, brown spiky hair, blue tunic, leather belt with sword"
    required: true
    min_chars: 8
    max_chars: 400
  injection_rule: "{base_positive}, {subject}"   # 또는 "{subject}, {base_positive}"

# 입력 라벨 description / alternatives 보강 (기존 input_labels 에 머지)
input_labels:
  pose_image:
    description: |
      ControlNet 포즈 가이드. 1×3 그리드 PNG. 같은 카테고리의 다른 grid 로
      교체하면 출력 레이아웃이 grid 에 맞춰 바뀐다 (예: 1×4 시트, 3×3 액션 시트).
    alternatives:
      - pose_grid_1x3_5h_1280x640.png        # 더 큰 캐릭터 비율
      - pose_grid_1x4_1280x640.png           # 4-pose
      - pose_grid_3x3_1280x896.png           # 9-pose 액션 시트
```

#### 필드 의미 (모두 옵셔널)

| 필드 | 타입 | 의미 |
|---|---|---|
| `intent` | str | **한 줄** 변형의 본질. 사람·LLM 모두 이 한 줄로 변형을 인식. |
| `use_cases` | str[] | 이 변형이 적합한 시나리오 (긍정 매칭 신호) |
| `not_for` | str[] | 부적합 + 대체 변형 힌트 (오선택 방지) |
| `output_layout.kind` | enum | 출력 형태. `single` / `pose_grid` / `tile_grid` / `character_sheet` |
| `output_layout.rows`/`cols` | int | grid 차원 (kind ≠ `single` 시) |
| `output_layout.per_cell_size` | [int,int] | 각 셀의 픽셀 크기 |
| `output_layout.alpha` | bool | 알파 채널 여부 |
| `output_layout.notes` | str | 자유 서술 (split 방법 등) |
| `tags` | str[] | 검색용 태그 (§C 룰 기반) |
| `prompt_template.*` | object | §B 참조 |
| `input_labels.<label>.description` | str | 기존 빈 문자열 채우기 |
| `input_labels.<label>.alternatives` | str[] | 같은 라벨에 대체 가능한 입력 파일들 |

#### Pydantic/Dataclass 타입 (서버 측)

```python
# workflow_registry.py (확장)

@dataclass(slots=True, frozen=True)
class OutputLayoutSpec:
    kind: Literal["single", "pose_grid", "tile_grid", "character_sheet"] = "single"
    rows: int = 1
    cols: int = 1
    per_cell_size: tuple[int, int] | None = None
    alpha: bool = False
    notes: str = ""

@dataclass(slots=True, frozen=True)
class UserSlotSpec:
    label: str = "subject"
    description: str = ""
    examples: tuple[str, ...] = ()
    required: bool = True
    min_chars: int | None = None
    max_chars: int | None = None

@dataclass(slots=True, frozen=True)
class PromptTemplateSpec:
    base_positive: str = ""
    base_negative: str = ""
    user_slot: UserSlotSpec | None = None
    injection_rule: str = "{base_positive}, {subject}"

@dataclass(slots=True, frozen=True)
class VariantMetaSpec:
    intent: str = ""
    use_cases: tuple[str, ...] = ()
    not_for: tuple[str, ...] = ()
    output_layout: OutputLayoutSpec = field(default_factory=OutputLayoutSpec)
    tags: tuple[str, ...] = ()
    prompt_template: PromptTemplateSpec | None = None
    # input_labels description/alternatives 는 기존 InputLabelSpec 확장으로 머지

# VariantSpec 확장
@dataclass(slots=True, frozen=True)
class VariantSpec:
    # ... 기존 필드 ...
    meta: VariantMetaSpec = field(default_factory=VariantMetaSpec)
```

### A.3 API 변경 — `/api/workflows/catalog`

#### Before (현재)
```jsonc
{
  "version": 1,
  "categories": {
    "sprite": {
      "description": "...",
      "primary_variant": "pixel_alpha",
      "variants": {
        "pixel_alpha": {
          "description": "픽셀 그리드 + 투명배경 (게임 엔진용) ⭐",
          "available": true,
          "status": "ready",
          "primary": true,
          "file": "...",
          "has_file": true,
          "outputs": [...],
          "defaults": {...},
          "input_labels": [{
            "label": "pose_image",
            "required": false,
            "default": "pose_grid_1x3_mini_2.5h_1280x640.png",
            "description": ""
          }]
        }
      }
    }
  }
}
```

#### After (이 PR)
```jsonc
{
  "version": 2,                                       // ← 응답 schema bump
  "categories": {
    "sprite": {
      "description": "...",
      "primary_variant": "pixel_alpha",
      "variants": {
        "pixel_alpha": {
          // 기존 필드 그대로 유지 ────────────────
          "description": "픽셀 그리드 + 투명배경 (게임 엔진용) ⭐",
          "available": true,
          "status": "ready",
          "primary": true,
          "file": "...",
          "has_file": true,
          "outputs": [...],
          "defaults": {...},

          // 신규 (§A) ────────────────────────────
          "meta": {
            "intent": "3-pose character sheet (...)",
            "use_cases": ["RPG character ..."],
            "not_for": ["single character portrait — use illustration/animagine_hires"],
            "output_layout": {
              "kind": "pose_grid",
              "rows": 1, "cols": 3,
              "per_cell_size": [426, 640],
              "alpha": true,
              "notes": "..."
            },
            "tags": ["pixel-art", "transparent-bg", "pose-sheet", ...],
            "prompt_template": {
              "base_positive": "pixel art, ...",
              "base_negative": "(worst quality:1.4), ...",
              "user_slot": {
                "label": "subject",
                "description": "캐릭터 묘사만 ...",
                "examples": [...],
                "required": true,
                "min_chars": 8,
                "max_chars": 400
              },
              "injection_rule": "{base_positive}, {subject}"
            }
          },

          // 확장 (§A): input_labels description/alternatives ──
          "input_labels": [{
            "label": "pose_image",
            "required": false,
            "default": "pose_grid_1x3_mini_2.5h_1280x640.png",
            "description": "ControlNet 포즈 가이드. 1×3 그리드 PNG. ...",
            "alternatives": [
              "pose_grid_1x3_5h_1280x640.png",
              "pose_grid_1x4_1280x640.png",
              "pose_grid_3x3_1280x896.png"
            ]
          }]
        }
      }
    }
  }
}
```

호환성:
- `version: 1 → 2` 응답 bump. 클라이언트는 `meta` 필드 부재를 *legacy* 로 인식.
- 신규 필드는 모두 옵셔널. 메타 yaml 없는 변형은 `meta: {}` (빈 객체) 로 노출.

### A.4 구현 단계

**Step 1 — Loader** (`workflow_registry.py`)

1. `infer_meta(file: Path) -> VariantMetaSpec` 함수: `<variant>.meta.yaml` 사이드카 로드. 없으면 `VariantMetaSpec()` 빈 객체.
2. `WorkflowRegistry._load_variant()` 안에서 호출, `VariantSpec.meta` 채움.
3. **JSON Schema 검증**: `schema_version` 미스매치는 `WorkflowRegistryError` (빠른 피드백). 권장: `pydantic` BaseModel 로 yaml 파싱.
4. `input_labels.<label>.description` 은 yaml 우선, 없으면 기존 `infer_input_labels` 의 빈 문자열 유지.

**Step 2 — API 직렬화** (`server.py` 또는 `catalog.py`)

5. `_variant_to_dict(v: VariantSpec) -> dict` 헬퍼에 `meta` 필드 추가. dataclass → dict 변환.
6. `version: 2` 로 bump.

**Step 3 — Tests**

7. `tests/test_workflow_registry.py`:
   - `test_meta_yaml_loaded`: pixel_alpha.meta.yaml 부착 후 VariantSpec.meta.intent 검증
   - `test_meta_missing_returns_empty`: 메타 파일 없으면 빈 객체 (forward-compat)
   - `test_meta_schema_violation_raises`: `schema_version: 99` → WorkflowRegistryError
8. `tests/test_workflow_endpoints.py`:
   - `test_catalog_v2_response_shape`: 응답에 `meta.intent`, `meta.output_layout.kind` 등 키 존재
   - `test_catalog_legacy_variant_has_empty_meta`: 메타 없는 변형은 `meta: {}`

**Step 4 — Migration** (data)

9. `sprite/pixel_alpha.meta.yaml` 작성 — 첫 변형. PR 분리.
10. 별 PR 들로: `sprite/{hires,rembg_alpha,full,...}`, `illustration/*`, `pixel_bg/*`, `icon/flat`.
    - 각 변형 PR 은 *그 변형 1개 yaml + tests* 만. reapply 단위 작게.

**Step 5 — Docs**

11. `sincerity-skills/asset-factory-api/SKILL.md`:
    - "변형 선택 체크리스트" 섹션 추가 — `intent`/`use_cases`/`not_for` 확인 가이드
    - freshness 표에서 §A 채워짐 표시

### A.5 테스트 전략

- **단위**: yaml 파싱 (스키마 / forward-compat / schema_version 검증)
- **통합**: `/api/workflows/catalog` 응답 shape — Pydantic response model 로 강제
- **회귀**: 기존 catalog 사용처 (cli/commands/workflow.py:catalog) 가 `meta` 부재에서도 안 깨지는지

### A.6 결정 메모

- **per-variant yaml** vs registry.yml 거대화: 분리. 변형 PR 단위가 작아지고 충돌 적다.
- **스키마 버저닝**: `schema_version: 1` 명시. 미래에 breaking 변경 시 `2` 로 bump 후 loader 분기.
- **`output_layout.kind` 닫힌 enum**: Literal 4개로 시작. 새 형태 추가는 enum 확장 PR.
- **i18n**: `intent`/`use_cases` 한국어/영어 혼용 OK. 검색 룰(§C)이 다국어 지원하면 됨.

---

## 1.B 공통 prompt 강제 주입 (P0)

§A 가 "에이전트가 잘못 고르는 사고" 를 막는 안전장치라면, 이건 **고른 변형 안에서 사용자가 잘못 짜는 사고** 를 막는 안전장치다.

### B.1 기획 의도

**문제**: 워크플로우 의도는 yaml/JSON 에 박혀 있는데, *prompt 는 사용자가 통째로 작성* 하는 흐름이라 의도가 prompt 에 0% 반영된다. ControlNet/PixelOE 같은 노드 강도만으로 형태를 강제하는데 이게 약하면 그대로 다른 그림으로 빠진다 (smoke 사고의 직접 원인).

**해결 가설**: 변형 yaml 에 `base_positive` / `base_negative` 를 박고, 사용자는 *주제(subject)* 만 주입. 서버가 정해진 `injection_rule` 로 합성해 ComfyUI 에 보낸다. SD 직접 호출자도 명시 키워드를 안 떼고 갈 수 있다.

**비기능 요구**:
1. **API 호환성** — 기존 `prompt`/`negative_prompt` 통째 입력은 *legacy 모드* 로 유지. 절대 깨지지 않음.
2. **신규 경로 (subject 입력)** 에서는 `base_*` 자동 합성, 사용자 prose 추가 가능.
3. **응답 가시성** — `final_prompt`/`final_negative` 를 응답에 노출. 디버깅·재현·로그 추적 가능.
4. **점진 마이그레이션** — yaml 에 `prompt_template` 없는 변형은 legacy 동작 유지.

### B.2 두 모드 정의

#### 모드 A: legacy (기존)
- 클라이언트가 `prompt` 통째로 보냄.
- 서버는 그대로 `workflow_patcher` 의 `prompt` 키로 ComfyUI 에 전달.
- `prompt_template` 무시. `negative_prompt` 도 통째.

#### 모드 B: subject-injection (신규)
- 클라이언트가 `subject` 만 보냄 (또는 `prompt` 가 *user_slot 으로 해석되는 짧은 형태*).
- 서버는 `prompt_template.base_positive` + `injection_rule` 로 합성.
- `negative_prompt` 가 있으면 `base_negative` 와 **콤마 join 추가** (override 아님).

#### 모드 선택 규칙

자동 감지 + 명시 플래그 둘 다 지원:

```
if request.subject is not None:
    mode = "subject"               # 신규 경로 명시
elif request.prompt_mode == "legacy":
    mode = "legacy"                # 명시 legacy
elif variant.meta.prompt_template is None:
    mode = "legacy"                # 변형이 안 받음
elif _looks_like_legacy_prompt(request.prompt):
    mode = "legacy"                # 자동 감지: 길고 style 키워드 다수
else:
    mode = "subject"
```

자동 감지 휴리스틱 `_looks_like_legacy_prompt`:
- prompt 길이 > `prompt_template.user_slot.max_chars` (default 400) → legacy
- `base_positive` 의 시그니처 키워드 (예: "pixel art", "duolingo style") 가 prompt 안에 *이미 있음* → legacy

### B.3 데이터 모델 (yaml — §A 와 통합)

(§A 의 yaml 안에 `prompt_template` 섹션 그대로. 재현 생략)

### B.4 API 변경 — `/api/workflows/generate`

#### Request (확장)

```python
class WorkflowGenerateRequest(BaseModel):
    # 기존 필드 ──────────────────────────────────
    project: str
    asset_key: str
    workflow_category: str
    workflow_variant: str
    prompt: str                    # 모드 A: 통째 / 모드 B: subject 로 해석 (auto)
    negative_prompt: str | None = None  # 항상 추가 (override 아님, 모드 B 에서)
    # 기존 seed/steps/cfg/sampler/candidates_total/workflow_params/...

    # 신규 (§B) ──────────────────────────────────
    subject: str | None = None     # 명시 모드 B 진입
    prompt_mode: Literal["auto", "legacy", "subject"] = "auto"
    style_extra: str | None = None # 모드 B 에서 base_positive 뒤에 추가될 사용자 prose (선택)
```

`prompt` 와 `subject` 동시 제공 시:
- `subject` 우선. `prompt` 는 무시 + warning 로깅.

`prompt_mode: "subject"` 인데 변형에 `prompt_template` 없음:
- HTTP 400 — `prompt_template_missing_for_variant`

#### Response (확장)

```jsonc
// POST /api/workflows/generate (200 OK)
{
  "job_id": "...",
  "variant": "sprite/pixel_alpha",
  "candidates": 1,
  "primary": "pixel_alpha",
  "approval_mode": "manual",

  // 신규 (§B) ────────────────────────
  "prompt_resolution": {
    "mode": "subject",                 // legacy | subject
    "user_slot": "subject",
    "user_input": "1girl, silver hair twin tails, navy school uniform",
    "final_positive": "pixel art, retro game sprite, sprite sheet, 1x3 pose grid layout, ..., 1girl, silver hair twin tails, navy school uniform",
    "final_negative": "(worst quality, low quality:1.4), ..."
  }
}
```

이 객체는 `GET /api/jobs/{id}` 에도 동일 포함 (재조회 가능).

### B.5 합성 알고리즘 (서버 측)

```python
# 의사코드
def resolve_prompt(
    request: WorkflowGenerateRequest,
    variant: VariantSpec,
) -> PromptResolution:
    template = variant.meta.prompt_template
    mode = _choose_mode(request, template)

    if mode == "legacy":
        return PromptResolution(
            mode="legacy",
            user_slot=None,
            user_input=request.prompt,
            final_positive=request.prompt,
            final_negative=request.negative_prompt or "",
        )

    # mode == "subject"
    user_input = (request.subject or request.prompt or "").strip()
    _validate_user_slot(template.user_slot, user_input)  # min_chars/max_chars 검증

    final_positive = template.injection_rule.format(
        base_positive=template.base_positive.strip(),
        subject=user_input,
    )
    if request.style_extra:
        final_positive = f"{final_positive}, {request.style_extra.strip()}"

    final_negative = template.base_negative.strip()
    if request.negative_prompt:
        final_negative = f"{final_negative}, {request.negative_prompt.strip()}"

    return PromptResolution(
        mode="subject",
        user_slot=template.user_slot.label,
        user_input=user_input,
        final_positive=final_positive,
        final_negative=final_negative,
    )
```

검증 (`_validate_user_slot`):
- `required=True` 인데 user_input 비어있으면 → HTTP 400 `subject_required`
- `min_chars`/`max_chars` 위반 → HTTP 400 `subject_length_invalid`
- (옵션) base_positive 의 시그니처 키워드가 user_input 에 중복 등장 → 200 + warning (header 로 노출)

### B.6 시퀀스 다이어그램

```
Client                Server                   Registry      Patcher        ComfyUI
  │                     │                        │              │              │
  │── POST /generate ──▶│                        │              │              │
  │  {subject: "..."}   │                        │              │              │
  │                     │── get(category,var) ──▶│              │              │
  │                     │◀──── VariantSpec ──────│              │              │
  │                     │                        │              │              │
  │                     │── resolve_prompt() ────│              │              │
  │                     │   (final_pos/neg 계산) │              │              │
  │                     │                        │              │              │
  │                     │── enqueue Task DB                                   │
  │                     │                                                      │
  │                     │── dispatch(prompt=final_pos, neg=final_neg) ──────▶│
  │                     │                       (ComfyUIBackend → patcher)    │
  │                     │                                                      │
  │◀── 200 ─────────────│                                                      │
  │  {job_id, prompt_resolution: {final_positive, final_negative, mode}}      │
```

### B.7 마이그레이션 단계

1. **Step 1**: `sprite/pixel_alpha.meta.yaml` 작성 (§A.4 step 9 와 동시) + `prompt_template` 박음.
2. **Step 2**: `WorkflowGenerateRequest` 에 `subject`/`prompt_mode`/`style_extra` 필드 추가, `resolve_prompt()` 구현, 핸들러 분기.
3. **Step 3**: 응답에 `prompt_resolution` 필드 추가. `/api/jobs/{id}` 도 동일.
4. **Step 4**: CLI (`af workflow gen`) 에 `--subject "..."`, `--prompt-mode {auto,legacy,subject}`, `--style-extra "..."` 추가. 기본 `--prompt-mode auto`.
5. **Step 5**: smoke 재현 — sprite/pixel_alpha 단일 시연 사고가 *재현 안 되는* 것을 확인 (회귀 테스트).
6. **Step 6**: 다른 변형 순차 이전. 각 변형 PR 은 yaml 1개 + 회귀 테스트만.
7. **Step 7**: SKILL.md 신규 경로 안내, legacy deprecation 노트.

### B.8 호환성/롤백

- legacy 사용처 (기존 `prompt` 통째 입력) 는 *조건 분기 없이* 동작 — 변형에 `prompt_template` 없으면 자동 legacy.
- 롤백 시 yaml 만 지우면 mode=legacy 로 fall-through. 코드 롤백 불필요.
- feature flag 불필요 (자동 감지 + per-variant opt-in).

### B.9 테스트 전략

```python
# tests/test_prompt_resolution.py
def test_subject_mode_injects_base_positive():
    req = make_req(subject="1girl, silver hair", workflow_variant="pixel_alpha")
    res = resolve_prompt(req, sprite_pixel_alpha_with_template)
    assert "pixel art" in res.final_positive
    assert "1girl, silver hair" in res.final_positive
    assert res.mode == "subject"

def test_legacy_mode_passes_through():
    req = make_req(prompt="completely custom prompt", prompt_mode="legacy")
    res = resolve_prompt(req, sprite_pixel_alpha_with_template)
    assert res.final_positive == "completely custom prompt"
    assert res.mode == "legacy"

def test_auto_detects_legacy_for_long_prompt():
    long_prompt = "..." * 100  # > max_chars
    req = make_req(prompt=long_prompt, prompt_mode="auto")
    res = resolve_prompt(req, sprite_pixel_alpha_with_template)
    assert res.mode == "legacy"

def test_negative_appended_not_replaced():
    req = make_req(subject="...", negative_prompt="extra negative")
    res = resolve_prompt(req, sprite_pixel_alpha_with_template)
    assert "(worst quality" in res.final_negative   # base 유지
    assert "extra negative" in res.final_negative   # user 추가

def test_variant_without_template_falls_back_to_legacy():
    res = resolve_prompt(req, variant_without_meta)
    assert res.mode == "legacy"

def test_subject_required_rejected():
    req = make_req(subject="")
    with pytest.raises(HTTPException) as exc:
        resolve_prompt(req, sprite_pixel_alpha_with_template)
    assert exc.value.status_code == 400
```

### B.10 결정 메모

- **`base_positive` 위치**: `injection_rule` 로 *앞* 또는 *뒤* 선택. SD 는 *앞쪽 토큰의 가중치가 큼* — 변형 의도가 강하게 박혀야 하면 `{base_positive}, {subject}`, 사용자 주제가 더 도드라져야 하면 `{subject}, {base_positive}`. 변형마다 yaml 에서 결정.
- **`negative_prompt` 추가만 / override 안 함**: 사용자가 base_negative 를 무력화할 수 있으면 *변형 안전장치* 가 깨짐. 명시 의도가 있다면 `prompt_mode: legacy` 로 갈 수 있음.
- **자동 감지 휴리스틱 위험**: 너무 똑똑하면 사용자가 헷갈림. 응답 `prompt_resolution.mode` 를 *항상* 명시해서 사용자가 자기 모드를 알 수 있게.
- **에이전트 사용 가이드**: `sincerity-skills` SKILL.md 는 *항상* `--subject` 권장. legacy 는 *원래 prompt 통째 갖고 있는 외부 시스템 옮길 때만*.

---

## 1.C 변형 의도 기반 인덱스 / 자연어 검색 (P1)

§A·§B 가 *데이터를 만드는* 작업이라면, 이건 *그 데이터를 쓰는* 작업.

### C.1 기획 의도

**문제**: 지금 에이전트가 카탈로그 전체 JSON 을 받아 prompt 안에 다 넣고 LLM 더러 골라달라는 구조. 변형 5개 OK, 30+ 면 토큰 낭비 + 오선택 빈도↑.

**해결 가설**: §A 의 `intent`/`use_cases`/`not_for`/`tags` 를 인덱싱해 자연어 의도 → ranked 변형 매칭.

### C.2 사용자 인터페이스

```bash
# 1. 자연어 의도 → 변형 추천 (top-k)
af workflow recommend "RPG 게임 캐릭터 정면/측면/뒷면 픽셀 스프라이트" --top 3
# 출력 예시:
# rank  variant                     score   intent (one-line)
#  1    sprite/pixel_alpha          0.92    3-pose character sheet (1×3 grid, transparent BG)
#  2    sprite/v37_pixel            0.71    legacy pose-guided pixel character (V37 pre-alpha)
#  3    illustration/animagine_hires 0.31   single anime illustration (NOT pixel art)
#
# 각 후보 옆에 not_for 첫줄을 함께 표시 — 오매칭 차단.

# 2. 태그 색인 검색
af workflow search --tag pose-sheet --tag transparent-bg
# 출력: 매칭 변형 목록 (intent 한 줄 포함)

# 3. 부정 필터
af workflow search --not scenery --tag pixel-art

# 4. JSON 출력 (스킬·파이프라인용)
af workflow recommend "..." --json
```

### C.3 API

```
POST /api/workflows/recommend
{
  "query": "RPG 캐릭터 정면/측면/뒷면 픽셀 스프라이트",
  "top": 3,
  "include_unavailable": false
}

→ 200 OK
{
  "query": "...",
  "candidates": [
    {
      "variant": "sprite/pixel_alpha",
      "score": 0.92,
      "intent": "3-pose character sheet (...)",
      "use_cases_hit": ["RPG character ..."],
      "tags_hit": ["pixel-art", "pose-sheet", "transparent-bg"],
      "not_for_warnings": []
    },
    ...
  ],
  "scoring_method": "rule"   // rule | embedding (Phase 2)
}
```

```
GET /api/workflows/search?tag=pose-sheet&tag=transparent-bg&not=scenery
→ 200 OK
{
  "matches": [
    {"variant": "sprite/pixel_alpha", "tags_hit": [...]},
    ...
  ]
}
```

### C.4 스코어링 — Phase 1 (룰 기반, 즉시 가능)

**입력**: 사용자 query (자연어), 모든 ready 변형의 `meta`.
**출력**: 각 변형에 0.0–1.0 score.

```python
def rule_score(query: str, meta: VariantMetaSpec) -> float:
    q_tokens = _tokenize(query.lower())     # 한국어/영어 모두 토큰화 (간단: regex)
    score = 0.0

    # intent — 한 줄에 가장 큰 가중치
    if any(t in meta.intent.lower() for t in q_tokens):
        score += 0.4

    # use_cases — 매칭마다 +0.15, max 0.45
    hits = sum(1 for uc in meta.use_cases if any(t in uc.lower() for t in q_tokens))
    score += min(hits * 0.15, 0.45)

    # tags — 매칭마다 +0.1, max 0.3
    tag_hits = sum(1 for tag in meta.tags if tag.lower() in q_tokens)
    score += min(tag_hits * 0.1, 0.3)

    # not_for 페널티 — 매칭되면 큰 감점
    not_for_hits = sum(1 for nf in meta.not_for if any(t in nf.lower() for t in q_tokens))
    score -= not_for_hits * 0.5

    return max(0.0, min(1.0, score))
```

토크나이저는 처음에 단순 regex (`[a-z가-힣0-9]+`). 한국어 형태소까진 안 가도 OK — `intent`/`tags` 를 영어/명사 위주로 쓰는 컨벤션이면 충분.

### C.5 Phase 2 — 임베딩 기반 (변형 30+ 시)

- `intent + use_cases` 를 BGE-m3 (또는 mxbai-embed-large) 로 임베딩.
- 사용자 query 도 동일 모델로 임베딩.
- cosine similarity 로 ranking. 임베딩 캐시: 변형 yaml hash → vector.
- 룰 점수와 weighted sum (예: `0.6 * rule + 0.4 * cosine`).
- 모델 로컬 inference (Ollama / sentence-transformers) — 외부 의존 X.

**언제 도입**: 변형 < 30 일 때는 룰만으로 충분. 임베딩 추가는 변형 수 임계치 도달 후 별도 PR.

### C.6 구현 단계

1. **Step 1**: `recommendations.py` 모듈 신규. `rule_score()`, `recommend()` 구현.
2. **Step 2**: `/api/workflows/recommend` POST 핸들러 + `/api/workflows/search` GET 핸들러.
3. **Step 3**: CLI `af workflow recommend` / `af workflow search` 추가.
4. **Step 4**: tests — 룰 매칭 케이스 5종 (긍정/부정/태그/not_for 페널티/빈 query).
5. **Step 5** (별 PR): 임베딩 backend 추가. `scoring_method` 응답 필드로 노출.

### C.7 결정 메모

- **자연어 query 의 한국어**: 룰 단계는 단순 토큰 매칭이라 *yaml 의 한국어 키워드와 매칭* 됨. yaml 작성 시 한국어 use_cases/tags 를 같이 넣으면 자연스럽게 한국어 query 도 매칭.
- **검색은 서버에**: 클라이언트(LLM 에이전트) 가 카탈로그 통째 받아 직접 매칭 가능하지만, 변형 수가 늘면 비효율. 서버가 룰/임베딩 캐시 보유.
- **LLM 분류 (옵션 3)** 는 서버 책임 X. 에이전트 측에서 `recommend` 결과를 받아 최종 선택하면 됨.

---

## 1.D 부수 보강

### D.1 `--dry-run` 플래그

`af workflow gen ... --dry-run` → ComfyUI dispatch 안 하고 다음만 출력:
- §A 의 변형 meta (intent / output_layout)
- §B 의 `prompt_resolution.final_positive` / `final_negative`
- patcher 의 `PatchReport` (어떤 키가 어떤 노드에 적용됐는지)
- ComfyUI 큐에 보낼 *최종 워크플로우 JSON 의 핵심 노드 추출*

`docs/TODOS_for_SKILL.md` ⏳ 항목으로 있던 거. §A/§B 와 합쳐지면 *"이 변형이 내 의도에 맞나"* 사전 점검에 핵심.

### D.2 SKILL 변형 선택 체크리스트

`sincerity-skills/asset-factory-api/SKILL.md` 에 추가:

> **변형 선택 4-step (§A/B/C 기반)**
> 1. `af workflow recommend "<요청 한 줄>"` → top-3 후보 받기
> 2. 각 후보의 `intent` / `not_for` 읽기. 매칭 안 맞으면 다음 후보.
> 3. `output_layout.kind` 가 기대치와 맞는지 — 단일 vs 그리드 (사용자 의도 재확인 필요할 수도)
> 4. 호출 시 **`--subject "<주제만>"`** 사용. prompt 통째 작성 ❌

---

## 2. Asset Factory 본체 후속 작업

### P0 — 즉시

#### 2.1 `icon/flat` 변형 회귀 ⚠️

PR #18 머지 후 `icon/flat` 호출이 1.5초만에 fail. ComfyUI 까지 도달도 못함.

- 증상: `status=completed_with_errors total=1 failed=1`, `error_message: null`, `assets=[]`, ComfyUI history 에 prompt_id 없음.
- 비교: 동일 환경에서 `sprite/pixel_alpha` 정상 (18s).
- 단서: `icon/flat.input_labels: []` (빈 배열). `workflow_patcher` 의 input_labels 빈 케이스 처리 의심.
- 보고: PR #18 코멘트로 재현 + 환경 이미 전달.
- 영향: **logo-agent v4 의 유일한 호출 경로** 라 우선순위 최상.

#### 2.2 task-level error 캡처 — `error_message: null` 금지

현재는 task 가 실패해도 job 응답의 `error_message: null` 이라 진단 불가. 백그라운드 task 의 exception traceback 을 DB 에 보관하고, `GET /api/jobs/{id}` 응답에 노출.

```jsonc
{
  "id": "...",
  "status": "completed_with_errors",
  "failed_count": 1,
  "task_errors": [
    {
      "task_index": 0,
      "stage": "comfyui_dispatch",  // patch / upload / dispatch / poll / fetch / postprocess
      "error_class": "ConnectionError",
      "message": "...",
      "traceback": "..."
    }
  ]
}
```

이게 있었으면 `icon/flat` 회귀를 1분 안에 진단했을 일이다.

DB 변경:
```sql
ALTER TABLE tasks ADD COLUMN error_stage TEXT;
ALTER TABLE tasks ADD COLUMN error_class TEXT;
ALTER TABLE tasks ADD COLUMN error_message TEXT;
ALTER TABLE tasks ADD COLUMN error_traceback TEXT;
```

후처리 코드:
- `generator_comfyui.ComfyUIBackend.execute()` 의 `except Exception as exc` 블록에서 `db.update_task(error_stage=..., error_class=type(exc).__name__, error_message=str(exc), error_traceback=traceback.format_exc())`.
- `/api/jobs/{id}` 응답 직렬화에 위 4 컬럼 합침.

### P1 — 다음 사이클

#### 2.3 카탈로그 API 풍부화 (§1.A)

위 §1.A 의 `intent`/`use_cases`/`not_for`/`output_layout`/`tags` 필드 추가. 데이터 출처는 변형마다 `<variant>.meta.yaml` 사이드카. (구현 명세는 §1.A.4)

#### 2.3b 공통 prompt 강제 주입 (§1.B)

워크플로우마다 `prompt_template` 박고 `--subject` 주입. 첫 변형 `sprite/pixel_alpha`, 응답 `prompt_resolution` 노출. (구현 명세는 §1.B.7)

#### 2.3c 변형 의도 기반 인덱스/검색 (§1.C)

`af workflow recommend` + `af workflow search`. 룰 기반부터, 변형 30+ 시 임베딩 추가. (구현 명세는 §1.C.6)

#### 2.4 `icon/flat` 외 로고 전용 변형 추가 검토

`icon/flat` 은 Animagine 기반 (애니/일러스트 톤). v3 로고 실험에서 효과 본 *duolingo/토스 chunky flat* 톤은 `dreamshaper_8` 기반 SD1.5 가 가장 안정적이었다. 후속 변형 `icon/logo_chunky` (혹은 `icon/dreamshaper_logo`) 를 검토.

#### 2.5 `--dry-run` 플래그 (§1.D.1)

`af workflow gen ... --dry-run` — patcher 결과 + final_prompt 만 출력, ComfyUI dispatch 안 함.

#### 2.6 `aliases` (`@character` 등)

자연어 키워드 → variant alias. 이건 §C 의 LLM 매칭으로 부분 해결 가능하므로 우선순위 낮음.

#### 2.7 `run:<run_id>/<output>` 입력 syntax

현재 `--input source_image=asset:<id>` 로 부분 충족. 다음 단계는 *특정 run 의 특정 output label* 직접 참조 — `--input src=run:abc/pixel_alpha`. chain 단순화에 큰 도움.

### P2 — 운영 편의

#### 2.8 `assets` 응답에 `approval_mode` 직렬화

PR #18 코멘트로 보고된 follow-up. `--bypass-approval` 로 호출했는데 응답의 `approval_mode` 가 `"manual"` 로 나오는 (smoke 재확인됨). 직렬화 수정.

#### 2.9 `excluded_bypassed` 메타 검증 강화

빈 프로젝트에서도 bypass 제외 카운트가 정확히 0 인지 + null 회피.

#### 2.10 sprite/pixel_alpha validation 메시지 보강

현재 `validation_message: "팔레트 초과: 6250색 (최대 32색)"` 만. 사용자 입장에선 *왜 32색이 한도인지* 와 *어떻게 줄이는지* 가 더 가치있다. 문맥 첨부:
```
validation_message: "팔레트 초과: 6250색 (한도 32색).
  원인 후보: ① 워크플로우 픽셀화 단계 (PixelOE) 결과가 약함 — controlnet_strength↑ 시도.
            ② 프롬프트가 너무 사실적 — 'pixel art, low color, limited palette' 키워드 추가.
            ③ stage1 출력은 1280×640 에 6250색이 정상이고 pixel_alpha 단계 자체가 fail."
```

#### 2.11 logo-agent v4 후속 — 진짜 로고 검증 시연

`icon/flat` 회귀 (2.1) 가 풀린 직후 docs/logo-agent.md v4 실제로 한 번 돌려서 결과물 첨부. 그래야 v4 가 실측 검증된 문서가 됨.

---

## 3. 구현 우선순위 / 의존성 그래프

```
                          ┌─────────────────────────────┐
                          │ 2.1 icon/flat 회귀 (P0)     │
                          └──────────────┬──────────────┘
                                         │ unblocks
                                         ▼
                          ┌─────────────────────────────┐
                          │ 2.11 logo-agent v4 시연     │
                          └─────────────────────────────┘

┌──────────────────┐      ┌─────────────────────────────┐
│ 2.2 task error   │ ───▶│ 차후 모든 디버깅 가속        │
│ 캡처 (P0)        │      └─────────────────────────────┘
└──────────────────┘

┌──────────────────────────────┐
│ §1.A catalog 풍부화 (2.3)    │  ◀── SSOT
└──────────────┬───────────────┘
               │ enables
               ├───────────────────────┐
               ▼                       ▼
┌──────────────────────┐   ┌──────────────────────┐
│ §1.B prompt 강제(2.3b)│   │ §1.C 의도 검색(2.3c) │
└──────────┬───────────┘   └──────────────────────┘
           │ enables
           ▼
┌──────────────────────┐
│ §1.D.1 --dry-run     │
│ (2.5)                │
└──────────────────────┘
```

권장 작업 순서:
1. **2.1 + 2.2** (P0, 별도 개발자 영역) — 회귀 + 진단성
2. **2.3 (§1.A)** — yaml 스키마 + loader. 데이터 SSOT 만들기. 첫 변형 1개 (`sprite/pixel_alpha`) 만 채우는 PR.
3. **2.3b (§1.B)** — `prompt_resolution` 핸들러. 같은 변형부터.
4. **2.3c (§1.C)** — recommend/search. 변형 5개 이상 yaml 채워진 후.
5. **2.5 (--dry-run)**, **2.4 (logo 변형)**, **2.6/2.7** 차례.

---

## 4. 진행 상태

| 항목 | 상태 |
|---|---|
| sincerity-skills PR #4 (post-PR#18 정리) | ✅ 머지됨 |
| asset-factory PR #18 (P0 갭) | ✅ 머지됨 |
| asset-factory PR #19 (logo-agent v4) | ✅ 머지됨 |
| asset-factory PR #20 (NEXT 초안) | ✅ 머지됨 |
| `docs/TODOS_for_SKILL.md` 폐기 | ✅ 이 문서로 흡수, 파일 삭제됨 |
| 2.1 icon/flat 회귀 | 🔴 별도 개발자 |
| 2.2 task-level error 캡처 | 🟡 별도 개발자 |
| 2.3 카탈로그 API 풍부화 (§1.A) | 🟡 spec 완료, 구현 대기 |
| 2.3b 공통 prompt 강제 주입 (§1.B) | 🟡 spec 완료, 구현 대기 |
| 2.3c 의도 기반 인덱스/검색 (§1.C) | 🔵 spec 완료, 2.3 후 |
| 2.4–2.11 | 🔵 backlog |

---

## 5. 결정 메모 (Top-level)

- **§A/B/C 가 한 yaml 의 SSOT 라는 점이 본 설계의 척추**. 세 작업이 별 yaml/별 시스템에 데이터 흩으면 동기화 비용 폭발. `<variant>.meta.yaml` 한 파일이 *문서·강제 적용·검색* 세 곳을 다 먹인다.
- **per-variant yaml** vs registry.yml 거대화: 분리. 변형 PR 단위가 작아지고 충돌 적다.
- **legacy 호환은 자동 감지로**: feature flag/환경변수 추가 안 함. 변형 yaml 에 `prompt_template` 부재 → mode=legacy. 도입 마찰 0.
- **`base_positive` 위치는 변형마다**: `injection_rule` 로 *앞* 또는 *뒤* 선택. SD 토큰 가중치 때문에 변형마다 다를 수 있음.
- **`negative_prompt` 추가만 / override 안 함**: 사용자가 base_negative 무력화하면 안전장치 깨짐. 명시 의도면 `prompt_mode: legacy`.
- **§C 임베딩은 변형 30+ 임계 후**: 룰만으로 5–20 변형 정확. 임베딩 도입은 별 PR.
- **2.1 icon/flat 회귀가 §1.B 도입의 사실상 첫 시험대**: 회귀 풀린 후 §1.B 로 바로 시연 (sprite/pixel_alpha + icon/flat 두 변형에서 final_prompt 노출 확인).
- **logo-agent v4 신뢰도**: `icon/flat` 회귀가 풀리기 전엔 v4 docs 가 *이론 검증* 만. 2.11 시연 거쳐야 production.
- **`docs/TODOS_for_SKILL.md` 폐기 사유**: TDD red phase 문서였고 PR #18 머지로 green. 잔여 ⏳ 항목 (`--dry-run`, `aliases`, `run:<id>/<out>`) 은 본 NEXT.md §2 P1 으로 흡수.
