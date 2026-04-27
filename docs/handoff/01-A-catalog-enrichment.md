# §1.A — 카탈로그 API 풍부화 (P0) — 개발자 핸드오프 Prompt

> **이 문서를 어떻게 쓰나**
> 별도 Claude Code 인스턴스에 *이 파일 통째로* 를 컨텍스트로 주입한다.
> (`claude -p "$(cat docs/handoff/01-A-catalog-enrichment.md)"` 또는 worktree 시작 시
>  `claude -w cat-enrich` 후 첫 메시지로 통째 paste.)
> 받는 개발자/에이전트는 *이 문서 + 명시된 참조 문서들 만으로* §1.A 작업을
> 시작·완료·테스트·PR 까지 갈 수 있어야 한다.

---

## 0. 작업 한 줄 요약

`/api/workflows/catalog` 응답에 변형(variant) 마다 *의도/용도/금기/출력
레이아웃/프롬프트 레시피* 메타데이터를 노출한다. 데이터 출처는 변형마다 별도
`<variant>.meta.yaml` 사이드카 파일 (SSOT). API 버전을 `1 → 2` 로 bump.

## 1. 선행 컨텍스트 — 반드시 먼저 읽어야 할 문서

받는 개발자는 **이 문서를 읽기 전에** 아래 문서들을 *순서대로* 한 번씩 훑는다.
모르면 §1.A 의 결정 메모(왜 yaml 인지, 왜 SSOT 인지) 가 와 닿지 않는다.

