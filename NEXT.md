# NEXT — 세션 핸드오프

> Transient 문서. 작성/삭제 규칙은 [`CLAUDE.md`](./CLAUDE.md#세션-핸드오프-nextmd) 참조.

_작성: 2026-04-20 · 세션 종료 시점_

---

## 최근작업

### SPA 리뉴얼 (Asset Factory Redesign v0.2) 마무리
11개 화면을 React + Babel CDN SPA 로 포팅 완료. 기획 설계문서
(`docs/SCREEN_SPEC_v0.2.md`) 기준 구현.

- **`/` · `/cherry-pick` → `/app/*` redirect** (legacy 경로 완전 제거)
- **레거시 static HTML/JS 퍼지** — `static/index.html`, `static/cherry-pick.html`,
  `static/app.js`, `static/style.css` 삭제. `/legacy` 라우트 제거.
  (followups §6 · DONE)
- **헤더 경량화** — 각 화면의 큰 타이틀/디스크립션 제거. `PageToolbar` +
  `PageInfo` (팝오버) 조합으로 대체. `static/app/js/hooks.jsx`,
  `static/app/css/app.css`.
- **Validation 뱃지** — `Thumb` 컴포넌트에 `warn` prop 추가, `.bdg.warn` CSS.
  CherryPick 후보 그리드, Assets `AssetCard`, BatchDetail 후보 그리드 3군데에서
  `validation_status='fail'` 시 빨간 뱃지 + `validation_message` tooltip.
  (followups §7a · DONE)
- **Batch spec 집계 API** — `GET /api/batches/{batch_id}` 신규
  (`server.py`, `models.py::Database.get_batch_detail`). 응답에
  `meta` / `tasks` / `candidates` / `spec(axes+common+task_count)` 포함.
  `BatchDetail.jsx` 의 `SpecView` 를 이 페이로드 기반으로 재작성
  (meta · axes · common params · candidates 4블록, `mixed` 뱃지).
  (followups §7b · DONE)
- **cat-raising 테스트 시드** — `scripts/seed_cat_raising.py` (멱등).
  3개 design 배치 (`btc_seed_cr_baby_idle`, `btc_seed_cr_baby_eat`,
  `btc_seed_cr_btn_feed`) + 후보 PNG. validation mix / 다축 spec /
  active queued 시나리오 커버. queued 태스크는 `next_attempt_at = 2099-01-01`
  로 박아서 worker 가 claim 하지 않도록 처리.
  실행: `.venv/bin/python scripts/seed_cat_raising.py`
  (followups §8 · DONE)

### 검증
- `pytest tests/` 86개 전부 pass (기존 테스트 영향 없음).
- `/api/batches/{id}` · `/api/cherry-pick/queue` curl 확인: 시드 배치 3종
  (`total=4/4`, `total=4/4`, `total=2, done=1, active=1`) 모두 정상 집계.

### 관련 문서
- `docs/asset-factory-redesign-followups.md` — §6/§7a/§7b/§8 DONE 마킹 완료.
  남은 backend followup §1–§5 는 이번 세션 범위 밖 (별도 이슈 후보).

---

<!-- 핸드오프 섹션은 인계할 in-flight 작업이 있을 때만 추가한다. 현재 없음. -->
