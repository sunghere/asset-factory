# NEXT — 세션 핸드오프

> Transient 문서. 작성/삭제 규칙은 [`CLAUDE.md`](./CLAUDE.md#세션-핸드오프-nextmd) 참조.

_작성: 2026-04-20 · 세션 종료 시점_

---

## 최근작업

### 테스트 커버리지 보강 (2026-04-21)

- `tests/test_generator.py` 신규 — `SDClient.health_check/list_models/list_loras`,
  `txt2img` payload·bad info 처리, `_request_json` retry/HTTP error 분기,
  저장 helper 2종 경로 sanitization까지 단위 테스트 추가.
- `tests/test_validator_unit.py` 신규 — PNG 성공 케이스, size/palette/format
  복합 실패 케이스, `_count_colors()` palette overflow fallback 추가.
- `tests/test_catalog_api.py` — invalid YAML, fallback field/title/alias, invalid
  metadata entry skip 케이스 보강.
- `tests/test_gc_policy.py` — rejected path 로딩 schema error fallback, candidates
  루트 밖 파일 보호 케이스 추가.
- `.gitignore` 에 `.coverage` 추가 (coverage 산출물 추적 방지).

### 검증
- `.venv/bin/python -m pytest tests/ -q` → **122 passed**
- `.venv/bin/python -m ruff check .` → **All checks passed**
- `.venv/bin/coverage run -m pytest tests/ -q && .venv/bin/coverage report -m`
  → 전체 **87%**, `generator.py` **97%**, `validator.py` **100%**,
  `catalog.py` **95%**, `candidate_gc.py` **88%**

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
