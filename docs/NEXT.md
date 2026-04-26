# NEXT — Asset Factory 후속 작업

> 이 문서는 **운영 회고 + 다음 액션 트래커**다.
> 스킬 관점 갭(`docs/TODOS_for_SKILL.md`)은 PR #18 머지로 흡수돼 더는 필요 없어 폐기.
> 이 NEXT.md 가 그 후속이다.

---

## 1. smoke 회고 — 왜 워크플로우를 제대로 못 썼나

### 사건

- `sprite/pixel_alpha` 로 단일 시연 이미지를 뽑으려 했는데, **이 변형은 1280×640 가로 시트에 1×3 포즈 그리드(=3방향 캐릭터)** 를 그리는 용도다. 단일 이미지 변형이 아니다.
- 결과물도 의도된 3컷이 아니라 **학교 복도에 캐릭터 2명이 있는 씬 일러스트** 로 빠짐 (vision 분석 확인).
- 즉 카테고리 인지 실패 → 부적절한 prompt → 워크플로우 의도와 동떨어진 산출물.

### 근본 원인 (catalog API의 정보 부족)

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

### 보완해야 할 작업

#### A. 카탈로그 API 풍부화 (P0)

`af workflow describe <variant>` (= `GET /api/workflows/catalog`) 응답에 **사용 의도/제약 컨텍스트** 를 명시 노출:

```jsonc
{
  "category": "sprite",
  "variant": "pixel_alpha",
  "intent": "3-pose character sheet (1×3 grid, transparent BG) for 2D game engine import",
  "use_cases": [
    "RPG character with idle/walk-side/walk-front 3 pose set",
    "Top-down RPG sprite atlas seed"
  ],
  "not_for": [
    "single character portrait — use illustration/animagine_hires",
    "scene/background — use pixel_bg/*",
    "logo or app icon — use icon/flat (currently broken, see #18)"
  ],
  "output_layout": {
    "kind": "pose_grid",
    "rows": 1, "cols": 3,
    "pose_template": "pose_grid_1x3_mini_2.5h_1280x640.png",
    "per_cell_size": [426, 640],
    "alpha": true
  },
  "prompt_recipe": {
    "subject_required": ["1girl|1boy", "consistent outfit description"],
    "subject_forbidden": ["scenery", "background props", "multiple characters in different sizes"],
    "style_locked_by_workflow": ["pixel art", "transparent bg"]
  },
  "input_labels": [
    {
      "label": "pose_image",
      "required": false,
      "default": "pose_grid_1x3_mini_2.5h_1280x640.png",
      "description": "ControlNet 포즈 가이드. 1×3 그리드 PNG. 같은 카테고리의 다른 grid (1x4, 3x3) 로 교체 가능 — 변형의 출력 레이아웃이 grid 에 맞춰 바뀐다.",
      "alternatives": [
        "pose_grid_1x3_5h_1280x640.png",
        "pose_grid_1x4_1280x640.png",
        "pose_grid_3x3_1280x896.png"
      ]
    }
  ]
}
```

핵심 추가 필드:
- **`intent` (한 줄)** — 변형의 본질
- **`use_cases` / `not_for`** — 에이전트가 잘못 매칭하는 걸 방지
- **`output_layout`** — grid 인지 단일인지, 셀 크기, alpha 여부
- **`prompt_recipe`** — subject/style 제약 (negative prose 의 구조화)
- **`input_labels[].description` 채우기 + `alternatives`** — pose_grid 종류별 의미

이게 채워지면 에이전트는 변형 선택 단계에서 의도 일치 검증이 가능하다.

#### B. 공통 프롬프트를 워크플로우에 박고 사용자는 *주제만* 주입 (P0)

§A 가 "에이전트가 잘못 고르는 사고" 를 막는 안전장치라면, 이건 **고른 변형 안에서 사용자가 잘못 짜는 사고** 를 막는 안전장치다.

지금 흐름: 사용자가 prompt 통째로 작성 → ComfyUI 로 그대로. 결과:
- `sprite/pixel_alpha` 에 `"1girl, silver hair, school uniform, walking"` 만 넣었더니 *3-pose grid* / *pixel art* / *transparent bg* 의도가 prompt 에 0% 반영. 워크플로우의 controlnet/pixelize 노드 강도만으로 형태를 강제하는데 이게 약하면 그대로 학교 복도 씬으로 빠진다.
- 사용자/에이전트가 변형 의도를 *prompt 에 다시 적어야* 안전한 구조 — 멘탈 부담 + 반복 작성.

