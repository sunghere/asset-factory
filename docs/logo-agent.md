# 앱 브랜드 로고 자동 생성 에이전트 (Logo Agent v4)

> **역할**: 앱 컨셉을 받아 분석하고, 독꾸미 로고 가이드라인을 만족하는
> **프롬프트**를 조립한 뒤 **`af workflow gen icon/flat`** 으로 후보 N장을 enqueue 한다.
> 모델/LoRA/step/cfg/sampler/해상도는 워크플로우 변형이 내장하므로 에이전트가 만지지 않는다.
> 이미지 생성·검증·저장·cherry-pick 큐 등록은 전부 Asset Factory가 처리한다.
>
> 이 MD는 에이전트(Claude 등 외부 LLM)가 로드해 쓰는 **도메인 스킬 문서**다.
> SD/ComfyUI 일반 지식 — 호출 방식, 카탈로그 탐색, 결과 회수 — 은 `asset-factory-api`
> 스킬을 따른다. 이 문서는 **"로고"라는 도메인에 한정된 지식**만 담는다.

---

## 0. v3 → v4 변경 요약 (필독)

| 항목 | v3 (구) | v4 (지금) |
|---|---|---|
| 백엔드 | A1111 `/sdapi/v1/txt2img` | **ComfyUI 워크플로우** (`icon/flat`) |
| 호출 | `POST /api/batches` JSON 조립 (8장 일괄) | **`af workflow gen icon/flat ...`** 한 줄 ×N (또는 `--candidates N`) |
| 모델 선택 | 에이전트가 `dreamshaper_8` / `juggernautXL` 결정 | **변형이 내장** — 에이전트 관여 X |
| 곱집합 | `prompts × models × loras × seeds_per_combo` 직접 계산 | 호출 1번당 1 prompt × seed; N장은 N번 호출하거나 `--candidates` |
| 카테고리 | `category: "logo"` | **`icon/flat`** (현 카탈로그에 `logo` 카테고리 없음) |
| 승인 | 항상 cherry-pick 큐 | 후보 8장은 cherry-pick, 단발 시뮬은 `--bypass-approval` |

**핵심 결과**: 에이전트의 작업은 *프롬프트와 negative 를 잘 짜는 일* 로 좁혀졌다.
JSON·SD 파라미터·모델 이름은 손대지 않는다.

---

## 1. 언제 이 에이전트를 쓰나

다음 같은 요청이 오면 이 워크플로우를 탄다:

- "X 앱 로고 만들어줘"
- "이 컨셉으로 브랜드 로고 뽑아줘"
- "앱 이름 + 설명 줄 테니 로고 후보 8장 주라"

트리거 키워드: `앱 로고`, `브랜드 로고`, `app icon`, `로고 생성`, `브랜드 아이콘`.

선행 조건: `asset-factory-api` 스킬을 함께 로드해 `af` CLI 사용법·카탈로그 구조를
숙지한 상태여야 한다. 이 문서는 그 위에 **로고 도메인** 만 얹는다.

---

## 2. 전체 흐름

```
[사용자 → 앱 컨셉 입력]
        │
        ▼
[에이전트(나)]
  1. 컨셉 분석 (키워드 / 무드 / 상징 / 팔레트 추출)
  2. 가이드라인 → 프롬프트 규칙 매핑
  3. positive/negative 프롬프트 N개 조립 (보통 4 or 8)
        │
        ▼
af workflow gen icon/flat <project> <asset_key>_v{i} "<prompt_i>" \
  --negative "<NEG>" --candidates 1 [--seed S] [--workflow-params '{...}']
   ※ 8장 필요하면 N번 반복 호출 (또는 한 호출에 --candidates 8)
        │
        ▼
[Asset Factory · ComfyUI]
  - icon/flat 워크플로우 (Animagine 기반) 1024×1024 alpha 출력
  - 후보 저장 → cherry-pick 큐 등록
        │
        ▼
[사람 → /cherry-pick 갤러리에서 1장 선택]
```

**에이전트가 직접 하지 않는 것**: ComfyUI 호출, 모델 선택, 이미지 저장, 검증, 리사이즈,
승인 처리. 이것들은 전부 Asset Factory + 사람이 담당한다.

