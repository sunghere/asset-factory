# PLAN — 워크플로우 디스커버리 + 사용 가이드 메타데이터 인덱스

> 상태: **검토 완료, 미구현**
> 작성일: 2026-04-26
> 후속 PR: 1~2개, ~5~6시간
> 관련: [`workflows/registry.yml`](../workflows/registry.yml), [`workflow_registry.py`](../workflow_registry.py), [`SKILL.md`](../../sincerity-skills/asset-factory-api/SKILL.md)

---

## 0. 한 줄 요약

워크플로우를 사용하는 클라이언트(LLM 또는 Asset-Factory Server)가 task 받았을 때
**"어떤 variant 를 골라야 하나, 어떤 prompt 골격으로 채워야 하나"** 를 self-service 로
알 수 있게 만드는 디스커버리 메타데이터 인덱스를 `registry.yml` 에 추가한다.

현재 `registry.yml` 은 **호출 메커니즘** (file 경로, 노드 매핑) 만 가짐.
**선택 의사결정 메타** (when-to-use, prompt template, tags) 가 없어 LLM 이 매번
시행착오로 변형을 찾아야 함.

---

## 1. 문제 정의

### 현재 상태

```yaml
# registry.yml 의 한 변형 (호출 메커니즘만)
sprite/pixel_alpha:
  description: "픽셀 그리드 + 투명배경 (게임 엔진용) ⭐"   # ← 한 줄 설명만
  file: sprite/...api_pixel_alpha.json
  outputs: [...]
  defaults: { steps: 30, cfg: 6.5, ... }
```

### 부족한 것

| 부재 메타 | LLM/클라이언트에 미치는 영향 |
|---|---|
| `use_cases` / `not_for` | sprite/pixel_alpha vs v36_pro_stage1 vs illustration/animagine_hires 어느 것을 골라야 할지 모름 |
| `tags` (kind/style/format/model_family) | rule-based 필터링 불가 (`kind=character & style=pixel-art` 식) |
| `prompt_template` (skeleton + required/forbidden tokens + examples) | 빈 prompt 부터 시작, 매번 시행착오. score_9 (Pony) 같은 모델 특화 토큰 누락 |
| `cost` (est_seconds, vram_gb) | 5분짜리인지 30초짜리인지 모름 — 배치 ETA 계산 불가, OOM 위험 미인지 |
| `pitfalls` / `related` | 검 들기 trick·flipX 권장 같은 검증 노하우 매번 손실. 다른 변형 fallback 못 함 |

---

## 2. 아키텍처

### 옵션 비교

| 옵션 | 메커니즘 | 평가 |
|---|---|---|
| **A. registry.yml 확장** ⭐ | variant 마다 새 키들 (`use_cases`, `tags`, `prompt_template`, `pitfalls`, `cost`, `related`) — `/api/workflows/catalog` 한 호출로 LLM 이 다 받음 | single source of truth, token 효율, 자기-문서화 |
| B. 변형별 별도 .md 파일 | `workflows/sprite/pixel_alpha.md` long-form 가이드 | 사람 친화 but LLM token 비효율, 동기화 깨지기 쉬움 |
| C. Embedding semantic search | 변형 description 을 vector index → 자연어 쿼리 | 강력하지만 인프라 (model + vectorstore) 추가, 1차엔 과함 |
| **D. `/api/workflows/recommend`** | A 위에 rule-based 필터 (tag 조합) 또는 자연어 힌트 | A 의 메타가 있으면 쉬움, 편의 |

**선택: A + D.** A 가 토대, D 는 편의. C 는 후속.

### 클라이언트 흐름 (목표)