**제안 구조**: 워크플로우 변형이 두 종류 prompt 섹션을 가짐.

```yaml
# workflows/sprite/pixel_alpha.meta.yaml (예시)
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
    label: "subject"
    description: "캐릭터 묘사만 (외형/복장/무기). 스타일·구도·배경 묘사 금지."
    examples:
      - "1girl, silver hair twin tails, navy school uniform, holding a notebook"
      - "1boy, brown spiky hair, blue tunic, leather belt with sword"
    required: true
  injection_rule: "{base_positive}, {subject}"   # 또는 "{subject}, {base_positive}"
```

서버 동작:
- `POST /api/workflows/generate` 가 `prompt` 를 받으면 *그게 user_slot 값* 으로 해석.
- 서버가 `injection_rule` 따라 `base_positive` 를 합쳐 ComfyUI 로 전달.
- `base_negative` 는 항상 합쳐짐. 사용자 `--negative` 는 *추가* (override 아님).
- 응답에 *최종 합쳐진 prompt/negative* 를 노출 — 디버깅용.

API 호환:
- 기존 `--negative` / 통째 prompt 입력은 *호환 모드* 유지 (legacy=true 플래그 또는 자동 감지: prompt 가 200자 초과 + style 키워드 다수면 legacy).
- 신규 권장 경로: `--subject "1girl ..."`, `--style-extra "..."` (선택).
- skill 문서는 신규 경로만 안내. legacy 는 deprecation.

### 검증/UX 효과:
- *catalog 풍부화 (§A)* + *prompt 강제 (§B)* 가 같이 가야 의미가 산다. §A 만 있으면 사용자가 무시할 수 있고, §B 만 있으면 *왜 이 base 가 박혀있나* 의 컨텍스트가 안 보인다.
- §B 의 `base_*` 는 §A 의 `prompt_recipe.subject_required/forbidden` 의 *실행 가능 형태* — 두 필드가 한 yaml 에서 단일 진실 원천.

### 마이그레이션 단계:
1. 변형 1개 (`sprite/pixel_alpha`) 에 `prompt_template` yaml 부착, generate 핸들러 분기.
2. 응답에 `final_prompt` / `final_negative` 노출 → 기존 사용처 회귀 없는지 검증.
3. 다른 변형 순차 이전 (`illustration/*`, `pixel_bg/*`, `icon/*`).
4. legacy 모드 deprecation 공지.

#### C. 변형 의도 기반 인덱스 / 자연어 검색 (P1)

§A·§B 가 *데이터를 만드는* 작업이라면, 이건 *그 데이터를 쓰는* 작업.

지금 에이전트는 카탈로그 전체 JSON 을 받아서 prompt 안에 다 넣고 LLM 더러 골라달라는 구조. 변형 5개일 땐 OK, 30개 넘어가면 컨텍스트 낭비 + 오선택 빈도 증가.

**필요한 형태**:

```bash
# 1. 자연어 → 변형 추천
af workflow recommend "RPG 게임 캐릭터 정면/측면/뒷면 픽셀 스프라이트"
# → ranked candidates:
#    1. sprite/pixel_alpha     (score 0.92)  ✓ 정확히 이 용도
#    2. sprite/v37_pixel       (score 0.71)  legacy
#    3. illustration/pony_hires (score 0.31)  픽셀 아님 — not_for 매칭
#
# 각 후보의 intent / use_cases / not_for 를 한 줄씩 출력.

# 2. 의도 키워드 색인
af workflow search --tag "transparent-bg" --tag "pose-sheet"
af workflow search --not "scenery"
```

**구현 옵션** (가벼운 → 무거운 순):
1. **룰 기반**: `use_cases` / `not_for` / `category` 의 키워드 매칭. yaml 만 잘 쓰면 충분.
2. **임베딩 기반**: `intent + use_cases` 를 임베딩 → cosine. 변형 30+ 개 넘어가면 가치. 모델은 작은 거(BGE-m3 등) 로 로컬.
3. **LLM 분류**: 사용자 의도 + 변형 카탈로그 → LLM 이 선택 (에이전트 자체 역할). 가장 무겁지만 가장 유연.