---

## 3. 입력 스키마 (사용자 → 에이전트)

필수는 `app_name`과 `description`만. 나머지는 비어 있으면 에이전트가 추론한다.

```jsonc
{
  "app_name": "독꾸미 메모",                // 필수
  "description": "귀엽고 간단한 메모 앱",   // 필수
  "category": "생산성",                      // 선택 (예: 게임, 유틸, 생산성, 소셜, 교육)
  "mood": ["친근함", "깔끔함", "경쾌함"],   // 선택
  "brand_color": "#FF6B9D",                  // 선택 (없으면 에이전트가 제안)
  "target_audience": "20-30대 여성",         // 선택
  "count": 8                                  // 선택 (기본 8)
}
```

자연어 요청만 와도 1~2개 질문으로 빠르게 채우거나 합리적 기본값으로 진행한다.

---

## 4. 독꾸미 로고 가이드라인 → 프롬프트 규칙

| # | 가이드 | 프롬프트 반영 |
|---|---|---|
| 1 | 배경색 필수 (흰색/투명 금지) | positive: `solid {brand_color} background, filled background, colored canvas` / negative: `white background, transparent background, checkered background, no background` |
| 2 | 다크모드 대비 고려 | brand_color가 너무 어두우면 명도 보정된 보조 컬러 제안 (HSB Brightness ↑) |
| 3 | 심볼 중심, 단순 형태 | positive: `minimal symbol, iconic logo mark, simple geometric shape, bold silhouette, single focal element, flat vector style` / negative: `text, letters, typography, wordmark, signature, watermark, multiple objects, cluttered` |
| 4 | 원형 그리드, 중앙 정렬 | positive: `centered composition, circular grid aligned, symmetrical, balanced` |
| 5 | 작은 크기 식별성 | positive: `high contrast, bold shape, clear silhouette, readable at small sizes, app icon` |
| 금지 | 텍스트 결합형 | negative에서 text / letters / typography / wordmark 강제 차단 |

> 이 가이드는 백엔드(A1111/ComfyUI)와 무관하다. 모델이 바뀌어도 그대로 유효.

### 4.1 Positive Prompt 템플릿 (flat app-icon 스타일)

**v3 실험으로 확정된 스타일 베이스, v4에서도 동일하게 유효**.
모든 prompt 항목에 아래 `STYLE` 부분을 붙여라.

```
{subject_hint}, minimalist flat icon, modern ios app icon, duolingo style app icon,
clean vector illustration, simple rounded shapes, chunky design,
centered single focal symbol, symmetrical composition, thick outline,
solid {brand_color_name} background, {brand_color_hex} canvas, filled colored background,
high contrast, clean bold edges, readable at small sizes,
professional brand identity, 2d flat design
```

- `{subject_hint}`: 앱 기능+가치를 담은 **합성 심볼** (§5.5 공식 참고).
  - `flat icon of a checklist notepad with a small blue money bag on top`
  - `flat icon of a blue money bag with a big white check mark on it`
  - `flat icon of a dollar symbol inside a rounded checkbox`
- `{brand_color_name}`: 자연어 색상명 (`vivid blue`, `hot pink`, `mint green` …).
- `{brand_color_hex}`: 참고용 hex. SD는 hex를 완벽히 못 읽지만 톤 힌트는 됨
  (예: `#3182F6 toss blue`).

> 핵심 키워드: `duolingo style app icon`, `ios app icon`, `chunky`, `thick outline`, `2d flat design`.
> 이 4개가 들어가면 "토스/듀오링고 느낌"이 안정적으로 나온다.

### 4.2 Negative Prompt 템플릿 (고정)

```
text, letters, words, numbers, typography, wordmark, signature, watermark, caption,
white background, transparent background, checkered background, no background, alpha channel,
realistic photo, 3d render, photograph, skeuomorphic,
cluttered, busy composition, collage, grid of icons, sticker sheet,
distorted, cropped, blurry, low quality, jpeg artifacts,
gradient mesh, drop shadow, bevel, emboss, lens flare,
human hand, face, character, people,
realistic fabric, realistic leather, western cowboy bag, burlap sack
```

