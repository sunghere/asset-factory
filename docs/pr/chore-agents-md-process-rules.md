# chore: AGENTS.md 신규 + CLAUDE.md 에 참조 추가

> Branch: `chore/agents-md-process-rules` → `main`

## 동기

이번 세션에서 PR #48~#53 을 진행하며 다음 사고들이 실제로 발생했다:

| # | 사고 | 후속 PR |
|--|--|--|
| 1 | PR #48 머지 시 CI FAILURE + CodeQL 인라인 리뷰 4건 무시 → main 빨간 상태 도착 | #49 |
| 2 | 로컬에서 `ruff check .` 한 번도 안 돌리고 push → CI 가 1차 검출자 | #49 |
| 3 | CherryPick.jsx 의 hook alias (`useStateCP`) 충돌 → 새 컴포넌트 raw `useState` 호출로 화면 크래시 | #49 |
| 4 | Frontend 가 `c.status === 'rejected'` 로 분기하는데 backend response 에 `status` 필드 없음 → 같은 후보 무한 재반려 | #51 |
| 5 | `candidate_gc.py` step 1 (rejected) 가 step 2 (bypass) 와 달리 `_delete_candidate_rows()` 호출 누락 → orphan row 31% 누적 | #53 |
| 6 | PR body 본문이 ephemeral 했음 → docs/pr/*.md 컨벤션 도입 | #48~ 부터 |

각 사고의 회고가 후속 PR 본문에 흩어져 있어 다음 세션 / 다음 에이전트가
같은 실수를 재발할 가능성이 높다. 한 곳에 모아 운영 규칙으로 hard-code.

## 변경

### `AGENTS.md` 신규

본 프로젝트에서 실제로 발생한 사고의 회고에서 도출된 9개 항목 + 부록.
일반론 배제, 모든 항목이 "이것 안 했더니 X 사고가 났다" 로 추적 가능.

1. 시작 시 항상 (NEXT.md, git status, 미커밋 변경 처리)
2. 로컬 검증 — ruff/pytest 가 CI 1차 검출자가 되지 말 것
3. PR 워크플로우 — 머지 전 CI + 인라인 리뷰 양쪽 확인 (커맨드 포함)
4. PR body 컨벤션 — `docs/pr/*.md`
5. 브랜치 / 커밋 컨벤션 (한국어 본문, prefix)
6. 워크트리로 후속 작업 격리
7. Frontend ↔ Backend 계약 mismatch — 서버에서 derive
8. 컴포넌트 추가 시 import scope 확인 (hook alias)
9. GC / cleanup — file unlink 와 DB row 정리는 짝
10. 백엔드 / 프론트엔드 환경 메모 (빠른 reference)
11. 부록 — 신규 사고 추가 템플릿

### `CLAUDE.md` 최상단에 참조 추가

기존 CLAUDE.md 가 도메인 / 스크린 / 데이터 소스 규칙을 담는 위치라,
"개발 흐름 자체의 규칙" 은 별도 파일이 의미 명료. 짧은 인용문으로 AGENTS.md
를 가리키며 역할 분리를 명시.

## scope 밖

- 본 PR 은 신규 도큐먼트만 — 코드 변경 0, 회귀 가드 0.
- AGENTS.md 의 9개 항목은 모두 이미 후속 PR 에서 일회성으로 적용된 규칙.
  앞으로의 사고가 누적되면 본 문서를 갱신.

## 검증

```
.venv/bin/python -m ruff check .   → All checks passed!
.venv/bin/python -m pytest -q      → 438 passed (변경 없음)
```