**권장 시작점**: §A 데이터가 채워지는 순간 (1) 룰 기반이 즉시 쓸만함. (2) 임베딩은 변형 수가 늘었을 때 추가. (3) 은 에이전트 측 책임으로 두고 서버는 (1)+(2) 만 제공.

#### D. catalog 외 보강

- `--dry-run` (`docs/TODOS_for_SKILL.md` ⏳ 미충족 항목, 이 문서로 흡수): prompt + workflow 가 의도와 맞는지 패치 결과만 보여주고 ComfyUI 호출 안 함. **§B 합쳐진 final_prompt 노출도 dry-run 의 핵심 기능.**
- skill (`asset-factory-api`) 의 *"변형 선택 체크리스트"* 섹션 추가:
  > 1. `intent` 가 사용자 요청과 정합한가?
  > 2. `output_layout` 이 기대치와 맞나? (단일 vs 그리드)
  > 3. `not_for` 에 사용자 요청 유형이 들어있지 않나?
  > 4. **사용자가 prompt 통째 작성 안 하고 `--subject` 만 주입했나? (§B)**

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
      "traceback": "..."  // 또는 별도 endpoint
    }
  ]
}
```

이게 있었으면 `icon/flat` 회귀를 1분 안에 진단했을 일이다.

### P1 — 다음 사이클

#### 2.3 카탈로그 API 풍부화 (1번 절의 §A)

위 §1.A 의 `intent`/`use_cases`/`not_for`/`output_layout`/`prompt_recipe` 필드 추가.
- 데이터 출처: 변형마다 yaml metadata 파일 또는 워크플로우 JSON 의 별도 메타 섹션.
- `workflow_registry` 에서 로드 → `/api/workflows/catalog` 응답에 머지.
- 마이그레이션: 비어 있는 변형은 빈 객체로 forward-compat.

#### 2.3b 공통 prompt 강제 주입 (§1.B)

워크플로우마다 `prompt_template.base_positive/base_negative` 박고, 사용자는 `--subject` 슬롯만 주입. legacy 호환 유지하며 점진 마이그레이션.
- 첫 변형: `sprite/pixel_alpha` (1×3 그리드 의도가 prompt 에 0% 반영되던 사고의 진앙)
- 응답 `final_prompt` / `final_negative` 노출
- skill 문서는 신규 경로만 안내

#### 2.3c 변형 의도 기반 인덱스/검색 (§1.C)

`af workflow recommend "<자연어 의도>"` + `af workflow search --tag` — 룰 기반부터 시작, 변형 30+ 시 임베딩 추가. catalog 풍부화(2.3) 완료 후 즉시 가능.

#### 2.4 `icon/flat` 외 로고 전용 변형 추가 검토

`icon/flat` 은 Animagine 기반 (애니/일러스트 톤). v3 로고 실험에서 효과 본 *duolingo/토스 chunky flat* 톤은 `dreamshaper_8` 기반 SD1.5 가 가장 안정적이었다. 후속 변형 `icon/logo_chunky` (혹은 `icon/dreamshaper_logo`) 를 검토.

#### 2.5 `--dry-run` 플래그 (`docs/TODOS_for_SKILL.md` ⏳ 흡수)

`af workflow gen ... --dry-run` → prompt + 워크플로우 패치 결과만 출력하고 ComfyUI dispatch 안 함. catalog 풍부화와 같이 가면 *"이 변형이 내 의도에 맞나"* 까지 사전 점검 가능.

#### 2.6 `aliases` (`@character` 등) (`docs/TODOS_for_SKILL.md` ⏳ 흡수)

자연어 키워드 → variant alias. 이건 catalog 풍부화 + LLM 매칭으로 부분 해결 가능하므로 우선순위 낮음.

#### 2.7 `run:<run_id>/<output>` 입력 syntax (`docs/TODOS_for_SKILL.md` ⏳ 흡수)

현재 `--input source_image=asset:<id>` 로 부분 충족. 다음 단계는 *특정 run 의 특정 output label* 직접 참조 — `--input src=run:abc/pixel_alpha`. chain 단순화에 큰 도움.

### P2 — 운영 편의

#### 2.8 `assets` 응답에 `approval_mode` 직렬화

PR #18 코멘트로 보고된 follow-up. `--bypass-approval` 로 호출했는데 응답의 `approval_mode` 가 `"manual"` 로 나오는 (smoke 재확인됨). 직렬화 수정.

#### 2.9 `excluded_bypassed` 메타 검증 강화

빈 프로젝트에서도 bypass 제외 카운트가 정확히 0 인지 + null 회피.

#### 2.10 sprite/pixel_alpha validation 메시지 보강

현재 `validation_message: "팔레트 초과: 6250색 (최대 32색)"` 만. 사용자 입장에선 *왜 32색이 한도인지* 와 *어떻게 줄이는지* 가 더 가치있다. workflow 변형 자체가 픽셀화를 충분히 안 한 케이스인지, prompt 가 너무 복잡했던 건지 분간 어렵다. 문맥 첨부:
```
validation_message: "팔레트 초과: 6250색 (한도 32색).
  원인 후보: ① 워크플로우 픽셀화 단계 (PixelOE) 결과가 약함 — controlnet_strength↑ 시도.
            ② 프롬프트가 너무 사실적 — 'pixel art, low color, limited palette' 키워드 추가.
            ③ stage1 출력은 1280×640 에 6250색이 정상이고 pixel_alpha 단계 자체가 fail."