> ⚠️ `icon/flat` 워크플로우는 결과 중 하나로 **alpha(투명배경)** 출력을 만든다.
> 우리 가이드라인은 **배경색 채움**을 원하므로, *cherry-pick 갤러리에서 raw(stage1) 출력을
> 우선 보고* alpha 는 후처리/export 용으로만 본다. negative 의 `transparent background,
> alpha channel` 은 *생성 단계*에서 흰/투명 배경이 나오는 걸 막는 역할이지, 워크플로우
> 알파 추출 자체를 막지는 않는다.

> `skeuomorphic`, `realistic fabric`, `burlap sack`, `western cowboy bag` 는 v3에서
> "서양식 밀가루 자루" 같은 돈주머니가 나오는 걸 막기 위해 넣었다. 타 카테고리도
> 재질이 너무 사실적으로 빠지는 걸 방지해준다.

---

## 5. 컨셉 분석 체크리스트 (에이전트가 수행)

입력을 받으면 **프롬프트 조립 전에** 아래를 머리 속에서 정리:

1. **상징 후보 3~5개**
   - 앱 이름/기능에서 직접 유추되는 오브젝트 (예: 메모 앱 → 종이/펜/말풍선/체크)
   - 추상적 메타포 (예: 생산성 → 상승 화살표/별/체크마크)
   - 브랜드 특유 요소 (예: 독꾸미 → 여우/강아지 실루엣)

2. **무드 → 형태 언어**
   - 친근함 → `rounded corners, soft curves`
   - 모던/시크 → `sharp angles, geometric, monoline`
   - 경쾌함 → `dynamic angle, diagonal, asymmetric balance`
   - 신뢰 → `stable, grounded, hexagon / shield shape`

3. **컬러**
   - `brand_color` 제공됨 → 그대로.
   - 없음 → 카테고리 + 무드 기반 2~3개 후보 제시 후 그 중 하나 선택.
     - 생산성/친근 → 파스텔 핑크/민트
     - 게임/경쾌 → 비비드 옐로/퍼플
     - 유틸/신뢰 → 딥블루/차콜
   - 컬러는 **자연어**가 메인, hex는 보조 메타.

4. **variation 전략 (8장 기준)**
   - **권장 A (다양성↑)**: prompt 4개 × seed 2 → `af workflow gen` 8번 호출.
     - 각 호출에서 `--seed` 명시하거나 비워 랜덤.
   - **권장 B (속도↑)**: prompt 1~2개 × `--candidates 8` 한 번 호출.
     - cherry-pick 후보가 더 균일하게 나오는 경향. 폭은 좁다.
   - **변형 단축**: `prompts × seeds` 곱집합을 머리 속으로 정확히 8 또는 12에 맞춰라.
   - **seed 가 구도 다양성에 진짜 중요함을 v3 실험에서 확인** — 같은 프롬프트도
     seed 바꾸면 비율/구도가 크게 달라진다. prompt 늘리기보다 seed 늘리기로
     충분할 때가 많다.

### 5.5 합성 심볼 공식 (핀테크/리워드 앱 로고 핵심)

> **"앱의 기능(A) + 제공 가치(B)"를 한 오브젝트에 녹여라.**
> 병렬 배치보다 **융합**이 강하다.

| 패턴 | 예시 |
|---|---|
| A가 B를 담고 있음 | `money bag opened showing a tiny checklist inside` |
| A 위에 B가 붙음 | `checklist notepad with a small blue money bag on top` |
| A가 B로 변형됨 | `dollar symbol inside a rounded checkbox` |
| A와 B가 한 기호로 | `blue money bag with a big white check mark on it` |

v3 실험에서 **"돈주머니 + 체크마크를 하나의 심볼로"** 접근이 가장 좋은 결과를 냈다.
기능과 가치가 "같이 있는 그림"이 아니라 **"하나로 합쳐진 기호"** 가 되도록 하는 게 핵심.

프롬프트는 `flat icon of a {합성 심볼}` 로 시작하면 SD가 단일 오브젝트로 해석할 확률이 높다.

---

## 6. 출력: `af workflow gen icon/flat` 호출

### 6.1 카테고리 / 변형

