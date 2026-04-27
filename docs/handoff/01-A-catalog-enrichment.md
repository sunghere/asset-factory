# §1.A — 카탈로그 API 풍부화 — **구현 완료**

> **이 핸드오프는 superseded.** 실제 구현은 [PR #28](https://github.com/sunghere/asset-factory/pull/28)
> (`feat: 워크플로우 디스커버리 메타데이터 인덱스`) 가 더 넓은 스코프 (25 변형 풀
> 메타 + recommend endpoint + CLI) 로 진행했다.
>
> 원래 명세 (이 핸드오프의 이전 버전, 461줄 prompt — 1 변형 단위) 는 git history
> 에 보존:
> - 최초 작성: commit `1bccb9f` (PR #27)
> - 단축 (이 commit): superseded 안내
>
> 후속 작업은 PR #28 검토 결과 (`docs/PLAN_workflow_discovery_metadata.md`) 를
> SoT 로 본다.

## 진행 흐름 요약

1. PR #21 — `NEXT.md` §1.A 회고 → 구현 명세 수준 보강 (머지됨)
2. PR #27 — 이 핸드오프 prompt 작성 (1 변형 pilot 가정)
3. **PR #28** — 실제 구현. 1 변형 → 25 변형 전부, dataclass 7종, recommend
   endpoint, CLI, SKILL.md 짝 PR (sincerity-skills #5).
4. PR #29 — `related.*` cross-ref 검증 (PR #28 후속, eng review §3.1).

## 다음 핸드오프 후보

§1.A 가 카탈로그 *데이터* 를 다 채웠으니 다음은:

- **§1.B** — `prompt_template.{model_triggers, forbidden_tokens, skeleton}` 을
  서버 측에서 *강제* 주입. 데이터는 PR #28 가 이미 노출.
- **§1.C** — `tags` / `use_cases` / `not_for` 룰 매칭 검색 (`/recommend` 의
  strict-AND 보다 유연).

§B/§C 핸드오프는 PR #28 + PR #29 머지 후 작성. 형식은 `docs/handoff/README.md`
의 6 block 골격 + 선행 컨텍스트 표 그대로 — 다만 "1 변형 pilot" 패턴은 폐기 (PR
#28 가 보여줬듯 25 변형 메타가 같이 가는 게 lake 단위).