```
LLM ── GET /api/workflows/catalog ──▶ asset-factory
       (모든 변형의 메타 + prompt template + tags + examples)
       ◀── 한 번의 응답으로 모든 변형 리스트 + 메타

LLM 이 task 받음 ("우리 게임 캐릭터 도트")
  └─ 메타 파싱: tags.kind=character & tags.style=pixel-art
  └─ 매칭: sprite/pixel_alpha (primary)
  └─ prompt_template.skeleton 으로 placeholder 채움
  └─ POST /api/workflows/generate {variant, prompt, ...}
```

---

## 3. 매니페스트 확장 schema (제안)

기존 필드는 모두 유지하고 **신규 키 추가**:

```yaml
sprite:
  variants:
    pixel_alpha:
      # ===== 기존 (호출 메커니즘) =====
      description: "픽셀 그리드 + 투명배경 (게임 엔진용) ⭐"
      file: sprite/...api_pixel_alpha.json
      primary: true
      outputs: [...]
      defaults: { steps: 30, ... }

      # ===== 신규: 디스커버리 / 사용 가이드 =====
      use_cases:
        - "게임 캐릭터 sprite (1×3 multi-view, 투명배경)"
        - "ControlNet OpenPose 로 등신 비율 강제 필요"
        - "픽셀 그리드 정렬된 알파 PNG 가 필요"
      not_for:
        - "단일 view → sprite/v36_pro_stage1"
        - "고해상도 일러스트 → illustration/*"

      tags:
        kind: character             # character | background | icon | illustration
        style: pixel-art            # pixel-art | flat | anime | illust
        format: multi-view-1x3      # single | multi-view-1x3 | tile
        output: alpha-pixel         # alpha-pixel | alpha-rembg | raw | hires
        model_family: illustrious   # sdxl | sd15 | illustrious | pony

      character_proportions:
        head_ratio: 2.5
        view_count: 3
        chibi: true

      prompt_template:
        skeleton: |
          {gender_count}, {hair_features}, {outfit_features},
          {pose_or_action}, {scene_qualifiers},
          masterpiece, best quality, very aesthetic
        required_tokens:
          - placeholder: "{gender_count}"
            examples: ["1girl", "1boy", "1other"]
          - placeholder: "{hair_features}"
            examples: ["(black hair:1.2), (side ponytail:1.3), long hair"]
        forbidden_tokens:
          # 워크플로우 자체와 충돌하는 것
          - { token: "(chibi:1.4)", reason: "ControlNet 이 비율 결정 — 강한 chibi 와 충돌" }
          - { token: "background, scenery", reason: "흰배경 강제됨" }
        examples:
          - prompt: |
              1girl, (black hair:1.2), (side ponytail:1.3), long hair,
              blue knight armor, (holding silver sword:1.3), red cape,
              fantasy warrior, masterpiece, best quality
            seed: 42
            note: "HANDOFF §1.1 메인 검증 캐릭터"

      cost:
        est_seconds: 45             # RTX 4080 기준
        vram_gb: 12

      pitfalls:
        - "검 든 캐릭터: (holding silver sword:1.3) + sword in right hand 세트로 떠다님 방지"
        - "좌측 view 는 게임 엔진에서 flipX 권장 (SDXL 좌측 학습 약함)"

      related:
        sibling: [sprite/hires, sprite/full, sprite/rembg_alpha]
        upstream: [sprite/v36_pro_stage1]   # 디자인 단계 → 이걸로 다듬은 후
        downstream: []
```

### 새 dataclass (workflow_registry.py)