- **카테고리**: `icon`
- **변형**: `flat` (현재 유일한 ready 변형)
  - 기반: `D1_AppIcon_V36_api_default.json` (Animagine flat icon)
  - 출력: `stage1` (raw) + `alpha` (투명배경 자동 추출) — primary 는 `alpha`
  - defaults: `steps=25, cfg=7, sampler=dpmpp_2m, scheduler=karras, 1024×1024`
  - 에이전트는 위 파라미터를 **건드리지 않는다**. 필요시 `--workflow-params` 로
    `width/height` 정도만.

> 카탈로그에 `logo/*` 카테고리는 **없다**. v3 문서가 가정했던 "logo 카테고리"는
> ComfyUI 전환 후 사라졌고, 현 시점 로고 작업은 `icon/flat` 으로 흡수됐다.
> 카탈로그 변동을 의심하면 항상 `af workflow catalog` 로 직접 확인.

### 6.2 명령 형식

```bash
af workflow gen icon/flat <project> <asset_key> "<positive_prompt>" \
  --negative "<negative_prompt>" \
  --candidates 1 \
  [--seed N] \
  [--workflow-params '{"width":1024,"height":1024}'] \
  [--bypass-approval] \
  [--wait]
```

| 인자 | 의미 | 로고 권장값 |
|---|---|---|
| `<category>/<variant>` | 워크플로우 변형 | `icon/flat` 고정 |
| `<project>` | 프로젝트 슬러그 | 앱 이름 slug (예: `dokkumi-memo`). 시뮬용은 `tmp_` prefix |
| `<asset_key>` | 이 호출의 식별자 | `logo_v01`, `logo_v02`, … (호출당 유일) |
| `"<prompt>"` | positive prompt | §4.1 템플릿 + §5.5 합성 심볼 |
| `--negative` | negative prompt | §4.2 템플릿 |
| `--candidates N` | 한 호출당 후보 장수 | 보통 1 (다양성 위해 N번 호출). 균일 후보면 8 |
| `--seed N` | 시드 고정 | 비워 랜덤 권장. 재현 필요 시만 명시 |
| `--workflow-params` | 변형 defaults 오버라이드 | 일반적으로 비워둠. 해상도 변경 시 `{"width":..., "height":...}` |
| `--bypass-approval` | cherry-pick 큐 우회 | 시뮬/스캐치만. 정식 후보는 절대 X |
| `--wait` | 동기 대기 | 짧은 시뮬엔 편함. batch 8장은 비동기 권장 |

### 6.3 호스트 / 환경

- `af` CLI 가 알아서 `127.0.0.1:8000` 에 붙는다. 에이전트는 host 신경 X.
- ComfyUI 호스트는 `.env` 의 `SD_COMFYUI_HOST` (예: `192.168.50.225:8188`).
  서버가 알아서 사용하므로 에이전트 관여 X.

---

## 7. 완성 호출 예시

앱 컨셉:
```json
{
  "app_name": "독꾸미 메모",
  "description": "귀엽고 간단한 메모 앱",
  "category": "생산성",
  "mood": ["친근함", "깔끔함"],
  "brand_color": "#FF6B9D",
  "target_audience": "20-30대 여성",
  "count": 8
}
```

에이전트가 조립한 4개 프롬프트 × 시드 2개 = 8장 호출:

```bash
PROJECT="dokkumi-memo"
KEY_PREFIX="logo_$(date +%Y%m%d_%H%M)"
NEG='text, letters, words, numbers, typography, wordmark, signature, watermark, caption,
white background, transparent background, checkered background, no background, alpha channel,
realistic photo, 3d render, photograph, skeuomorphic,
cluttered, busy composition, collage, grid of icons, sticker sheet,
distorted, cropped, blurry, low quality, jpeg artifacts,
gradient mesh, drop shadow, bevel, emboss, lens flare,
human hand, face, character, people,
realistic fabric, realistic leather, western cowboy bag, burlap sack'

PROMPTS=(
  "flat icon of a stylized memo pad with a tiny sparkle, minimalist flat icon, modern ios app icon, duolingo style app icon, clean vector illustration, simple rounded shapes, chunky design, centered single focal symbol, symmetrical composition, thick outline, solid hot pink background, #FF6B9D canvas, filled colored background, high contrast, clean bold edges, readable at small sizes, professional brand identity, 2d flat design"
  "flat icon of an abstract speech bubble fused with a check mark, minimalist flat icon, modern ios app icon, duolingo style app icon, simple rounded shapes, chunky design, centered single focal symbol, symmetrical composition, thick outline, solid hot pink background, #FF6B9D canvas, filled colored background, high contrast, clean bold edges, readable at small sizes, 2d flat design"
  "flat icon of a geometric folded paper symbol, minimalist flat icon, modern ios app icon, duolingo style app icon, monoline geometric, centered single focal symbol, symmetrical composition, thick outline, solid hot pink background, #FF6B9D canvas, filled colored background, high contrast, clean bold edges, 2d flat design"
  "flat icon of a pencil tip with a sparkle abstract mark, minimalist flat icon, modern ios app icon, duolingo style app icon, simple rounded shapes, chunky design, centered single focal symbol, symmetrical composition, thick outline, solid hot pink background, #FF6B9D canvas, filled colored background, high contrast, 2d flat design"
)

i=1
for P in "${PROMPTS[@]}"; do
  for SEEDLABEL in a b; do
    af workflow gen icon/flat "$PROJECT" "${KEY_PREFIX}_v$(printf '%02d' $i)" "$P" \
      --negative "$NEG" \
      --candidates 1
    i=$((i+1))
  done
done
```

→ 총 8개 asset (`logo_20260501_1430_v01` … `_v08`) 가 cherry-pick 큐에 등록된다.

**한 번에 8장 (속도 우선)**:

```bash
af workflow gen icon/flat dokkumi-memo logo_20260501_1430 "$PROMPTS_0" \
  --negative "$NEG" --candidates 8
```

→ 단일 프롬프트 × 8 시드. 다양성은 적지만 균일하고 빠름.

---

## 8. 사용자에게 돌려줄 결과 메시지 템플릿

```
✅ 로고 후보 enqueue 완료

• 앱: 독꾸미 메모
• project: dokkumi-memo
• asset_key prefix: logo_20260501_1430_v01..v08
• 변형: icon/flat (Animagine flat icon, 1024×1024)
• 생성 예정: 8장 (약 N분)
• Cherry-pick: http://localhost:8000/cherry-pick?project=dokkumi-memo

생성 완료되면 갤러리에서 1장 골라줘.
(alpha 출력은 export 단계용 — raw/stage1 보고 결정)
```

---

## 9. 후처리 (Asset Factory 쪽 / export 파이프라인)

가이드라인은 최종 전달 규격을 다음과 같이 요구한다:

- 정방형: **380px** 권장
- 가로형: **최대 450px** 이하

`icon/flat` 은 1024×1024 로 산출하므로, 사람이 cherry-pick 으로 1장 승인한 뒤
export 시점에 앱 스토어/웹 규격으로 리사이즈한다. 알파 출력(`alpha` label)은
투명배경이 필요한 export 채널용으로 별도 보관.
→ **에이전트는 생성 요청까지만**. 리사이즈/export 는 사용자/export 파이프라인 담당.

---

## 10. Pitfalls (학습된 실수들)

1. **카테고리 추측 금지** — `logo` 카테고리는 카탈로그에 **없다**. 항상
   `af workflow catalog` 로 ready 인 변형을 확인한 뒤 `icon/flat` 같이 정확히 명시.
2. **모델/LoRA/step/cfg 손대지 마라** — 변형 defaults 가 알아서 한다.
   괜히 `--workflow-params` 로 `model_name` 같은 거 넣으려 시도 X (스키마 거부됨).
3. **hex 색상을 prompt에 단독으로 넣지 마라** — SD는 `#FF6B9D`를 정확히 못 읽는다.
   **자연어 색상명을 메인**, hex는 톤 힌트로만 병기 (`hot pink, #FF6B9D canvas`).
4. **텍스트/레터링 차단을 negative에서 반드시** — `text, letters, words, numbers,
   typography, wordmark` 전부. 안 막으면 가짜 글자가 박힌다.
5. **`white background` + `transparent background` negative 필수** — positive 의
   "solid background" 만으론 흰/투명이 자주 나온다.
