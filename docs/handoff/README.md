# docs/handoff/

별도 Claude Code 인스턴스 (또는 다른 개발자) 에 줄 작업 지시 prompt 모음.

## 라이프사이클

- 핸드오프 작성 → PR 분리 → 받는 측이 **이 markdown 통째** 를 컨텍스트로 받아서 작업 시작
- **PR 단위 분리 원칙**: 작업당 1 markdown 파일 1 PR
- 작업 머지 후 해당 핸드오프 파일은 **삭제** (git history 에 남음). 핸드오프 디렉토리에는 *지금 in-flight 핸드오프* 만 남긴다.

## 작성 골격

- 스킬 `spec-doc-handoff-quality` 의 6 block 패턴 (기획 의도 / 데이터 모델 / API / 구현 단계 / 테스트 / 결정 메모)
- 첫 섹션에 **선행 컨텍스트 (반드시 먼저 읽어야 할 문서)** 표 — 받는 측이 한 곳만 보고 시작 가능해야 함
- 마지막에 **작업 완료 체크리스트** — PR submit 전 self-review 용

## 활성 핸드오프

| 파일 | 작업 | 상태 | Spec 출처 |
|---|---|---|---|
| `01-A-catalog-enrichment.md` | §1.A — 카탈로그 API 풍부화 (P0) | **SUPERSEDED by PR #28** | `docs/NEXT.md` §1.A (PR #21) |

## 발행 예정 (선행 작업 머지 후)

- **§1.B** — 공통 prompt 강제 주입 (P0). PR #28 + PR #29 머지 후 작성 — 데이터는 이미 노출 (`prompt_template.model_triggers/forbidden_tokens`).
- **§1.C** — 변형 의도 기반 인덱스/자연어 검색 (P1). §1.B 와 병렬 진행 가능.

> **§1.A 회고**: 핸드오프를 "1 변형 pilot" 으로 잡았는데 실제 구현 (PR #28) 은
> 25 변형 풀 메타로 갔다. AI 보조에서 lake 단위 (변형 N개 한 번에) 가 marginal
> cost 작아 자연스러움. §1.B / §1.C 핸드오프는 처음부터 lake 가정으로.