```python
@dataclass(slots=True, frozen=True)
class TagSet:
    kind: str | None
    style: str | None
    format: str | None
    output: str | None
    model_family: str | None


@dataclass(slots=True, frozen=True)
class PromptToken:
    placeholder: str
    examples: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class ForbiddenToken:
    token: str
    reason: str


@dataclass(slots=True, frozen=True)
class PromptExample:
    prompt: str
    seed: int | None
    note: str | None


@dataclass(slots=True, frozen=True)
class PromptTemplate:
    skeleton: str
    required_tokens: tuple[PromptToken, ...]
    forbidden_tokens: tuple[ForbiddenToken, ...]
    examples: tuple[PromptExample, ...]


@dataclass(slots=True, frozen=True)
class CostEstimate:
    est_seconds: int | None
    vram_gb: float | None


@dataclass(slots=True, frozen=True)
class RelatedRefs:
    sibling: tuple[str, ...]   # ["sprite/hires", ...]
    upstream: tuple[str, ...]
    downstream: tuple[str, ...]


# VariantSpec 에 추가:
@dataclass(slots=True, frozen=True)
class VariantSpec:
    # ... 기존 ...
    use_cases: tuple[str, ...] = ()
    not_for: tuple[str, ...] = ()
    tags: TagSet | None = None
    character_proportions: dict[str, Any] | None = None
    prompt_template: PromptTemplate | None = None
    cost: CostEstimate | None = None
    pitfalls: tuple[str, ...] = ()
    related: RelatedRefs | None = None
```

---

## 4. 단계별 구현

### Phase A — registry.yml 확장 + catalog 노출 (필수)

| 단계 | 내용 | 추정 |
|---|---|---|
| A1 | 매니페스트 schema 설계 확정 (위 §3) + `WorkflowRegistry` dataclass / 로더 확장 | 1.5h |
| A2 | sprite/pixel_alpha 풀 메타 작성 → 검증, 그 다음 25 변형 일괄 작성 | 2h |
| A3 | `to_catalog()` 응답 확장 + `tests/test_workflow_registry.py` 보강 (parse + roundtrip) | 30min |

**가치 90%** — LLM 이 `/api/workflows/catalog` 한 번 호출로 모든 의사결정 가능.

### Phase D — 편의 (선택)

| 단계 | 내용 | 추정 |
|---|---|---|
| D1 | `/api/workflows/recommend?kind=character&style=pixel-art` rule-based filter | 45min |
| D2 | `af workflow recommend` / `af workflow describe <variant>` CLI | 30min |
| D3 | SKILL.md 갱신 — "LLM 이 task → variant 결정하는 가이드" | 30min |

총 ~5~6시간. **A 시리즈만 해도 LLM 가용 가치 대부분 확보**.

---

## 5. 결정 필요 사항 (착수 전 사용자 컨펌)

- [ ] **범위**: A 시리즈만(매니페스트 + catalog 노출) vs A+D 둘 다(+ recommend endpoint, CLI 확장)
- [ ] **메타 작성 분담**: 25개 변형 메타 — 내가 초안 작성 후 사용자 검토 vs 사용자가 prompt examples / pitfalls 의 도메인 디테일 채우기
- [ ] **prompt_template 표현**: 위 예시처럼 `placeholder + examples` 정형 vs free-form text (한 줄 markdown 설명)
- [ ] **tag 분류 체계**: 위 안 (kind/style/format/output/model_family) 그대로 vs 별도 정의

---

## 6. 작업 우선순위 / 의존성

### 의존성 그래프

```
A1 schema + 로더  ─┐
                  ├─▶ A2 메타 작성 ─▶ A3 테스트 ─▶ Phase A 완료
                  └─▶ (병행) D1 recommend endpoint ─▶ D2 CLI ─▶ D3 SKILL
```

A1 → A2 가 critical path. A1 끝나면 사용자가 A2 메타 작성 동시에 D1 진행 가능.

### 영향 범위

| 모듈 | 영향 |
|---|---|
| `workflow_registry.py` | dataclass 추가, 로더 파싱 확장 (필수) |
| `workflows/registry.yml` | 25 변형에 메타 추가 (필수) |
| `server.py` `to_catalog()` | 응답에 새 필드 노출 (필수) |
| `tests/test_workflow_registry.py` | parse + 라운드트립 검증 (필수) |
| `server.py` `/api/workflows/recommend` 신설 | 선택 (Phase D) |
| `af.mjs` | 선택 (Phase D2) |
| `SKILL.md` | 선택 (Phase D3) |
| 기존 호출 (`/api/workflows/generate`) | **무영향** — 추가만 |

