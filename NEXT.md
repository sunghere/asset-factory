# NEXT — 세션 핸드오프

> Transient 문서. 작성/삭제 규칙은 [`CLAUDE.md`](./CLAUDE.md#세션-핸드오프-nextmd) 참조.

_작성: 2026-04-26 · 세션 종료 시점_

---

## 최근작업

### ComfyUI 백엔드 스택 + Windows 회귀 fix (PR #13, 2026-04-26)

- **PR #13 머지 완료** ([4de9fbf](https://github.com/sunghere/asset-factory/commit/4de9fbf), squash):
  - ComfyUI 백엔드 (`generator_comfyui.py`, `sd_backend.py`, `workflow_registry.py`,
    `workflow_patcher.py`) + 41개 워크플로우 카탈로그 + 신규 엔드포인트
    `GET /api/workflows/catalog`, `POST /api/workflows/generate`
  - Windows 환경 회귀 fix: `server.py:_allowed_roots`(`os.pathsep`),
    `scanner.py:infer_category`(`as_posix`)
- **1차 리뷰 + 비판 검토 후 P1×9 + P2×5 모두 같은 PR 에 반영** — lint/CodeQL,
  KSampler title-aware 패치, ClientSession 재사용, prompt_lost fail-fast,
  빈 outputs 디버깅 메시지, catalog 절대경로 회피, atomic registry 갱신,
  GenerationOutcome invariant, VariantSpec 타이핑, disk guard scaling,
  random seed fallback. **기존 mock 시그니처 가변 인자 패턴 통일**.
- **신규/보강 테스트 +17** (`tests/test_generator_comfyui.py` 13 케이스 +
  workflow_patcher 3 + sd_backend 2)
- **검증**: pytest **202 passed**, ruff **All checks passed**

### 문서/기획 (2026-04-26)

- [`docs/PLAN_comfyui_dynamic_inputs.md`](./docs/PLAN_comfyui_dynamic_inputs.md) —
  ComfyUI 동적 입력 업로드 1차 PR 상세 계획 (8단계 ~3.5h, 코드 스켈레톤 +
  테스트 매트릭스 + 위험 표 + 결정 사항 6개 확정)
- [`docs/TODOS.md`](./docs/TODOS.md) — 후속 작업 추적 신설 (P0/P1/P2 13항목,
  라벨 6종)

---

## 핸드오프

### B 작업 — ComfyUI 동적 입력 1차 PR (신규 세션 픽업)

상위 설계는 [`docs/PLAN_comfyui_dynamic_inputs.md`](./docs/PLAN_comfyui_dynamic_inputs.md),
**신규 세션을 위한 상세 핸드오프**는
[`docs/HANDOFF_comfyui_dynamic_inputs.md`](./docs/HANDOFF_comfyui_dynamic_inputs.md)
에 작성 — 코드 위치(file:line), 결정 사항, 테스트 패턴, 개발 환경, CI 게이트,
git 워크플로우 템플릿, loose ends 까지 포함.

**선결 (사용자 외부 작업):** ComfyUI 의 `POST /upload/image` 가 존재하지 않는
subfolder 를 자동 생성하는지 curl 1회 실측. 결과에 따라 폴백 분기 결정. 상세는
HANDOFF 문서 §3 참조.

### C-P0 #2 — `workflows/README.md` 의 `af workflow gen` 광고 정정 (uncommitted)

이번 세션이 시작했다가 인터럽트로 중단. 3개 hunk 가 working tree 에 남아 있을
수 있음 (상단 CLI 미구현 배너 + §3-§4 curl 예제 교체 + §새 카테고리 한 줄
정정). 처리 옵션:

1. **B 작업 PR 에 묶음** (권장 — 어차피 B 가 `workflows/README.md` 에 curl 예제
   추가하므로 자연스럽게 합쳐짐)
2. 별도 작은 PR 로 먼저 처리 (5분)
3. revert 후 B 끝나고 다시 — `git checkout workflows/README.md`

### 워크트리 잔재 (사용자 외부 작업)

이번 세션이 `claude/vigorous-torvalds-a1e1af` 워크트리 안에서 돌고 있어 강제
삭제 불가. 사용자가 본인 터미널에서 (선택):

```bash
cd D:/DEV/asset-factory
git worktree remove --force .claude/worktrees/vigorous-torvalds-a1e1af
git branch -D claude/vigorous-torvalds-a1e1af
```

이미 `.gitignore` 에 `.claude/` 들어가 있어 안 지워도 향후 커밋 오염 0.
