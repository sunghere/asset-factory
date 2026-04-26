# NEXT — 세션 핸드오프

> Transient 문서. 작성/삭제 규칙은 [`CLAUDE.md`](./CLAUDE.md#세션-핸드오프-nextmd) 참조.

_작성: 2026-04-27 · 세션 종료 시점_

---

## 최근작업

### `feat/skill-p0-gaps` 브랜치 — `docs/TODOS_for_SKILL.md` P0 3개 완료 (2026-04-27)

세 커밋 독립 (각각 별 PR 가능), 회귀 `pytest -q --ignore=tests/test_generator_comfyui.py`
263 → 294 통과. P0 가 머지되면 `~/workspace/sincerity-skills/asset-factory-api/SKILL.md`
한 장으로 asset-relay-agent 등이 별도 변경 없이 모든 에셋 파이프라인을 실행 가능.

- **`c371620`** — P0-2: `input_labels` 자동 추론 + catalog 노출.
  - `workflow_patcher.find_load_image_label()` public helper.
  - `workflow_registry.InputLabelSpec`, `infer_input_labels()`,
    `VariantSpec.input_labels`, `_build_input_labels()` 머지 로직.
  - 워크플로우 JSON 의 LoadImage 노드를 `_LOAD_IMAGE_RULES` 와 매칭해 자동 추출.
    YAML `input_labels:` 섹션 있으면 description/required/default override —
    추론에 없는 라벨 선언 시 startup 에러 (오타 방지).
  - 실 registry 확인: sprite/pixel_alpha/hires/rembg_alpha/stage1/full/v37_*
    → `pose_image`, sprite/pose_extract → `source_image`. illustration/icon/
    pixel_bg 는 LoadImage 없어 빈 배열.

- **`550b7c5`** — P0-1: bypass(승인 우회) 모드.
  - 스키마: `assets`/`asset_candidates`/`generation_tasks` 에 `approval_mode`
    TEXT NOT NULL DEFAULT 'manual' 컬럼 + ALTER 마이그레이션
    (`models.py:_migrate_legacy_schema`). `idx_assets_approval` 인덱스.
  - API: `WorkflowGenerateRequest.approval_mode: Literal['manual','bypass']`.
    `list_project_assets`/`list_assets` 에 `include_bypassed=False` 기본.
    `list_batch_candidates` 항상 bypass 제외 (cherry-pick 큐 오염 방지).
    `export_assets`/`list_approved_assets` bypass 제외 + 응답에
    `excluded_bypassed`. `/api/health` 에 `bypass_retention_days`.
  - GC: `candidate_gc.run_gc_candidates(bypass_max_age_seconds)` +
    `AF_BYPASS_RETENTION_DAYS` env (default 7). bypass 후보만 짧게 별도 청소,
    bypass 로 promote 된 asset 은 GC 대상 아님 (chain 안전성).

- **`192d45a`** — P0-3: `af` CLI 부트스트랩.
  - typer 기반 `cli/` 패키지 (`cli/main.py`, `cli/http.py`,
    `cli/commands/workflow.py`). `python -m cli` 또는 `scripts/af` 셔임.
  - `af workflow catalog | describe | upload | gen`. `--input LABEL=VALUE`:
    `@<path>` → 자동 upload, `asset:<id>`/UUID → from-asset upload, plain →
    그대로. catalog 의 `input_labels` 와 대조해 unknown 라벨 시 가능 라벨
    알려주며 친절 에러. `--bypass-approval` → request body 의
    `approval_mode='bypass'`.
  - 환경변수: `AF_API_KEY` > `API_KEY` 폴백, `AF_BASE_URL` (default
    `http://localhost:8000`).
  - requirements.txt: `typer==0.25.0`, `respx==0.23.1` 추가.

---

## 핸드오프

### PR 분리 + 머지 결정

세 커밋 모두 독립이라 다음 중 택일:

1. **한 PR (`feat/skill-p0-gaps` 그대로)** — 빠르지만 리뷰가 무거움.
2. **3 PR (P0-2 → P0-1 → P0-3 순)** — `docs/TODOS_for_SKILL.md` 권장 형태.
   P0-2 가 가장 작아 워밍업.

```bash
# 옵션 2 의 첫 PR (P0-2 만):
git checkout main
git checkout -b feat/skill-p0-2-input-labels
git cherry-pick c371620
git push -u origin feat/skill-p0-2-input-labels
gh pr create --title "feat(registry): catalog 응답에 input_labels 자동 추론 노출 (P0-2)"
```

### 통합 smoke (사용자 외부, ~10min)

실 ComfyUI 로 end-to-end (현재 머지 안 됨 — 브랜치 위에서):

```bash
af workflow catalog | jq '.categories.sprite.variants.pixel_alpha.input_labels'
af workflow upload ./pose.png   # → 이름 한 줄
af workflow gen sprite/pixel_alpha tmp_test demo_001 "test prompt" \
    --input pose_image=@./pose.png --bypass-approval --candidates 2 --wait
af list tmp_test                          # bypass 안 보임 (OK)
af list tmp_test --include-bypassed       # 보임
af export tmp_test --manifest             # excluded_bypassed > 0 확인
curl -s localhost:8000/api/health | jq .bypass_retention_days  # 7
```

`af list`/`af get`/`af export` 는 본 PR 범위 밖 — REST 직접 호출 또는 Web UI
사용. 추가가 필요하면 별도 PR.

### `~/workspace/sincerity-skills/asset-factory-api/SKILL.md` 갱신

P0 머지 후 SKILL.md 를 `docs/TODOS_for_SKILL.md` "최종 호출 인터페이스 (요약)"
섹션 그대로 풀어쓰면 됨. 별도 코드 변경 불필요.

### 미반영 P1/P2 (별도 트래커 유지)

[`docs/TODOS_for_SKILL.md`](./docs/TODOS_for_SKILL.md) §P1/§P2 그대로. 특히:

- **P1-5** `/api/workflows/generate` 응답 정형화 (`run_id`, `expected_outputs`,
  `poll_url`) — `--wait` 의 폴링 의미가 명확해짐.
- **P1-6** `from-asset` 엔드포인트에 `run_id + output_label` 추가. CLI 의
  `--from-run <run_id> --output <label>` 시그니처는 P0-3 에서 예약 — 서버만
  채우면 동작.
- **P1-4** preset 이 catalog 에서 이미 resolve 된 형태로 노출되는데, 변형별
  어떤 preset 이 적용됐는지 `recommended_negative_preset_name` 메타를 별도로
  노출하면 사용자가 알기 쉬움.
- **P2-9** `dry_run=true` — `workflow_patcher.PatchReport` 그대로 반환.

### 환경 이슈

`tests/test_generator_comfyui.py` 는 `aioresponses` 미설치로 collect 에러 —
본 세션과 무관하게 기존부터 있던 환경 문제. `.venv/bin/pip install aioresponses`
또는 `requirements.txt` 의 `aioresponses==0.7.8` 를 통해 설치.