6. **`logo` 단독 키워드는 약함** — `logo mark`, `app icon`, `duolingo style app icon`,
   `ios app icon` 처럼 구체 구문을 써라.
7. **곱집합 직접 계산** — `prompts × candidates × (반복 호출 수)` 가 정확히 8 or 12.
   서버는 곱집합 자동 안 짜준다 (v3 batch 와 다름).
8. **`asset_key` 중복 주의** — 같은 키를 두 번 호출하면 뒤엣것이 덮어쓴다.
   타임스탬프 + `_vNN` 으로 유일하게.
9. **승인 우회는 시뮬에만** — `--bypass-approval` 은 임시/스캐치 (`tmp_`/`sim_`
   project prefix) 한정. 정식 후보는 무조건 cherry-pick 거쳐야 한다.
10. **배경색 요구가 강할수록 채도가 뭉개진다** — 너무 진한 브랜드 컬러면
    `muted vivid blue background` 처럼 톤 내려서 디테일 보존.
11. **서양식 자루/사실적 재질 주의 (v3 lesson, v4에서도 유효)** — "money bag" 만
    쓰면 burlap sack 이 나온다. negative 에 `realistic fabric, burlap sack,
    western cowboy bag, skeuomorphic` 꼭 추가.
12. **기능+가치를 병렬 배치하지 말고 융합해라 (v3 lesson)** — "노트 + 돈주머니"
    나란히보다 "노트 위 돈주머니" 또는 "돈주머니에 체크마크"처럼 **한 심볼로 합쳐야**
    로고 톤이 산다. §5.5 공식.
13. **seed variation을 얕보지 마라 (v3 lesson)** — 같은 프롬프트도 seed 바꾸면
    구도/비율/디테일이 크게 달라진다. prompt 늘리기보다 seed 늘려도 충분할 때가 많다.
14. **`alpha` 출력에 현혹되지 마라** — `icon/flat` 은 자동으로 알파 추출본을
    같이 만든다. 우리 가이드라인은 *배경 채움*이므로 cherry-pick 에선 **stage1(raw)**
    가 우선. alpha 는 export 용으로만.

---

## 11. 에이전트 셀프체크 (요청 전 1분 점검)

- [ ] `af workflow catalog` 로 `icon/flat` 이 `ready/available=true` 인 걸 봤나?
- [ ] 모든 prompt 항목에 `flat icon` / `duolingo style app icon` / `ios app icon`
      중 하나 이상 들어갔나?
- [ ] 모든 prompt 항목에 **solid {color} background**가 있는가?
- [ ] `subject_hint`가 **단일 합성 심볼**로 서술됐나? (병렬 ❌ / 융합 ✅)
- [ ] `--negative` 에 `text, letters, numbers, typography, wordmark` 포함됐나?
- [ ] `--negative` 에 `white background, transparent background` 포함됐나?
- [ ] `--negative` 에 `skeuomorphic, realistic fabric, burlap sack` 포함됐나?
- [ ] 총 생성 장수가 정확히 목표(8 또는 12)인가? (`prompts × candidates × 호출수`)
- [ ] `asset_key` 가 타임스탬프 + 버전 suffix 로 유일한가?
- [ ] `category/variant` 를 `icon/flat` 로 정확히 썼나? (`logo/*` 같은 가짜 X)
- [ ] `--workflow-params` 에 모델/LoRA/sampler 같은 변형 내장 키를 우겨넣지 않았나?
- [ ] 시뮬이 아니라면 `--bypass-approval` 안 썼는가?

---

## 12. 개선 TODO (이 문서의 한계)

- [ ] 로고 전용 ComfyUI 변형 (`icon/logo_chunky` 등) 추가되면 본 문서 §6.1 갱신
- [ ] 카테고리별 서브젝트 hint 프리셋 사전 (게임/핀테크/교육 등)
- [ ] 컬러 팔레트 자동 생성 함수 (brand_color 하나 → harmony 3색)
- [ ] Export 단계에서 380px/450px 자동 리사이즈 정책 정식화
- [ ] alpha 출력의 cherry-pick UX 분리 (raw 우선 노출, alpha 토글)
