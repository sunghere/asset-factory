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
- **검수 후속 (2026-04-20)** — `AssetDetail` `useSSE` 가 배열 배치를 처리하도록 수정;
  `Assets`·`Export` 화면에 갤러리/보내기 관련 SSE 무효화 추가;
  `tests/test_redirect.py` 에 `GET /` → `/app/` 핀;
  `tests/test_api_tasks.py` 에 `API_KEY` 설정 시 `retry-failed` 401 계약.

### 검증
- `pytest tests/` 전부 pass (검수 후속: `GET /` 리다이렉트 · `retry-failed` API 키 계약 추가 후 **104** 기준).
- `/api/batches/{id}` · `/api/cherry-pick/queue` curl 확인: 시드 배치 3종
  (`total=4/4`, `total=4/4`, `total=2, done=1, active=1`) 모두 정상 집계.

### 관련 문서
- `docs/asset-factory-redesign-followups.md` — §6/§7a/§7b/§8 DONE 마킹 완료.
  남은 backend followup §1–§5 는 이번 세션 범위 밖 (별도 이슈 후보).

---

## 핸드오프 — 기획 ↔ 구현 갭 (재검수 · 코드 기준 2026-04-20)

아래는 **구 self-check 목록(동일 날짜 초안)을 코드와 대조해 재분류**한 것이다.
단일 진실은 계속 `docs/SCREEN_SPEC_v0.2.md` 이며, followups 문서의 §1–§5(썸네일·키맵·projects·SSE 명세·캐시) 중복은 생략한다.

**범례:** `해결` = 초안 주장이 더 이상 맞지 않음 · `부분` = 구현됐으나 스펙과 디테일 차이 · `미해결` = 여전히 갭

### 구 번호 → 재분류 (요약)

| 구# | 재분류 | 한 줄 |
|-----|--------|--------|
| 1 CherryPick 사이드 | **해결** | 380px SidePanel·Preview/Meta/Actions·Compare |
| 2 키맵 | **부분** | Enter/x/v/c/i/m/?/J·K·LoRA 등 대부분 반영; 스펙 문구와 1:1 대조는 별도 |
| 3 Compare | **해결** | `Dialog`/`CompareDialog`·비교 set |
| 4 SSE | **부분** | Dashboard/Queue/CherryPick/Batches/BatchDetail·AssetDetail 연결됨. `gc_run_completed`·`sd_health_changed` UI 연동·Export는 `export_completed` SSE 추가됨. 다중 `EventSource`·폴링 주기 vs §6.1 수치는 여전히 검토 여지 |
| 5 Tasks 탭 | **해결** | 3탭·`/api/batches/{id}/tasks`·retry-failed |
| 6 가상화·prefetch | **부분** | 구현 있음; 100장 실측·스펙 §11 문구 정밀 대조는 미완 |
| 7 Dashboard | **부분** | SD·by_category·curl 등 일부 블록/카피는 §6.1과 차이 |
| 8 Queue 행 | **부분** | 풀 와이어 vs 현재 테이블 밀도 |
| 9 Assets | **부분** | 정렬·URL·bulk·페이징·SSE 일부 반영; 스펙 문장 전부 충족은 아님 |
| 10 Settings auto-advance | **부분** | 설정 키 존재; CherryPick이 읽는지·도움말 버튼은 재확인 |
| 11 System | **해결** | DB/Worker/Logs 블록·API 존재 |
| 12 AssetDetail 복원 | **부분** | restore 플로우·SSE 배치 처리(검수 후속에서 수정) |
| 13 a11y | **부분** | listbox 등 일부; Toast role·SegProgress 등 잔여 |
| 14 BatchNew | **부분** | 200+ 경고·JSON 프리뷰·loras 매트릭스 등 |
| 15 Catalog | **부분** | 카드·역참조 등 — 3자 검수에서는 pass 근접, 스펙 전 항목은 재확인 권장 |
| 16 Export | **부분** | category/since·트리·MB 등 상당 부분 구현; 레이아웃 vs §6.9 |
| 17 에러·배너 | **부분** | ErrorPanel·PersistentBanners·401 복구 UX vs §9–10 정밀 |
| 18 localStorage 키 | **부분** | `af_api_key`·마이그레이션 여부 코드 확인 |
| 19 모바일 | **미해결** |
| 20 분석 이벤트 §12 | **미해결** |
| 21 TopBar API 키 | **해결** | `ApiKeyChip` 등 |

### SCREEN_SPEC §6·§8 기준 **부분(partial)** 이슈 풀 (이슈 쪼개기용)

1. **IA 표기**: 문서는 `/cherry-pick?batch=` · 앱은 `/cherry-pick/:batchId` (레거시 쿼리는 서버에서 `/app/cherry-pick/:id` 로 리다이렉트).
2. **§6.1 Dashboard**: CTA가 큐가 아닌 바로 체리픽으로 가는지·카드 클릭·타임라인 밀도.
3. **§6.2 Queue**: empty state curl·행 풀 스펙·footer.
4. **§6.3 CherryPick**: 3-segment 진행바·다음 배치 자동 진입 등 세부.
5. **§6.4–6.5 Assets / AssetDetail**: 캐러셀 vs 테이블·옵션 `role` 완성도.
6. **§6.6 BatchDetail**: Spec 탭 curl 복사 등.
7. **§6.7 BatchNew**: 성공 후 자동 이동 vs 토스트만.
8. **§6.9 Export**: 3열 와이어 vs 2열 그리드.
9. **§8 a11y**: Toast `role`·`Thumb`+listbox·`SegProgress` progressbar.

### 기타 관찰 (갱신)

- `app.jsx` 에 `/regen` → `/batches/new` 리다이렉트만 남음; 사이드 네비에서 `/monitor`·`/errors` 노출은 **제거된 상태**로 보는 것이 맞다(재확인: `chrome.jsx` `NAV_ITEMS`).
- 스크립트 로더는 `static/app/manifest.json` 로 이전 완료 — 초안의 "index.html 알파벳 순" 지적은 **구식**.

### 추천 다음 스프린트 (실제 갭 위주로 수정)

- **A — 스펙 밀도**: §6.1 대시보드 · §6.2 큐 · §6.3 체리픽 세부(진행바·empty)·§6.9 익스포트 레이아웃.
- **B — SSE·폴링**: `sd_health_changed` / `EVENTS.md` 소비자 정리, 단일 구독 여부, §7.3 이름과 코드 정렬(`task_error` vs 문서 `task_failed` 등).
- **C — a11y·설정**: §8 잔여 · Settings 도움말/auto-advance 검증.
- **D — 장기**: §12 계측 · 모바일 §19.

브라우저 스모크 순서는 [`docs/BROWSER_SMOKE_CHECKLIST.md`](docs/BROWSER_SMOKE_CHECKLIST.md).