| 순서 | 파일 | 왜 읽나 | 핵심 섹션 |
|---|---|---|---|
| 1 | `docs/NEXT.md` (PR #21 머지본) | §1.A/B/C 전체 그림. 이 핸드오프는 §1.A 만 다룸 | `## 0. 큰 그림`, `## 1.A` 전체 (L91–L405) |
| 2 | `docs/NEXT.md` `## 0.1 현재 데이터/제어 흐름` (L14–L52) | 현재 카탈로그가 어떻게 만들어지는지 ASCII 다이어그램 | L14–L52 |
| 3 | `CLAUDE.md` (root) | 프로젝트 운영 규칙, NEXT.md 라이프사이클 | 전체 (짧음) |
| 4 | `.cursorrules` | 코딩 스타일·구현 순서·테스트 정책 | 전체 |
| 5 | `HANDOFF.md` | 인프라 (SD 서버 주소, ComfyUI 호스트 등) | 전체 |
| 6 | `DESIGN.md` | 카탈로그/워크플로우 전체 설계. §1.A 가 어디 끼어드는지 | "Workflow Registry" 섹션 |
| 7 | `workflow_registry.py` 전체 | `VariantSpec`, `CategorySpec`, `OutputSpec`, `InputLabelSpec` 현재 구조 | 전체 (~수백 줄) |
| 8 | `server.py` 의 `workflows_catalog` 핸들러 | 현재 직렬화 로직 — `_variant_to_dict` 또는 dataclass→JSON 변환 위치 | grep `catalog` |
| 9 | `workflows/registry.yml` | 변형 등록 매니페스트. **§1.A 는 이 파일을 건드리지 않는다** (사이드카로 분리) | 전체 훑기 |
| 10 | `workflows/sprite/Sprite_Illustrious_PoseGuided_Alpha_V38_api_pixel_alpha.json` | 첫 마이그레이션 대상 변형의 ComfyUI workflow JSON. meta.yaml 작성할 때 참고 | 전체 |
| 11 | `tests/test_workflow_registry.py`, `tests/test_workflow_endpoints.py` | 기존 테스트 패턴 (fixture 위치, helper 함수) | 전체 |
| 12 | `~/workspace/sincerity-skills/asset-factory-api/SKILL.md` | catalog 사용처. §1.A 머지 후 freshness 표 갱신 필요 | "변형 선택" 관련 섹션 |

선행 지식 부족 시 먼저 메인 사용자에게 질문 후 시작. **추측으로 시작 금지.**

---

## 2. 기획 의도 (왜 이 작업인가)

### 2.1 문제

카탈로그 사용자(에이전트/사람)가 변형을 *고를 때* 필요한 정보가 흩어져 있다.
현재 `description` 한 줄 (`"픽셀 그리드 + 투명배경"`) 만으로는 변형의 본질을
파악할 수 없다.

**구체적 사건** (2026-04-26 smoke test):
`sprite/pixel_alpha` 는 *1×3 pose grid 캐릭터 시트* (1280×640, 3방향 포즈,
투명배경) 생성용인데, 사용자가 "그리드"라는 단어를 *픽셀 그리드* (해상도 격자)
로 오해해서 `"1girl, silver hair, school uniform, walking"` 단일 일러스트
프롬프트를 던졌다. 결과: 학교 복도 씬 그림. 의도와 정반대.

### 2.2 해결 가설

변형마다 *의도(intent)/용도(use_cases)/금기(not_for)/출력 레이아웃
(output_layout)/프롬프트 레시피(prompt_recipe)* 를 구조화 필드로 노출하면,
매칭 정확도가 오르고 §1.B(prompt 강제 주입) 와 §1.C(자연어 검색) 의 데이터
원천이 된다 (SSOT).

### 2.3 비기능 요구사항

1. **Forward-compatible**: 새 필드는 모두 옵셔널. 기존 클라이언트 (legacy
   variant — meta yaml 없는 변형) 가 깨지지 않아야 한다.
2. **데이터 출처는 단일 yaml 사이드카** (변형마다 1파일). 코드에 분기 X.
3. **SSOT** — §1.B 의 `base_positive`/`base_negative`, §1.C 의 검색 인덱스가
   *모두 같은 yaml* 을 본다.
4. API 응답 `version: 1 → 2` bump. 응답 root 에 `version` 필드.

---

## 3. 데이터 모델

### 3.1 위치

변형마다 `workflows/<category>/<variant>.meta.yaml` 사이드카 파일.

> **대안 검토**: `workflows/registry.yml` 거대화. **기각** 이유:
> registry 는 *호출 매니페스트* 책임 (어떤 노드/입력), meta 는 *사용자 노출*
> 책임 (의도/용도). 분리해야 변형 PR 단위가 작아지고 충돌 적다.

### 3.2 yaml 스키마 (전체 예시 — `workflows/sprite/pixel_alpha.meta.yaml`)

```yaml
schema_version: 1

# §A 핵심 ─────────────────────────────────────────────────
intent: |
  단일 캐릭터의 1×3 pose grid 시트를 1280×640, 투명 배경으로 생성한다.
  게임 엔진/스프라이트 워크플로우용 캐릭터 시트.

use_cases:
  - 게임용 캐릭터 스프라이트 시트 (3방향 포즈)
  - 도트 RPG NPC 디자인 컨셉
  - Aseprite import 전 base sheet

not_for:
  - 단일 일러스트 (배경/씬 포함)
  - 다중 캐릭터 그룹샷
  - 정사각형 단일 픽셀 아이콘 → icon/flat 사용

output_layout:
  kind: pose_grid          # Literal[single, pose_grid, tile_grid, character_sheet]
  width: 1280
  height: 640
  rows: 1
  cols: 3
  cell_width: 320
  cell_height: 480
  alpha: true              # 투명 배경
  notes: "각 셀은 같은 캐릭터, 다른 포즈 (정면/측면/후면)"

# 자연어 검색용 태그 (§C 룰 기반 검색에 사용)
tags:
  - sprite
  - pose-grid
  - transparent-bg
  - pixel-art
  - 1x3
  - character-sheet

# §B 핵심 ─────────────────────────────────────────────────
prompt_recipe:
  base_positive: |
    pixel art, 1x3 pose grid, character sheet,
    front view, side view, back view,
    transparent background, simple shading, clean line
  base_negative: |
    background, scenery, multiple characters, blur, jpeg artifacts
  injection_rule: "{base_positive}, {subject}"
  subject_hint: "캐릭터 묘사만. 배경/씬 묘사 금지. 예: '1girl, silver long hair, school uniform'"

# 입력 라벨 description / alternatives 보강 (기존 input_labels 에 머지)
input_labels:
  prompt:
    description: "캐릭터 묘사. base_positive 와 자동 합쳐짐."
    alternatives: ["subject", "character"]
  negative_prompt:
    description: "추가 negative. base_negative 와 append 됨 (override 불가)."
```

### 3.3 필드 의미 (모두 옵셔널 — 부재 시 빈 객체/문자열)

| 필드 | 타입 | 의미 |
|---|---|---|
| `schema_version` | int | meta 스키마 버전. 미스매치는 에러 |
| `intent` | str | 한 줄~문단. 변형의 본질 |
| `use_cases` | list[str] | 적합한 사용 사례 |
| `not_for` | list[str] | 금기 사용 사례 (§C 검색에서 negative match) |
| `output_layout.kind` | Literal | `single \| pose_grid \| tile_grid \| character_sheet` |
| `output_layout.{width,height,rows,cols,cell_*,alpha}` | int/bool | 출력물 구조 |
| `tags` | list[str] | §1.C 검색 태그 |
| `prompt_recipe.base_positive` | str | §1.B 가 사용. 사용자 subject 앞에 prepend |
| `prompt_recipe.base_negative` | str | §1.B 가 사용. 사용자 negative 와 append (override 금지) |
| `prompt_recipe.injection_rule` | str | `{base_positive}` / `{subject}` 플레이스홀더 |
| `prompt_recipe.subject_hint` | str | 사용자에게 보여줄 가이드 |
| `input_labels.<key>.{description,alternatives}` | str/list | 기존 input_labels 에 yaml 우선 머지 |

### 3.4 서버 측 Pydantic/Dataclass 타입 (`workflow_registry.py` 확장)

```python
from dataclasses import dataclass, field
from typing import Literal

OutputLayoutKind = Literal["single", "pose_grid", "tile_grid", "character_sheet"]

@dataclass(slots=True, frozen=True)
class OutputLayoutSpec:
    kind: OutputLayoutKind | None = None
    width: int | None = None
    height: int | None = None
    rows: int | None = None
    cols: int | None = None
    cell_width: int | None = None
    cell_height: int | None = None
    alpha: bool | None = None
    notes: str = ""

@dataclass(slots=True, frozen=True)
class PromptRecipeSpec:
    base_positive: str = ""
    base_negative: str = ""
    injection_rule: str = ""        # 예: "{base_positive}, {subject}"
    subject_hint: str = ""

@dataclass(slots=True, frozen=True)
class VariantMetaSpec:
    schema_version: int = 1
    intent: str = ""
    use_cases: tuple[str, ...] = ()
    not_for: tuple[str, ...] = ()
    output_layout: OutputLayoutSpec = field(default_factory=OutputLayoutSpec)
    tags: tuple[str, ...] = ()
    prompt_recipe: PromptRecipeSpec = field(default_factory=PromptRecipeSpec)

# VariantSpec 확장 — 기존 필드 유지하고 meta 추가
@dataclass(slots=True, frozen=True)
class VariantSpec:
    # ... (기존 필드들 그대로)
    meta: VariantMetaSpec = field(default_factory=VariantMetaSpec)
```

> **권장**: yaml 파싱은 `pydantic.BaseModel` 로 한 후 dataclass 로 변환하면
> 검증/에러 메시지 품질이 좋다.

---

## 4. API 변경 — `/api/workflows/catalog`

### 4.1 Before (현재 — version 1)

```jsonc
{
  "version": 1,
  "categories": {
    "sprite": {
      "name": "Sprite",
      "description": "스프라이트 시트 생성",
      "variants": {
        "pixel_alpha": {
          "id": "sprite/pixel_alpha",
          "description": "픽셀 그리드 + 투명배경",
          "workflow_file": "Sprite_Illustrious_PoseGuided_Alpha_V38_api_pixel_alpha.json",
          "input_labels": {
            "prompt": {"description": "", "alternatives": []},
            "negative_prompt": {"description": "", "alternatives": []}
          },
          "outputs": [{"name": "image", "node_id": "...", "type": "image"}]
        }
      }
    }
  }
}
```

### 4.2 After (이 PR — version 2)

```jsonc
{
  "version": 2,                              // ← bump
  "categories": {
    "sprite": {
      "name": "Sprite",
      "description": "스프라이트 시트 생성",
      "variants": {
        "pixel_alpha": {
          // ─── 기존 필드 그대로 ─────────
          "id": "sprite/pixel_alpha",
          "description": "픽셀 그리드 + 투명배경",
          "workflow_file": "...",
          "input_labels": {
            "prompt": {
              "description": "캐릭터 묘사. base_positive 와 자동 합쳐짐.",
              "alternatives": ["subject", "character"]
            },
            "negative_prompt": {
              "description": "추가 negative. base_negative 와 append 됨 (override 불가).",
              "alternatives": []
            }
          },
          "outputs": [...],

          // ─── 신규 (meta) ──────────────
          "meta": {
            "schema_version": 1,
            "intent": "단일 캐릭터의 1×3 pose grid 시트를 1280×640...",
            "use_cases": ["게임용 캐릭터 스프라이트 시트 (3방향 포즈)", "..."],
            "not_for": ["단일 일러스트 (배경/씬 포함)", "..."],
            "output_layout": {
              "kind": "pose_grid",
              "width": 1280, "height": 640,
              "rows": 1, "cols": 3,
              "cell_width": 320, "cell_height": 480,
              "alpha": true,
              "notes": "각 셀은 같은 캐릭터, 다른 포즈"
            },
            "tags": ["sprite", "pose-grid", "transparent-bg", "..."],
            "prompt_recipe": {
              "base_positive": "pixel art, 1x3 pose grid, ...",
              "base_negative": "background, scenery, ...",
              "injection_rule": "{base_positive}, {subject}",
              "subject_hint": "캐릭터 묘사만. 배경/씬 묘사 금지."
            }
          }
        },
        "hires": {
          // meta yaml 없는 변형 (legacy) ──
          "id": "sprite/hires",
          // ...
          "meta": {}                          // ← 빈 객체 (forward-compat)
        }
      }
    }
  }
}
```

### 4.3 호환성 정책 (반드시 지켜야 함)

- `version: 1 → 2` bump.
- `meta` 키는 **항상 존재** (없으면 빈 객체 `{}`). 클라이언트는 `meta` 부재가 아닌
  `meta == {}` 또는 `meta.intent == ""` 로 legacy 판단.
- 기존 모든 필드는 *그 자리* 그대로. 위치/이름 변경 금지.
- input_labels 의 `description` 은 yaml 우선, 없으면 빈 문자열 (현 동작 유지).

---

## 5. 구현 단계 (이 순서대로 커밋 분리)

### Step 1 — Loader (`workflow_registry.py`)

1. `infer_meta(workflow_file: Path) -> VariantMetaSpec` 함수 신규
   - 같은 디렉토리에 `<variant>.meta.yaml` 사이드카 찾음
   - 없으면 `VariantMetaSpec()` 빈 객체 반환
2. `WorkflowRegistry._load_variant()` 안에서 `infer_meta()` 호출, `VariantSpec.meta` 채움
3. **JSON Schema 검증**: pydantic BaseModel 로 yaml 파싱 → `schema_version` 미스매치는 `WorkflowRegistryError`
4. `input_labels.<label>.description` 은 yaml 우선, 없으면 기존 `infer_input_labels` 의 빈 문자열

### Step 2 — API 직렬화 (`server.py`)

5. `_variant_to_dict(v: VariantSpec) -> dict` (또는 동일 역할 헬퍼) 에 `meta` 필드 추가
   - dataclass → dict 는 `dataclasses.asdict()` 또는 수동 변환
   - tuple → list, frozen dataclass → dict
6. catalog 응답 root `version: 2` 로 bump

### Step 3 — Tests

7. `tests/test_workflow_registry.py` 신규 테스트:
   ```python
   def test_meta_yaml_loaded(tmp_path):
       # pixel_alpha.meta.yaml 작성
       # WorkflowRegistry 로드
       # variant.meta.intent 검증
       assert "pose grid" in variant.meta.intent.lower()
       assert variant.meta.output_layout.kind == "pose_grid"

   def test_meta_missing_returns_empty(tmp_path):
       # meta.yaml 없는 변형 로드
       # variant.meta == VariantMetaSpec()
       assert variant.meta.intent == ""
       assert variant.meta.output_layout.kind is None

   def test_meta_schema_violation_raises(tmp_path):
       # schema_version: 99 인 yaml 작성
       # WorkflowRegistry 로드 시
       with pytest.raises(WorkflowRegistryError):
           registry.load()
   ```

8. `tests/test_workflow_endpoints.py` 신규 테스트:
   ```python
   def test_catalog_v2_response_shape(client):
       res = client.get("/api/workflows/catalog")
       assert res.json()["version"] == 2
       v = res.json()["categories"]["sprite"]["variants"]["pixel_alpha"]
       assert "meta" in v
       assert v["meta"]["output_layout"]["kind"] == "pose_grid"
       assert isinstance(v["meta"]["use_cases"], list)

   def test_catalog_legacy_variant_has_empty_meta(client):
       # meta.yaml 없는 변형 (e.g., sprite/hires) 응답에 meta == {} 확인
       res = client.get("/api/workflows/catalog")
       v = res.json()["categories"]["sprite"]["variants"]["hires"]
       assert v["meta"] == {} or v["meta"]["intent"] == ""
   ```

### Step 4 — Migration data (이 PR 에서는 1개만)

9. `workflows/sprite/pixel_alpha.meta.yaml` 작성 — 위 §3.2 예시 그대로
10. **이 PR 에는 다른 변형 yaml 추가 금지** (PR 단위 작게). 별 PR 들로:
    - `sprite/{hires,rembg_alpha,full,...}`
    - `illustration/*`
    - `pixel_bg/*`
    - `icon/flat`

### Step 5 — Docs

11. `~/workspace/sincerity-skills/asset-factory-api/SKILL.md` 갱신 (별 PR):
    - "변형 선택 체크리스트" 섹션 추가
    - freshness 표에서 §A 채워짐 표시

> **이 핸드오프 PR 범위**: Step 1~4 + Step 5 의 NEXT.md freshness 한 줄만.
> SKILL.md 갱신은 별 PR (sincerity-skills 레포에서).

---

## 6. 테스트 전략

| 분류 | 무엇을 검증 | 어디 |
|---|---|---|
| 단위 | yaml 파싱 — 스키마 / forward-compat / `schema_version` 검증 | `tests/test_workflow_registry.py` |
| 통합 | `/api/workflows/catalog` 응답 shape — `meta` 키 존재 / 타입 | `tests/test_workflow_endpoints.py` |
| 회귀 | 기존 catalog 사용처 (e.g., `cli/commands/workflow.py:catalog`) 가 `meta` 부재에서도 안 깨지는지 | 기존 테스트 그대로 통과 |

**실행**: `pytest tests/test_workflow_registry.py tests/test_workflow_endpoints.py -v`

전체 회귀: `pytest -v` (다른 테스트 안 깨져야 함).

---

## 7. 결정 메모 (왜 이렇게 안 하는가)

- **per-variant yaml** vs `registry.yml` 거대화 → **분리**. 변형 PR 단위가 작아지고
  충돌 적다. registry 는 호출 매니페스트 책임, meta 는 사용자 노출 책임.
- **스키마 버저닝**: `schema_version: 1` 명시. 미래 breaking 변경 시 `2` bump
  후 loader 분기.
- **`output_layout.kind` 닫힌 enum**: Literal 4개로 시작
  (`single | pose_grid | tile_grid | character_sheet`). 새 형태 추가는 enum
  확장 PR.
- **i18n**: `intent`/`use_cases` 한국어/영어 혼용 OK. 검색 룰(§C) 이 다국어
  지원하면 됨.
- **legacy 호환 = 자동 감지**: feature flag/환경변수 분기 X. **데이터 부재 →
  legacy fallthrough** (`meta == {}`).
- **`base_negative` append-only**: 사용자가 negative override 못 함. 안전 장치
  (예: "transparent background" 가 빠지면 sprite 가 망가짐). §1.B 에서
  강제됨.

---

## 8. 작업 완료 체크리스트 (PR submit 전)

- [ ] `infer_meta()` 구현, `VariantSpec.meta` 채워짐
- [ ] `schema_version` 검증 → `WorkflowRegistryError`
- [ ] `_variant_to_dict()` 에 `meta` 직렬화 추가
- [ ] catalog 응답 `version: 2`
- [ ] `workflows/sprite/pixel_alpha.meta.yaml` 작성 (§3.2 그대로)
- [ ] 단위 테스트 3개 (`test_meta_yaml_loaded`, `test_meta_missing_returns_empty`, `test_meta_schema_violation_raises`)
- [ ] 통합 테스트 2개 (`test_catalog_v2_response_shape`, `test_catalog_legacy_variant_has_empty_meta`)
- [ ] `pytest -v` 전체 통과 (회귀 0)
- [ ] PR 본문에 "이 PR 은 §1.A 만 (변형 1개). 다른 변형 yaml 은 별 PR" 명시
- [ ] PR 본문에 `Refs: docs/NEXT.md §1.A (PR #21)` 링크
- [ ] `docs/NEXT.md` 의 freshness/진행 상태 표에 §1.A 완료 체크 (있으면)
- [ ] 머지 후 §1.B/§1.C 핸드오프 작성을 메인 사용자(멍멍) 에게 알림
  → 그래야 §B/§C prompt 가 실제 yaml 결과 (이번 PR) 를 참조 가능

---

## 9. 후속 작업 (이 PR 범위 밖)

머지 후 다른 핸드오프 prompt 가 별도로 발행됨:

- **§1.B** — 공통 prompt 강제 주입 (P0). `prompt_recipe.base_positive` 를 서버에서
  자동 prepend. `docs/NEXT.md` `## 1.B` (L407–L645).
- **§1.C** — 변형 의도 기반 인덱스/자연어 검색 (P1). `tags`/`use_cases`/`not_for`
  로 룰 기반 매칭. `docs/NEXT.md` `## 1.C` (L647–L775).

§B/§C 는 §A 의 *실제 yaml 결과* (`pixel_alpha.meta.yaml`, `VariantMetaSpec`
dataclass) 를 import 해서 사용하므로 §A 머지 *후* 핸드오프 작성 필요.

---

## 10. 막힐 때

- 카탈로그/registry 구조 모호 → `docs/NEXT.md` `## 0.1` 다이어그램 (L14–L52) 다시
  읽기
- pydantic vs dataclass 선택 → pydantic 으로 yaml 파싱 → dataclass 로 변환
  (검증/에러 메시지 좋고, 런타임은 frozen dataclass 가 안전)
- 메인 사용자(멍멍) 에게 질문할 때 → "**§1.A 작업 중, [구체 위치]: [구체 질문]**"
  형식. 추측 금지.