### 호환성

- 매니페스트의 신규 키는 **모두 optional** — 기존 변형이 메타 없이도 동작 (`available=True` 유지)
- catalog 응답에 새 필드 추가 — 기존 클라이언트는 그냥 무시 (forward-compatible)

---

## 7. 미해결 / 후속 검토

- **embedding-based 검색 (옵션 C)** — Phase B 이후 LLM 이 자연어로 "수영하는 캐릭터" 같은 hint 줘도 매칭하고 싶을 때 추가. `sentence-transformers` + FAISS 또는 OpenAI embedding. 현재는 LLM 이 자체적으로 메타 보고 결정하면 충분.
- **메타 자동 생성 보조** — 25 변형 메타 사람이 다 적기 부담. 워크플로우 JSON 분석으로 일부 자동 추출 (model_family from checkpoint 이름, format from 노드 그래프, vram_gb from latent 크기 추정).
- **버전 관리** — 메타 변경 시 `version` bump 정책. 1차는 `version: 1` 그대로, 향후 schema 진화 시 `meta_version` 별도 필드.
- **prompt template 의 i18n** — 한국어 placeholder vs 영어. 1차는 한국어 description + 영어 prompt example. 다국어 지원은 후속.
- **cherry-pick UI 노출** — `/cherry-pick` 페이지에서 변형 메타 (use_cases, related variants) 도 같이 보여주면 사용자가 후보 결정 더 쉬움. Phase D 이후.

---

## 8. 관련 / 참조

### 기존 결정·근거

- [HANDOFF.md §1.1 메인 검증 캐릭터](../../sincerity-skills/sd-generator/HANDOFF.md) — sprite/pixel_alpha 의 검증된 prompt
- [HANDOFF.md §5 핵심 결정 사항](../../sincerity-skills/sd-generator/HANDOFF.md) — chibi 키워드 과적용·검 떠다님·1×3 layout 등의 pitfalls 원본
- [SKILL.md §"ComfyUI 워크플로우 카테고리 — 언제 무엇을"](../../sincerity-skills/asset-factory-api/SKILL.md) — 사람용 카테고리 가이드 (이번 작업의 LLM-readable 버전)

### 다른 PR 들과의 관계

- **`PLAN_comfyui_dynamic_inputs.md`** (별도) — 동적 입력 업로드. 이번 메타 인덱스 작업과 독립. 둘 다 끝나면 LLM 이 (1) 변형 선택 (2) 입력 업로드 (3) 호출 까지 자율 가능.
- **C/D 시리즈 활성화** (완료) — 25 변형 가용. 메타 인덱스의 25 변형이 그대로 대상.

---

## 9. 한 페이지 요약 (다음 세션 픽업용)

이번 작업은 **registry.yml 의 25 변형에 디스커버리 메타데이터 추가**.

각 변형마다:
- `use_cases` / `not_for` (언제 쓰나)
- `tags` (kind/style/format/output/model_family)
- `prompt_template` (skeleton + required/forbidden tokens + examples)
- `cost` (est_seconds, vram_gb)
- `pitfalls` / `related`

이걸로 LLM 이 `/api/workflows/catalog` 한 호출에 모든 변형의 사용 가이드 받음 →
시행착오 없이 task 메타로 즉시 변형 선택 + prompt 골격 채움.

**다음 세션 시작점**:
1. 위 §5 결정 4가지 컨펌 받기 (특히 범위 A vs A+D, 메타 작성 분담)
2. A1 (schema + dataclass + 로더) 부터 진행 — workflow_registry.py 확장 + tests/test_workflow_registry.py 의 새 메타 라운드트립 테스트
3. A2 진행 시 sprite/pixel_alpha 한 변형 풀 메타로 작성해 패턴 확정 후 나머지 24 일괄