```

#### 2.11 logo-agent v4 후속 — 진짜 로고 검증 시연

`icon/flat` 회귀 (2.1) 가 풀린 직후 docs/logo-agent.md v4 실제로 한 번 돌려서 결과물 첨부. 그래야 v4 가 실측 검증된 문서가 됨.

---

## 3. 진행 상태

| 항목 | 상태 |
|---|---|
| sincerity-skills PR #4 (post-PR#18 정리) | ✅ 머지됨 |
| asset-factory PR #18 (P0 갭) | ✅ 머지됨 |
| asset-factory PR #19 (logo-agent v4) | ✅ 머지됨 |
| `docs/TODOS_for_SKILL.md` 폐기 | ✅ 이 문서로 흡수, 파일 삭제 예정 |
| 2.1 icon/flat 회귀 | 🔴 별도 개발자 |
| 2.2 task-level error 캡처 | 🟡 별도 개발자 |
| 2.3 카탈로그 API 풍부화 (§1.A) | 🟡 설계 단계 |
| 2.3b 공통 prompt 강제 주입 (§1.B) | 🟡 설계 단계 |
| 2.3c 의도 기반 인덱스/검색 (§1.C) | 🔵 backlog (2.3 후) |
| 2.4–2.11 | 🔵 backlog |

---

## 4. 결정 메모

- **카탈로그 풍부화(§A)가 1순위 인 이유**: 에이전트가 *잘못 매칭* 하는 사고는 호출 회수만 늘리는 게 아니라 사람이 cherry-pick 으로 전부 거르고 다시 시키는 비용이 든다. 카탈로그 한 줄에 `intent` 가 있었으면 sprite/pixel_alpha 를 단일 시연용으로 쓰는 실수는 안 일어났다.
- **§B (공통 prompt 강제) 가 §A 와 짝이어야 하는 이유**: §A 는 *문서*, §B 는 *실행*. 문서만 풍부해도 사용자가 prompt 통째 짜는 흐름이면 의도가 prompt 에 반영 안 된다. 1×3 grid 의도가 워크플로우 yaml 에 박혀 있고 사용자는 `--subject "1girl, ..."` 만 주는 구조여야 *카탈로그 description ↔ 실제 prompt* 가 동기화된다.
- **§C (의도 인덱스) 는 §A 의 데이터를 재활용** — yaml 에 `intent` / `use_cases` / `not_for` 가 채워지면 룰 기반 검색은 거의 공짜. 임베딩은 변형 수 임계점(30+) 넘기 전까지 보류.
- **`docs/TODOS_for_SKILL.md` 폐기 사유**: TDD red phase 문서였고 PR #18 머지로 green 됐다. 잔여 항목 (`--dry-run`, `aliases`, `run:<id>/<out>` syntax) 은 본 NEXT.md 의 P1 으로 흡수.
- **logo-agent v4 의 신뢰도**: `icon/flat` 회귀가 풀리기 전엔 v4 docs 가 *이론 검증* 만 된 상태. 2.11 시연을 거쳐야 production 수준.
