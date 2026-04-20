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

## 핸드오프 — 기획 ↔ 구현 갭 (self-check 2026-04-20)

`docs/SCREEN_SPEC_v0.2.md` 를 기준으로 현재 `/app/*` SPA 를 전수 비교한
결과. 모두 이번 세션 범위 밖이라 **미결 항목**. 다음 작업자가 골라 잡기
쉽도록 severity 순으로 정리. 이미 followups 문서에 있는 §1–§5 는 중복 기재
안 함 (thumbnail variant, 키맵 재검증, projects 응답, SSE 이벤트 명세,
static cache 헤더).

### 🔴 CRITICAL — 핵심 UX 가 스펙과 불일치

1. **CherryPick 사이드 패널 전체 부재** (`static/app/js/screens/CherryPick.jsx`)
   - spec §6.3 은 우측 380px Side 에 `Preview (zoom-on-i) / MetaList /
     action buttons` 3단 구성. 현재 구현은 grid + keystrip 뿐이고 활성 후보
     미리보기/메타/액션이 없다.
   - 메타 없이는 `seed / model / lora / validation / prompt` 비교가 불가능 →
     체리픽 의사결정의 질이 낮아짐.

2. **단축키 매핑이 스펙과 다름**
   - spec: `Enter=승인` · `x=reject` · `v=비교 토글` · `c=비교 모달` · `i=zoom`
     · `m=메타 hide` · `f=필터 focus` · `1-9=LoRA 필터` · `?=도움말` · `J/K=행 이동`.
   - 현재: `space=pick` / `u=reject` / `1-5=로컬 별점` / `backspace=queue` 만.
   - followups §2 에 "키맵 재검증" 이 걸려 있지만 그게 끝나기 전에도 최소
     `Enter`/`?` 는 채워두는 게 낫다.

3. **비교(Compare) 기능 전면 누락**
   - `<Dialog>` 컴포넌트 자체가 없음. spec §5 의 native `<dialog>` 시맨틱 +
     §6.3 의 `v` toggle/`c` modal/TopBar "비교 set N장" 인디케이터 전부 미구현.

4. **SSE 가 핵심 화면에 미연결 — `Monitor.jsx` 한 곳만 사용**
   - `static/app/js/hooks.jsx` 의 `useSSE` 는 `screens/Monitor.jsx` 에서만
     호출됨. Dashboard/Queue/CherryPick/Batches/BatchDetail 은 전부
     `setInterval` 폴링 (2–5s).
   - spec §7.3 의 이벤트→화면 라우팅 테이블 (candidate_added → grid fade-in,
     task_done → batches invalidate, validation_updated → badge refresh 등)
     전부 붕 떠 있음. 특히 CherryPick 의 "새 후보 실시간 추가" UX 가 손실.

5. **BatchDetail `Tasks` 탭 부재** (`BatchDetail.jsx`)
   - spec §6.6 은 `[Tasks] [Candidates] [Spec]` 3-탭 + "실패만 재생성" 버튼 +
     task 테이블 (id/model/seed/status/attempts/last_error). 현재는 candidates
     + spec 2탭이라 **task-level 실패 분석/재시도 UI 자체가 없음**.
   - 필요 API: task 리스트 조회. `get_batch_detail` 은 집계만 있으므로
     `/api/batches/{id}/tasks` 신규 또는 detail 에 `tasks: [...]` 추가 필요.

### 🟡 HIGH — 기능 누락 (구현 난이도 중)

6. **Cherry-pick 성능 가드 부재**
   - 가상화 없음 (spec §6.3·§11 — 첫 60장 렌더 후 스크롤 로딩).
   - 활성 ±2 prefetch 없음.
   - 스펙상 100장 batch 가 정상인데, 실측 검증 안 됨.

7. **Dashboard 누락 블록**
   - SD 헬스 카드 (spec 와이어프레임), 카테고리별 분포 bar chart (`by_category`
     는 이미 summary API 가 내려줌 — 렌더만 하면 됨).
   - 온보딩 빈 상태의 **sample curl** 도 없음 (재생성 버튼만 있음).

8. **Queue 행 풀 스펙 미구현** (`screens/Queue.jsx`)
   - spec: asset_key 큰 글씨 + project·category badge + `rejected/picked/남음` +
     relative time + progress bar + 작업하기 버튼 + ⋯ 메뉴.
   - 현재: 축약된 table 1행 (project, remaining, total, progress bar 만).
   - `↑↓ 키보드 nav`, "합계: N batches · M장 · 예상 소요 X분" footer 도 없음.

9. **Assets gallery 정리 기능 부족** (`screens/Assets.jsx`)
   - 정렬 컨트롤 없음 (created/updated/asset_key/color_count — spec §6.4).
   - URL 쿼리 직렬화 없음 → 필터 공유 링크 불가.
   - bulk 액션 4종 중 **승인/reject 2개 누락** (재검증/재생성만 있음).
   - `shift+↑↓` 다중 선택 없음 (shift+click 선택은 있음).
   - 페이지네이션 없음 (한 번에 전체 fetch).

10. **Settings 의 auto-advance 가 데드 설정**
    - `af_auto_advance` 를 localStorage 에 저장하지만 `CherryPick.jsx` 의 pick
      핸들러는 **항상** `setCursor(c+1)` 를 호출. 설정값을 읽지 않음.
    - 단축키 도움말 dialog 재출력 버튼도 spec §6.11 에 있는데 구현 X.

11. **System 관측성 부족** (`screens/System.jsx`)
    - spec §6.10: SD / GC / **DB** (path·size·row count) / **Worker** (queue
      depth·current task·uptime) / **Logs tail** 5-block. 현재는 app health +
      SD + GC 3-block 만. DB/Worker/Logs 전부 누락.
    - 필요 API: `GET /api/system/db` (파일 경로·크기·row count), `GET
      /api/system/worker` (현재 claim 중인 task/queue depth), `GET
      /api/system/logs/recent?level=error` — 모두 신규.

12. **AssetDetail 의 history 에 "이 버전으로 복원" 없음**
    - 현재는 read-only 테이블. spec §6.5 는 carousel + 복원 버튼 + 확인 toast.
    - 복원 API 자체는 `POST /api/assets/{id}/select-candidate` 로 커버 가능
      (slot 기반) 이지만 history row 의 `job_id/slot_index` 를 노출해야 UI 에서
      호출할 수 있음.

### 🟢 MEDIUM — 폴리시 갭

13. **접근성 (spec §8) 미충족**
    - cherry-pick grid 에 `role="listbox" + aria-activedescendant` 없음
      (현재는 div role="button").
    - toast `role="status"/"alert"` 없음 (`hooks.jsx::ToastProvider`).
    - `SegProgress` 에 `aria-valuenow` 없음.
    - focus trap (모달) — 모달 자체가 없으니 N/A 지만 나중에 비교 dialog 낼 때
      함께 처리 필요.

14. **BatchNew wizard 스펙 대비 축소**
    - Step 3 common: `sampler/width/height/max_colors/negative_prompt` 편집 불가
      (negative 는 있고 sampler='DPM++ 2M' 하드코딩, width/height/max_colors
      서버 기본값 의존).
    - expand count > 200 경고 없음.
    - "미리보기 JSON" 버튼 없음.
    - loras 를 `list[list[LoraSpec]]` 매트릭스로 빌드하는 UI 가 없고 단일 그룹.

15. **Catalog 를 카드 뷰가 아닌 table 로 구현**
    - spec §6.8: 카드 + 사용된 batch 수 (역참조) + 마지막 사용 시각 + 상세
      패널 + "이 모델로 batch 만들기" 프리필 버튼.
    - 현재는 table 에 카탈로그 메타만. 역참조/상세/프리필 전부 없음.

16. **Export 미완**
    - 카테고리/since 필터 없음 (project 만).
    - 라이브 사이즈(MB) 카운트 없음 — `totalBytes = 0` 주석으로 표시됨.
    - 결과 영역의 "파일 트리 프리뷰" 없음 (성공 toast 만).

17. **에러/알림 폴리시**
    - spec §9: 5xx 시 "다시 시도" 버튼 + 에러 본문 펼치기. 현재는 error-banner
      텍스트만 보여줌.
    - spec: 401 시 상단 banner "API key 없이 일부 기능 제한 · /settings".
      없음.
    - spec §10: "SD WebUI 연결 끊김" persistent top banner. 없음 (side-rail
      dot 만).

### 🔵 LOW — 장기 과제

18. **localStorage 키 네이밍 스펙 불일치**
    - spec §13 은 기존 `assetFactoryApiKey` 유지. 현재는 `af_api_key` 로 리네이밍.
      기존 레거시 UI 저장소를 쓰던 사용자는 key 를 다시 입력해야 함.
    - 마이그레이션 코드 (`localStorage.getItem('assetFactoryApiKey') →
      af_api_key`) 한번만 실행하고 덮어쓰면 깔끔.

19. **모바일 (≤768px) 시나리오 P5**
    - CherryPick: 3-col grid / 하단 drawer / tap-pick / swipe-compare /
      long-press reject. 전부 미구현 (현재 CSS `.cherry-grid` 는 고정 컬럼).

20. **분석/계측 콘솔 이벤트 (spec §12)**
    - `cherrypick_session_started/_ended`, `time_to_first_pick`,
      `keyboard_action vs mouse_action` 비율. 아무 것도 계측 안 됨.

21. **TopBar 의 API key 인디케이터 없음**
    - spec §5: TopBar 우측에 "no-api-key" 상태 표시. 현재 chrome.jsx 의
      `AppTopBar` 는 SD dot + 큐 카운트 + 아바타만.

### 기타 관찰

- app entry 에 `/regen`, `/monitor`, `/errors` 라우트가 남아 있는데 spec IA
  에는 없는 경로. Side-nav 에도 노출됨. 정리하려면 (a) 정식 화면으로 spec 에
  편입하거나 (b) 제거. 특히 `/regen` 은 `/batches/new` 와 기능 중복,
  `/monitor` 는 `useSSE` 의 유일한 사용자라 제거 전에 핵심 화면으로 이식 필요.
- `index.html` 의 `SCREEN_SCRIPTS` 로딩 순서는 현재 알파벳 순이 아니라 라우터
  매칭 순서에 영향이 없지만, 새 screen 추가 시 빠뜨리기 쉬움. 스크립트
  manifest 를 별도 파일로 분리하거나 glob 기반 로더를 두는 게 덜 깨짐.

### 추천 다음 스프린트

우선순위 높은 것만 묶어서 작업할 때:

- **스프린트 A (cherry-pick 살리기)**: 1 (사이드 패널) + 2 (키맵) + 4 (SSE
  candidate_added) + 6 (가상화). 체리픽 하나가 spec 의 원형이 되는 "거룩한"
  화면이므로 여기부터.
- **스프린트 B (운영 가시성)**: 5 (Tasks 탭) + 11 (System DB/Worker/Logs) +
  17 (persistent banner). ops 가 실패를 볼 방법이 없음.
- **스프린트 C (폴리시)**: 9 (Assets 정렬/URL/bulk) + 10 (auto-advance 실배선)
  + 13 (a11y).
