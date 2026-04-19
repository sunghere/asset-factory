# Asset Factory — 화면 요구사항 정의서 (v0.2)

> 본 문서는 v0.1.5 디자인 리프레시 이후 **전체 웹 화면을 0부터 다시 설계**하기 위한 spec.
> 코드/DB는 그대로 유지하고, **프론트엔드 + IA + 디자인 시스템 + 인터랙션**을 새로 정의.

## 0. 메타

| 항목 | 내용 |
|---|---|
| 버전 | v0.2 (full redesign) |
| 대상 | Asset Factory 웹 UI 전체 |
| 베이스 라우트 | FastAPI (`server.py`) — 라우트는 변경 X. 프런트만 SPA화 검토 |
| 기존 페이지 | `static/index.html` (대시보드), `static/cherry-pick.html` (체리픽) |
| 통신 | REST + SSE (`/api/events`) |
| 인증 | `x-api-key` (선택, 환경변수 `API_KEY` 설정 시) |
| 브라우저 지원 | 최신 Chromium, Safari 17+, Firefox 최신 |
| 주 입력기기 | 데스크톱 (마우스+키보드) / 태블릿 보조 / 모바일은 체리픽 진행만 |

## 1. 제품 컨텍스트

### 1.1 한 줄 정의

> **Agent가 이미지 생성 요청을 큐잉하고, 사람이 cherry-pick으로 빠르게 검수해 승인본만 내보내는 워크벤치.**

### 1.2 핵심 사용자 & Jobs To Be Done

| 사용자 | 가장 자주 하는 일 | 성공 기준 |
|---|---|---|
| **검수자 (Reviewer)** | 매일 아침 큐의 batch들을 훑고 1장씩 승인 | 1 batch당 사람 손 시간 ≤ 5분, j/k/x/Enter만으로 끝남 |
| **에이전트 운영자 (Agent Operator)** | batch 상태 모니터, 실패 재시도 | 에러 batch에 1클릭으로 도달 가능 |
| **에셋 소비자 (Game/App dev)** | 승인본을 export, manifest로 게임에 통합 | 클릭 3번 안에 export ZIP까지 |
| **시스템 관리자** | SD 헬스, GC, 디스크 사용량 모니터 | 한 화면에 핵심 헬스 지표 |

### 1.3 사용량 가정 (설계 입력)

- 1 batch = 평균 **20–100장 후보** (모델×LoRA×seed 곱집합)
- 하루 batch 수 = 5–30개
- 동시 사용자 = 1명 (single-tenant 자가 호스팅)
- 그리드 한 화면 표시 후보 = **20–60장**

---

## 2. 디자인 원칙

| # | 원칙 | 의미 | 반영 |
|---|---|---|---|
| 1 | **Cherry-pick is sacred** | 검수 화면이 가장 자주 쓰이는 화면. 다른 모든 화면은 거기로 빠르게 진입시키는 통로. | 모든 페이지에서 "오늘 큐 N batches" 핫링크 상시 노출 |
| 2 | **Keyboard first** | 마우스 없이도 끝까지 갈 수 있어야 함. | j/k/x/Enter/v/c/i/?/m/Esc 글로벌 단축키 |
| 3 | **Undo, never confirm** | "정말 삭제하시겠습니까?" 모달 금지. 5초 toast로 실행취소. | reject / approve 둘 다 undo toast |
| 4 | **Surface the work, hide the plumbing** | 평소엔 candidates/jobs/SD 호스트 같은 plumbing 안 보임. 문제 생길 때만 노출. | "시스템" 화면을 별도 분리, 헬스 dot만 토프바에 |
| 5 | **Dense by default, zoomable on demand** | 검수자 모드는 많이 보여야 함. 필요할 때 i 키로 zoom. | 5열 그리드 기본, pixel-perfect zoom 토글 |
| 6 | **Dark by birth** | 픽셀아트/도트는 어두운 배경에서 색이 정확히 보임. | 다크모드 only (`--bg-base: #0f1115`) |
| 7 | **One motion language** | 모든 모션은 `--ease-out` + 120/200/320ms 3단. | 토큰화된 motion duration |
| 8 | **Honest empty states** | 빈 상태에서도 다음 액션을 알려줌 (curl 샘플 등). | 체리픽 빈 상태에 sample curl (이미 구현) |

---

## 3. 정보 구조 (IA)

```
/                       대시보드 (홈)
/cherry-pick            체리픽 작업화면 (개별 batch)
  ?batch=<id>             특정 batch 직접 진입
/queue                  체리픽 큐 (오늘의 batch 리스트, 신규)
/assets                 에셋 갤러리 (라이브러리)
  /assets/:asset_id       에셋 상세 (히스토리, 후보, 재생성)
/batches                배치 모니터 (job/태스크 상태)
  /batches/:batch_id      배치 상세 (태스크별 진행률, 실패 분석)
/batches/new            새 배치 요청 (manual enqueue)
/catalog                SD 모델/LoRA 카탈로그
/export                 내보내기 + manifest
/system                 헬스/GC/디스크/SD 호스트
/settings               API key, 표시 옵션
```

> **현재 vs 신규 매핑**
>
> - 현재 `/`는 모든 기능을 한 페이지에 욱여넣음 → 신규에선 **대시보드는 요약만**, 액션은 각 전용 화면으로 분기.
> - 현재 `/cherry-pick`는 batch select가 화면 안에 있음 → 신규엔 **`/queue`가 1단 진입점**, `/cherry-pick?batch=…`는 작업화면.

---

## 4. 글로벌 디자인 시스템

### 4.1 토큰 (현재 `style.css` 기반 확장)

```css
/* surface */
--bg-base:      #0f1115;
--bg-elev-1:    #171b22;   /* 카드 */
--bg-elev-2:    #1d222b;   /* 강조 카드 / pill */
--bg-elev-3:    #232a36;   /* 인터랙티브 hover */
--bg-input:     #0f131a;
--border-subtle:#2a313d;
--border-strong:#3a4250;

/* text (모두 4.5:1 대비 검증 완료) */
--text-primary:  #f0f3f8;
--text-secondary:#b8c1d0;
--text-muted:    #8590a3;
--text-faint:    #6b7484;

/* semantic accents */
--accent-pick:           #8fb8ff;   /* 비교 / link */
--accent-pick-strong:    #4c8dff;
--accent-approve:        #f5d76e;   /* 승인 (gold) */
--accent-approve-strong: #f0b441;
--accent-reject:         #ff7a7a;
--accent-reject-bg:      #3a1f1f;
--accent-success:        #6ee7a8;
--accent-warning:        #ffb86b;   /* 신규 — 부분 실패/경고 */
--accent-info:           #8fb8ff;

/* radius */
--radius-sm: 6px;  --radius-md: 10px;  --radius-lg: 14px;  --radius-pill: 999px;

/* spacing (4 베이스) */
--space-1: 4px; --space-2: 8px; --space-3: 12px;
--space-4: 16px; --space-5: 24px; --space-6: 32px;

/* type — Pretendard Variable */
--type-12: 12px;  --type-13: 13px;  --type-14: 14px;
--type-16: 16px;  --type-20: 20px;  --type-28: 28px;
--font-sans: "Pretendard Variable", ...;
--font-mono: ui-monospace, "SF Mono", Menlo, ...;

/* motion */
--motion-fast: 120ms;
--motion-mid:  200ms;
--motion-slow: 320ms;
--ease-out: cubic-bezier(0.2, 0.8, 0.2, 1);

/* focus */
--focus-ring: 0 0 0 2px rgba(143, 184, 255, 0.55);
```

### 4.2 타이포그래피 스케일

| 토큰 | 크기 | 용도 |
|---|---|---|
| `--type-28` | 28 / 36 | 페이지 타이틀 |
| `--type-20` | 20 / 28 | 섹션 헤더 |
| `--type-16` | 16 / 24 | 카드 헤더, 폼 라벨 강조 |
| `--type-14` | 14 / 20 | 본문 기본 |
| `--type-13` | 13 / 18 | 메타, 보조 |
| `--type-12` | 12 / 16 | 캡션, badge |

### 4.3 그리드/레이아웃 베이스

- **데스크톱 컨테이너 폭**: 1280px max, 16px gutter
- **사이드바 너비**: 220px (collapsible 56px)
- **체리픽 메타 패널**: 380px 고정
- **카드 최소 width**: 280px (responsive grid `auto-fill`)

### 4.4 브레이크포인트

| 이름 | 폭 | 동작 |
|---|---|---|
| `xs` | <480 | 단일 컬럼, 사이드바 hide, 체리픽 그리드 3열 |
| `sm` | 480–768 | 모바일 체리픽 (drawer 패턴, swipe) |
| `md` | 768–1024 | 태블릿, 사이드바 collapsible |
| `lg` | 1024–1440 | 데스크톱 기본, 사이드바 펼침 |
| `xl` | ≥1440 | 체리픽 그리드 6–7열까지 확장 |

### 4.5 모션 가이드

- 모든 transition: `var(--motion-fast) var(--ease-out)` (default)
- 페이드/스케일 인: `--motion-mid`
- 큰 modal/drawer 슬라이드: `--motion-slow`
- `prefers-reduced-motion: reduce` → 1ms로 강제

---

## 5. 글로벌 컴포넌트

| 컴포넌트 | 설명 | 상태 변형 |
|---|---|---|
| `<TopBar>` | 좌: 로고+네비, 우: 큐 pill / SD 헬스 dot / 사용자 메뉴 / API key 인디케이터 | normal, sd-down, no-api-key |
| `<SideNav>` | 1차 네비 (대시보드, 큐, 갤러리, 배치, 카탈로그, 내보내기, 시스템) | expanded / collapsed (≥md만) |
| `<QueuePill>` | "오늘 큐 **N** batches (M장 남음)" — TopBar 상시 노출 | 0건일 때 회색, ≥1 골드 |
| `<HealthDot>` | SD WebUI 연결 상태 (green/red/gray=unknown) | tooltip에 host + last check |
| `<Card>` | `--bg-elev-1` + 1px border + `--radius-md` | hover (elev-2), selected (border-accent) |
| `<Button>` | primary (gold-strong) / pick (blue-strong) / danger (red) / ghost (transparent) | min-height 32px, mobile 48px |
| `<KeyChip>` `<kbd>` | 단축키 표기 | inline, mono 폰트 |
| `<Toast>` | 우하단 stack, role="status" / role="alert" | success / error / info, action 버튼 (실행취소) |
| `<Dialog>` | `<dialog>` native, esc로 닫힘 | modal / non-modal |
| `<EmptyState>` | 아이콘 + 헤딩 + 1문장 설명 + **다음 액션 버튼/curl 샘플** | 항상 액션 1개 이상 포함 |
| `<ProgressBar>` | 3-segment (approved gold / rejected red / remaining transparent) | aria-valuenow 자동 |
| `<Badge>` | seed/model/lora/category/status 표시 | 색상은 의미 매핑 (semantic) |
| `<Filter pill>` | toggle on/off | active=accent-pick |
| `<DataTable>` | 정렬, 행 hover, 키보드 nav (↑↓), 무한 스크롤 | empty / loading / error |
| `<MetaList>` | key:value 박스 (체리픽 사이드와 동일 패턴) | `<dl>` 시맨틱 사용 |

### 5.1 폼 컨트롤 표준

- 입력 높이: 32px (`--space-2`*2 + 14px 라인)
- 라벨: `<label>`로 wrap, 좌측 정렬 (인라인) / 모바일에선 위 라벨
- 검증 에러: 인라인, 빨강 텍스트 + `aria-invalid="true"`
- 자동 저장: 200ms 디바운스 + ✓ 인디케이터 (예: API key 저장)

---

## 6. 화면별 상세 요구사항

### 6.1 `/` — 대시보드 (홈)

#### 목적
오늘 시작할 일을 한 눈에 보여주고, 가장 큰 액션 1개를 강조.

#### 화면 구성
```
┌──────────────────────────────────────────────────────────────────┐
│ TopBar  Asset Factory   [큐 pill: 12 batches · 247장 남음] [SD●]│
├──────┬───────────────────────────────────────────────────────────┤
│ Side │ ┌─ Hero CTA ─────────────────────────────────────────┐   │
│ Nav  │ │ 오늘 큐 12 batches · 247장 남음                    │   │
│      │ │ [▶ 체리픽 시작하기]   (가장 위, 가장 큼)           │   │
│      │ └────────────────────────────────────────────────────┘   │
│      │                                                           │
│      │ ┌─ 요약 카드 (4개 그리드) ──────────────────────────┐   │
│      │ │ Pending  Approved  Rejected  실패 작업           │   │
│      │ │  247     1,820      315      3 ⚠               │   │
│      │ └──────────────────────────────────────────────────┘   │
│      │                                                           │
│      │ ┌─ 최근 활동 타임라인 ──────────┐ ┌─ SD 헬스 ──┐    │
│      │ │ • 14:02 batch_marine_v2 done │ │ host:7860  │    │
│      │ │ • 13:55 5 candidates rejected│ │ models 7   │    │
│      │ │ • 13:47 batch enqueued (40)  │ │ loras 23   │    │
│      │ └──────────────────────────────┘ │ disk 1.2GB │    │
│      │                                   └────────────┘    │
│      │ ┌─ 카테고리별 분포 ─────────────────────────────────┐   │
│      │ │ character ████████ 412   ui ████ 188             │   │
│      │ │ background ███ 96  item ██ 47                    │   │
│      │ └──────────────────────────────────────────────────┘   │
└──────┴───────────────────────────────────────────────────────────┘
```

#### 상호작용
- Hero CTA "체리픽 시작하기" 클릭 → `/queue` (큐가 0이면 disabled + "오늘은 enqueue된 batch 없음")
- 요약 카드 클릭 → `/assets?status=…` 로 필터된 갤러리
- "실패 작업 N" 클릭 → `/batches?status=failed`
- 최근 활동 항목 클릭 → 해당 batch / asset 상세
- SD 헬스 카드 클릭 → `/system`

#### 데이터
| 항목 | API | 갱신 |
|---|---|---|
| 큐 카운트 | `GET /api/cherry-pick/queue` | 30s polling + SSE `batch_*` 시 invalidate |
| 요약 | `GET /api/assets/summary` | 60s polling |
| 최근 활동 | `GET /api/jobs/recent?limit=10` + SSE | 실시간 |
| SD 헬스 | `GET /api/health/sd` | 30s polling |
| 카테고리별 | `GET /api/assets/summary` (project별 grouped) | 60s |

#### 빈 상태
- 큐 0 + Pending 0 + Approved 0 → **온보딩 모드**: "첫 batch를 보내려면 아래 curl을 실행하거나 [수동 배치 요청]을 눌러보세요" + 샘플 curl + `/batches/new` 링크

#### 성공 지표
- TTFI ≤ 800ms, 첫 의미 콘텐츠(큐 카운트) ≤ 400ms

---

### 6.2 `/queue` — 체리픽 큐 (신규)

#### 목적
**오늘 처리할 batch 목록만 모아서 1클릭에 작업화면으로 보내는** 페이지. 현재 cherry-pick.html 안에 있던 batch select를 분리.

#### 화면 구성
```
┌──────────────────────────────────────────────────────────────────┐
│ TopBar                                                           │
├──────┬───────────────────────────────────────────────────────────┤
│ Side │ Cherry-Pick 큐                                            │
│      │ since [2026-04-19 00:00 ▼]    [숨김:완료된 batch ☑]       │
│      │ ┌─────────────────────────────────────────────────────┐  │
│      │ │ ★ marine_v2_idle                  cat-raising · char│  │
│      │ │ 100장 후보 · 12 rejected · 88 남음 · 1h ago         │  │
│      │ │ ▰▰▰▰▱▱▱▱▱▱  [▶ 작업하기 (Enter)]                  │  │
│      │ ├─────────────────────────────────────────────────────┤  │
│      │ │   bg_lobby_v3                     cat-raising · bg  │  │
│      │ │ 40장 · 0 rejected · 40 남음 · 2h ago                │  │
│      │ │ ▱▱▱▱▱▱▱▱▱▱  [▶ 작업하기]                          │  │
│      │ ├─────────────────────────────────────────────────────┤  │
│      │ │ ✓ ui_button_set (approved)                          │  │
│      │ │ 20장 · 14 rejected · 1 picked · 5h ago              │  │
│      │ │ ▰▰▰▰▰▰▰▰▰▰  [상세 보기]                           │  │
│      │ └─────────────────────────────────────────────────────┘  │
│      │ 합계: 12 batches · 247장 남음 · 예상 소요 21분           │
└──────┴───────────────────────────────────────────────────────────┘
```

#### 행 구성
- 좌측 상태 아이콘: ★(미진행) / ◐(진행중, 일부 picked) / ✓(approved, 회색)
- 1행: asset_key (큰 글씨) + project · category badge
- 2행: 후보 카운트 + rejected/picked/남음 + relative time
- 3행: progress bar + 작업하기 버튼

#### 상호작용
- 행 클릭 / Enter → `/cherry-pick?batch=<id>`
- ↑↓ 키로 행 이동, Enter로 진입
- "숨김:완료된 batch" off하면 approved batch도 보임
- 행 우측에 ⋯ 메뉴: "이 batch 모두 reject" / "복사 batch_id"

#### 데이터
| 항목 | API |
|---|---|
| 큐 | `GET /api/cherry-pick/queue?since=…` |
| 진행률 | 응답의 `remaining/candidate_total` 계산 |
| 예상 소요 | (남은 장 수) × 1.5초 추정치 (UX 기대치 설정용) |

#### 빈 상태
- 큐 0 → 큰 일러스트 + "오늘 처리할 batch가 없습니다" + 샘플 curl + `/batches/new` 버튼 (현재 cherry-pick의 빈 상태와 동일 패턴)

---

### 6.3 `/cherry-pick?batch=<id>` — 체리픽 작업화면 (재설계)

#### 목적
한 batch를 빠르게 훑고 1장 승인. **세션 내내 손이 키보드에서 떨어지지 않게.**

#### 레이아웃 (현재보다 메타 패널 폭 ↓ + 비교 영역 정식 컴포넌트화)
```
┌─ TopBar ───────────────────────────────────────────────────────┐
│ ← /queue  [batch select▼]  [reload]  filter: model▼ lora▼     │
│ □ rejected숨김  [비교(c)] [?]  status: 12/100              [ ]│
├─ Progress ─────────────────────────────────────────────────────┤
│ 오늘 큐 › marine_v2_idle    ▰▰▰▰▱▱▱▱▱▱   12 / 100 (남음 88) │
├──────────────────────────────────┬─────────────────────────────┤
│ Grid (5열, 가상 스크롤)         │ Side (380px)               │
│ ┌──┐┌──┐┌──┐┌──┐┌──┐           │ ┌─ Preview (zoom on i) ──┐ │
│ │  ││  ││  ││  ││  │           │ │   현재 활성 후보       │ │
│ └──┘└──┘└──┘└──┘└──┘           │ └────────────────────────┘ │
│ ┌──┐┌──┐┌──┐┌──┐┌──┐           │ ┌─ Meta ────────────────┐ │
│ │  ││  ││  ││  ││  │           │ │ asset_key [입력]     │ │
│ └──┘└──┘└──┘└──┘└──┘           │ │ candidate #45 slot 12 │ │
│ ...                              │ │ seed 1234   model x   │ │
│                                  │ │ lora marine@0.7 sad@.5│ │
│                                  │ │ validation pass · 32c │ │
│                                  │ │ prompt … (펼치기)     │ │
│                                  │ │ metadata_json (펼치기)│ │
│                                  │ └────────────────────────┘ │
│                                  │ [x reject][v 비교][Enter ✓]│
│                                  │ kbd: j/k x v c Enter i ?  │
└──────────────────────────────────┴─────────────────────────────┘
```

#### 신규 도입 사항 (현재 대비 추가/개선)
1. **TopBar 통합**: batch select / filter / 단축키 도움말이 한 줄에 정리.
2. **breadcrumb 표시**: "오늘 큐 › <asset_key>" — 큐로 돌아가는 길을 항상 보여줌.
3. **비교 다이얼로그 정식화**: 현재도 있지만 "비교 set 인디케이터(현재 N장)"를 TopBar 옆에 항상 표시.
4. **자동 진행 (Auto-advance) 토글**: Enter 후 자동으로 다음 후보 활성화 (기본 on, 토글 가능).
5. **그리드 가상화**: 100장 이상에서 스크롤 끊김 방지 (첫 60장 렌더, scroll 시 추가).
6. **비활성 상태에서도 미리보기 lazy preload**: 활성 + 좌우 ±2장 이미지 prefetch.
7. **메타 패널 접기**: `m` 키로 메타 hide → 그리드 풀폭 (현재 일부만 구현).

#### 단축키 (현재 + 신규 ★)
| 키 | 동작 |
|---|---|
| `j` / `→` | 다음 후보 |
| `k` / `←` | 이전 후보 |
| `J` / `K` | 다음/이전 행 (★ 신규) |
| `x` | reject (5초 undo toast) |
| `v` | 비교 set 토글 |
| `c` | 비교 모달 열기 |
| `Enter` | 승인 → 다음 후보 |
| `i` | 미리보기 zoom (pixel-perfect) |
| `m` | 메타 패널 hide/show |
| `f` | 필터 input 포커스 (★ 신규) |
| `1`–`9` | n번째 LoRA 필터 토글 (★ 신규) |
| `Esc` | 모달/zoom/spacer 닫기 |
| `?` | 단축키 도움말 |

#### 상태
| 상태 | 트리거 | 표시 |
|---|---|---|
| 로딩 | batch 처음 로드 | skeleton 그리드 (회색 박스 5×4) |
| 빈 후보 | candidates=0 | "이 batch에 후보가 없습니다" + batch 상세 링크 |
| 모두 reject | filtered=0, hide-rejected on | "모두 rejected. [x] 풀고 다시 보기" |
| reject 중 에러 | 5xx | toast error + 즉시 UI rollback |
| 승인 후 새 batch 자동 이동 | 마지막 승인이 batch 끝 | 3초 카운트다운 후 다음 batch 자동 진입 (★ 신규) |

#### 모바일 (≤768px)
- 그리드 3열, 사이드는 하단 drawer (collapsed=120px height)
- tap = 활성 + 승인, swipe-right = 비교, long-press = reject
- 단축키 영역 hide

#### API 매핑
| 액션 | 엔드포인트 |
|---|---|
| 큐/배치 메타 | `GET /api/cherry-pick/queue` |
| 후보 목록 | `GET /api/batches/{batch_id}/candidates` |
| reject | `POST /api/batches/{id}/candidates/{cid}/reject` |
| unreject | `POST /api/batches/{id}/candidates/{cid}/unreject` |
| 승인 | `POST /api/assets/approve-from-candidate` (category는 batch에서 자동) |
| 후속 picked 추적 | (서버 자동) `picked_at`, `picked_asset_id` 셋팅 |
| undo-approve | `POST /api/assets/{asset_id}/undo-approve` |
| 실시간 alert | SSE `/api/events` 구독 |

---

### 6.4 `/assets` — 에셋 갤러리 (라이브러리)

#### 목적
승인본을 포함한 **전체 에셋 라이브러리** 탐색/필터/일괄작업.

#### 화면 구성
```
┌─ TopBar ───────────────────────────────────────────────────────┐
├──────┬─────────────────────────────────────────────────────────┤
│ Side │ Filter sidebar (left, 220px)        Result grid (right) │
│ Nav  │ ┌─ Project ─────┐  ┌──────────────────────────────────┐│
│      │ │ ☑ cat-raising │  │ ┌──┐┌──┐┌──┐┌──┐┌──┐ 1247 hits ││
│      │ │ ☐ default     │  │ │  ││  ││  ││  ││  │            ││
│      │ ├─ Status ──────┤  │ └──┘└──┘└──┘└──┘└──┘            ││
│      │ │ ☑ pending     │  │ ┌──┐┌──┐┌──┐┌──┐┌──┐            ││
│      │ │ ☑ approved    │  │ │  ││  ││  ││  ││  │            ││
│      │ │ ☐ rejected    │  │ └──┘└──┘└──┘└──┘└──┘            ││
│      │ ├─ Category ────┤  │ (가상 스크롤, 무한 페이징)       ││
│      │ │ char ui bg item│ └──────────────────────────────────┘│
│      │ ├─ Validation──┤                                        │
│      │ │ ◉ all  ○pass  │  Bulk: ☐ 선택한 N개                  │
│      │ │      ○fail    │  [재검증][재생성][승인][reject]       │
│      │ ├─ Search ──────┤                                        │
│      │ │ asset_key… 🔍 │                                        │
│      │ └───────────────┘                                        │
└──────┴─────────────────────────────────────────────────────────┘
```

#### 카드 한 장
- 정사각 썸네일 + 상태 dot (좌상) + validation badge (우상)
- 하단 strip: `<asset_key>` + `<seed>` + relative time
- hover: 컬러 카운트 / 크기 미니 표시

#### 상호작용
- 카드 클릭 → `/assets/:id` (상세 라우트)
- shift+클릭 / shift+↑↓ → 다중 선택
- bulk 액션 4종은 항상 footer에 floating bar로 (선택 ≥1일 때 활성화)
- 정렬: created desc (default), updated desc, asset_key asc, color_count asc/desc
- URL 쿼리에 필터 직렬화 (공유 가능 링크)

#### 데이터
- `GET /api/assets?project=&status=&category=&validation=&q=&offset=&limit=` (필터 쿼리는 서버에 추가 필요 — 현재는 일부만 지원, 클라이언트 필터로 보완 가능)
- 일괄: `POST /api/validate/all`, `POST /api/batch/regenerate-failed`, `POST /api/batch/revalidate-failed`

---

### 6.5 `/assets/:asset_id` — 에셋 상세

#### 목적
한 에셋의 **현재 primary + 이전 버전 history + 후보 슬롯** 한 화면.

#### 화면 구성
```
┌─ Topbar + breadcrumb: assets › cat-raising › marine_v2_idle ──┐
├────────────────────────────────────────────────────────────────┤
│ ┌─ Primary preview (좌, 50%) ─┐  ┌─ Meta + Actions (우) ────┐│
│ │                              │  │ status: approved          ││
│ │   현재 primary 이미지        │  │ project / asset_key      ││
│ │   (zoom 토글)                │  │ category / created       ││
│ │                              │  │ validation pass · 32c     ││
│ │                              │  │ generation seed/model     ││
│ │                              │  │ prompt (펼치기)           ││
│ │                              │  │ metadata_json (펼치기)    ││
│ └──────────────────────────────┘  │                           ││
│                                    │ [▶ 다시 생성]            ││
│                                    │ [🔁 재검증]              ││
│                                    │ [↶ 승인 취소(undo)]     ││
│                                    │ [✗ Reject]               ││
│                                    └───────────────────────────┘│
│ ┌─ History (가로 스크롤 carousel) ─────────────────────────────┐│
│ │ v3 (current)  v2 (gold border) v1                            ││
│ │ ┌──┐ ┌──┐ ┌──┐                                              ││
│ │ │  │ │  │ │  │   각 카드: 썸네일 + version + created       ││
│ │ └──┘ └──┘ └──┘   클릭 → "이 버전으로 복원" 버튼 (확인 toast)││
│ └──────────────────────────────────────────────────────────────┘│
│ ┌─ 후보 슬롯 (이 에셋이 만들어진 batch의 다른 후보) ──────────┐│
│ │ ┌──┐┌──┐┌──┐┌──┐  클릭 → 이 슬롯으로 primary 교체         ││
│ └──────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────┘
```

#### 데이터
- `GET /api/assets/{id}/detail`
- `GET /api/assets/{id}/history`
- `GET /api/assets/{id}/candidates?job_id=…` (slot 별)
- 액션: `POST /api/assets/{id}/regenerate`, `POST /api/validate/{id}`, `POST /api/assets/{id}/undo-approve`, `POST /api/assets/{id}/select-candidate`

---

### 6.6 `/batches` & `/batches/:batch_id` — 배치 모니터

#### 목적
agent가 enqueue한 batch들의 **task-level 진행률 / 실패 분석 / 재시도**.

#### 리스트 (`/batches`)
- 테이블: batch_id · project · asset_key · created · status (queued/processing/done/failed) · 진행률 bar · ⋯
- 필터: status, project, since
- 정렬: created desc

#### 상세 (`/batches/:batch_id`)
```
header: batch_id  cat-raising/marine_v2_idle  100 tasks  12 failed
        [▶ 실패만 재생성]  [↻ 새로고침]  [↗ 체리픽으로]

tabs: [Tasks] [Candidates] [Spec]

Tasks 탭:
  진행 메트릭 4개: queued / processing / done / failed
  task 테이블: id · model · seed · status · attempts · last_error · 최근 시각
              status가 failed면 행 빨강 + 마지막 에러 라이트박스로 펼침

Candidates 탭:
  체리픽과 동일한 그리드 (read-only) — 빠른 미리보기

Spec 탭:
  expand된 spec JSON 뷰 (folding) + "복사 curl" 버튼
```

#### 데이터
- `GET /api/batches?status=&since=` (현재 존재)
- `GET /api/batches/{id}/candidates`
- 실패 재시도: `POST /api/batch/regenerate-failed?batch_id=…`
- spec/meta 보강은 서버 측 추가 필요 (현재 spec 자체 저장이 없으면 client에서 enqueue 시 캐시)

---

### 6.7 `/batches/new` — 수동 배치 요청

#### 목적
agent 없이 사람이 직접 batch enqueue. **agent와 같은 schema를 form으로 빌드**.

#### 화면 구성 (3-step wizard)
```
Step 1: Target
  project [select/free] · asset_key · category [select]

Step 2: Generation
  prompts (textarea, 한 줄 = 1 prompt)
  models [multi-select from /api/sd/catalog/models]
  loras  [matrix builder: 한 행 = 한 LoRA 그룹 (list[list[LoraSpec]])
                          + weight slider]
  seeds_per_combo [number]

Step 3: Common params
  steps · cfg · sampler · width · height · negative_prompt · max_colors
  [좌측에 expand 미리보기: prompts × models × loras × seeds = N tasks]

footer: [← 뒤로] [↗ 미리보기 JSON] [▶ Enqueue]
```

#### 검증
- 클라이언트에서 `DesignBatchRequest` 스키마 검증 (zod-like)
- expand 결과 N > 200이면 경고 ("작업이 많습니다, 정말 enqueue?")
- enqueue 성공 시 toast + `/batches/:id` 자동 이동

#### API
- 카탈로그: `GET /api/sd/catalog/models`, `GET /api/sd/catalog/loras`
- enqueue: `POST /api/batches`

---

### 6.8 `/catalog` — SD 모델/LoRA 카탈로그

#### 목적
사용 가능한 모델/LoRA 목록을 카드로 보여주고, 미리보기/메타 노출.

#### 구성
- 탭: [Models] [LoRAs]
- 카드 한 장: 이름 · 카테고리 · 추천 weight · 사용된 batch 수(역참조) · 마지막 사용 시각
- 우측 상세 패널: 설명 / 샘플 prompt / 호환 모델 (LoRA의 경우)
- "이 모델로 batch 만들기" → `/batches/new?model=…` prefilled

#### API
- `GET /api/sd/catalog/models`, `GET /api/sd/catalog/loras`

---

### 6.9 `/export` — 내보내기

#### 목적
승인본을 디스크/ZIP으로 내보내고 manifest 생성.

#### 구성
- 좌: 필터 (project / category / since)
- 중앙: 미리보기 카드 그리드 (read-only)
- 우 floating: 내보내기 패널
  - export dir 입력 (기본 `~/workspace/assets`)
  - manifest 저장 ☑
  - "포함될 N개 / 약 X MB" 라이브 카운트
  - [▶ 내보내기]
- 결과 영역: manifest URL · 파일 트리 미리보기

#### API
- `POST /api/export`
- `GET /api/export/manifest`

---

### 6.10 `/system` — 헬스 / GC / 디스크

#### 구성
```
[SD WebUI ●green]  host: 192.168.50.225:7860  models: 7  loras: 23
[GC      ]   data/candidates/ 832MB / 1GB  rejected: 12  oldest: 5d
                                          [▶ 지금 GC 실행]
[DB      ]   asset-factory.db  124MB  rows: 2,043 assets · 5,128 cands
[Worker  ]   queue depth: 3  current: task_812  uptime: 4h 12m
[Logs (tail)]  최근 에러 5건 (stack 펼치기)
```

#### API
- `GET /api/health`, `GET /api/health/sd`
- `GET /api/system/gc/status`, `POST /api/system/gc/run`

---

### 6.11 `/settings`

| 섹션 | 항목 |
|---|---|
| API Key | 입력 (password 마스킹) + 저장 + 마스킹 미리보기 |
| 표시 | 그리드 기본 열 수 (4/5/6), 모션 강도 (full/reduced/none), 자동 advance on/off |
| 단축키 | 도움말 dialog 다시 보기 / 키 매핑 표 |
| 데이터 | DB 경로 / 데이터 디렉토리 (read-only 표시), CSV/JSON export |
| 정보 | 버전, 빌드 SHA, 호스트 |

---

## 7. 글로벌 인터랙션 패턴

### 7.1 Undo Toast 패턴
- 모든 파괴적 액션 (reject / approve / restore version)은 **즉시 실행 + 5초 toast**.
- toast 안에 `[실행취소]` 버튼. 클릭하면 역연산 호출.
- toast 사라지기 전 ESC = 실행취소.
- 라벨 일관성: "M장 reject됨 · 실행취소 (esc)" / "승인됨 · 실행취소"

### 7.2 자동저장
- 폼 입력은 200ms 디바운스 후 자동 저장. 우상단 미니 인디케이터: `· 저장 중` / `✓ 저장됨` / `! 실패`

### 7.3 SSE 이벤트 처리
- `/api/events` 구독은 SPA 진입 시 1회.
- 이벤트 타입별 라우팅:

  | type | 영향 화면 |
  |---|---|
  | `batch_enqueued` | 큐 pill 카운트 +1, 대시보드 활동 |
  | `task_done` / `task_failed` | 배치 모니터 진행률 |
  | `candidate_added` | 체리픽 그리드에 fade-in |
  | `asset_approved_from_candidate` | 갤러리/큐 invalidate |
  | `asset_approve_undone` | 동일 |
  | `gc_run_completed` | 시스템 화면 |

### 7.4 단축키 충돌 회피
- input/textarea 포커스 시 글로벌 단축키 비활성화 (현재 패턴 유지).
- `?` 도움말은 어디서든 떠야 함.

---

## 8. 접근성 (A11y)

| 영역 | 요구 |
|---|---|
| 색 대비 | 모든 본문 텍스트 4.5:1 이상 (현재 토큰 검증됨) |
| 포커스 링 | 모든 인터랙티브에 `--focus-ring` 가시화, 포커스 trap (모달) |
| 키보드 | 모든 액션이 키보드만으로 가능. 단축키와 별개로 Tab nav. |
| ARIA | grid → `role="listbox"` + `aria-activedescendant`. toast → `role="status"`/`alert`. progress → `role="progressbar"` (현재 구현됨). |
| 모션 | `prefers-reduced-motion: reduce` 시 1ms로 강제 (현재 구현됨) |
| 라벨 | 모든 input은 `<label>` 또는 `aria-label`. icon-only 버튼도 동일. |
| 색맹 | reject(빨강)와 approved(골드)는 모양/위치/텍스트로도 구분 |

---

## 9. 빈 상태 / 에러 상태 카탈로그

| 화면 | 상태 | 표시 |
|---|---|---|
| 대시보드 | 큐 0 + 에셋 0 | 온보딩 hero + curl 샘플 |
| 큐 | batch 0 | 일러스트 + curl + `/batches/new` 버튼 |
| 체리픽 | 후보 0 | "후보가 없습니다 · 배치 상세에서 task 상태를 확인하세요" |
| 갤러리 | 결과 0 | "필터에 일치하는 에셋 없음 · 필터 초기화" |
| 배치 모니터 | batch 0 | 큐와 동일 |
| 카탈로그 | 0 | "config/sd_catalog.yml 비어있음" + 경로 표시 |
| 모든 화면 | 5xx | 인라인 에러 카드 + "다시 시도" + 에러 본문 펼치기 |
| 모든 화면 | 401 (API key) | 상단 banner: "API key 없이 일부 기능 제한 · /settings" |

---

## 10. 알림 설계

| 종류 | 채널 | 예시 |
|---|---|---|
| Action ack (긍정) | success toast | "1장 승인됨 · 실행취소" |
| Action ack (위험) | info toast + undo | "5장 reject됨 · 실행취소" |
| 시스템 경고 | top banner (persistent) | "SD WebUI 연결 끊김 · 30s 후 재시도" |
| 비동기 완료 | info toast + 라우팅 링크 | "batch_marine_v2 완료 · [체리픽으로 이동]" |
| 치명 에러 | error toast (longer ttl) + 콘솔 stack | "4xx/5xx 본문" |

---

## 11. 성능 요구사항

| 지표 | 목표 |
|---|---|
| TTFI 대시보드 | ≤ 800ms (LAN) |
| 큐 화면 큐 카운트 표시 | ≤ 400ms |
| 체리픽 그리드 100장 첫 페인트 | ≤ 1.2s (썸네일 lazy) |
| j/k 키 입력 → active 변경 | ≤ 50ms |
| reject/approve API 응답 | ≤ 200ms (로컬 LAN 가정) |
| SSE 이벤트 → UI 반영 | ≤ 300ms |
| 모바일 체리픽 fps | ≥ 50fps (스크롤/스와이프) |

### 구현 가이드
- 썸네일 `loading="lazy"` + `decoding="async"`
- 큰 그리드는 가상화 (intersection observer)
- 활성 ±2 prefetch
- 이미지 캐시는 브라우저에 위임 (`Cache-Control: public, max-age=…` 서버 추가 필요)

---

## 12. 분석/계측 이벤트 (옵션)

로컬 dogfood 단계라 외부 analytics는 안 붙이지만, **콘솔 로그 + `/api/events` publish**로 다음을 추적:

- `cherrypick_session_started` / `_ended` (duration, batch_id, picked, rejected)
- `time_to_first_pick` (TopBar로 진입 후 첫 Enter까지)
- `bulk_action_used` (target, count)
- `keyboard_action` vs `mouse_action` 비율 (단축키 ROI 측정)

---

## 13. 마이그레이션 / 호환성

- 기존 `/` (index.html) → `/legacy` 로 보존, 새 `/`는 신규 대시보드. 1주 후 `/legacy` 제거.
- 기존 `/cherry-pick` 라우트는 그대로 유지하되 컴포넌트만 swap.
- API 변경 없음 (이번 spec은 프런트엔드 전용 재설계).
- `localStorage` 키 호환: `assetFactoryApiKey` 그대로 사용.

---

## 14. 기술 스택 권장 (재설계 한정)

| 영역 | 권장 |
|---|---|
| 프레임워크 | **Vite + React 18 + TypeScript** (또는 SolidJS) — 단일 SPA |
| 라우팅 | TanStack Router (타입 안전 쿼리 직렬화) |
| 데이터 | TanStack Query (캐시 + invalidation + SSE 통합) |
| 상태 | Zustand (전역) + Query (서버 상태) |
| 스타일 | CSS variables (현재 토큰 그대로) + Vanilla Extract / CSS Modules |
| 아이콘 | Lucide (트리쉐이킹) |
| 가상화 | TanStack Virtual |
| 단축키 | mousetrap or hand-rolled (현재 cherry-pick 구조 유지 가능) |
| 빌드 | FastAPI에서 `static/` mount 그대로 — Vite output을 `static/dist/` 로 |

> 만약 의존성 늘리기 싫으면, **vanilla TS + lit-html** 조합도 가능. 단 라우팅/캐싱은 직접 구현.

---

## 15. 우선순위 / 단계 설계

| Phase | 범위 | 산출물 |
|---|---|---|
| P0 (1주) | 디자인 토큰 정리, 컴포넌트 라이브러리 (Storybook), 글로벌 TopBar/SideNav | `static/dist/` 첫 빌드 |
| P1 (1주) | 신규 `/` 대시보드 + `/queue` + `/cherry-pick` 재설계 | 검수자가 매일 쓰는 핵심 path 완성 |
| P2 (1주) | `/assets` + `/assets/:id` + `/batches` + `/batches/:id` | 라이브러리/모니터 |
| P3 (3일) | `/batches/new` + `/catalog` + `/export` | manual 워크플로 |
| P4 (2일) | `/system` + `/settings` + 빈 상태/에러 폴리시 | 마무리 |
| P5 (이후) | 모바일 진입 시나리오 polish, analytics, i18n | dogfood 피드백 반영 |

---

## 16. 부록 A — 화면 ↔ API 매트릭스

| 화면 | 주요 GET | 주요 POST | SSE 의존 |
|---|---|---|---|
| `/` | `/api/cherry-pick/queue`, `/api/assets/summary`, `/api/jobs/recent`, `/api/health/sd` | — | ✓ 활동/큐 |
| `/queue` | `/api/cherry-pick/queue` | — | ✓ |
| `/cherry-pick` | `/api/batches/{id}/candidates`, `/api/cherry-pick/queue` | `…/reject`, `…/unreject`, `/api/assets/approve-from-candidate`, `…/undo-approve` | ✓ candidate_added |
| `/assets` | `/api/assets`, `/api/assets/summary` | `/api/validate/all`, `/api/batch/*` | △ |
| `/assets/:id` | `…/detail`, `…/history`, `…/candidates`, `…/image` | `…/regenerate`, `…/select-candidate`, `/api/validate/{id}`, `…/undo-approve` | △ |
| `/batches` | `/api/batches` | `/api/batch/regenerate-failed` | ✓ task_* |
| `/batches/:id` | `/api/batches/{id}/candidates` (+ tasks 엔드포인트 필요 시 추가) | `/api/batch/regenerate-failed?batch_id` | ✓ |
| `/batches/new` | `/api/sd/catalog/*`, `/api/projects` | `/api/batches` | — |
| `/catalog` | `/api/sd/catalog/models`, `/api/sd/catalog/loras` | — | — |
| `/export` | `/api/assets`, `/api/export/manifest` | `/api/export` | — |
| `/system` | `/api/health`, `/api/health/sd`, `/api/system/gc/status` | `/api/system/gc/run` | ✓ gc_* |
| `/settings` | — | (localStorage only) | — |

> 서버 측 보강이 필요한 항목 (★ 신규 API 제안)
> - `/api/batches/{id}` 단건 + spec 원문 보존 (현재 spec은 expand 후 task에만 박힘)
> - `/api/assets` 에 `q` (asset_key partial) / `category` 필터 정식 지원
> - `/api/system/disk` (data dir 사용량 별도)
> - `/api/system/logs/recent?level=error&limit=10`

---

## 17. 부록 B — 와이어프레임 ASCII (요약)

각 화면별 ASCII 스케치는 6장 본문에 inline. Figma 변환 시 다음 우선순위 파일 권장:

1. `01_dashboard.fig`
2. `02_queue.fig`
3. `03_cherrypick_desktop.fig` + `03b_cherrypick_mobile.fig`
4. `04_gallery.fig` + `05_asset_detail.fig`
5. `06_batches_monitor.fig` + `06b_batches_new.fig`
6. `07_catalog.fig` / `08_export.fig` / `09_system.fig` / `10_settings.fig`

각 .fig는 desktop 1440 + mobile 390 두 세트.

---

## 18. 결정 필요 (Decisions Needed)

다음은 의도적으로 비워둔 의사결정 — **리뷰 시 확정** 필요:

1. **SPA 전환 범위**: 전체 SPA(추천) vs 현재처럼 페이지별 HTML 유지 vs 하이브리드?
2. **라이트 모드 지원**: 현재는 dark only. 라이트 모드도 토큰 만들어둘지?
3. **i18n**: 현재 한국어 전용. 영어 토글 필요 여부?
4. **모바일 검수 지원 수준**: P1에 포함? 아니면 P5로 미룰지?
5. **로그인 / 멀티 사용자**: single-tenant 가정인데 향후 팀 사용 가능성?
6. **자동 advance default**: Enter 후 다음으로 자동 이동 ON이 기본? OFF가 기본?
7. **drag & drop 일괄 import**: 외부 PNG를 끌어다 갤러리 등록 지원?

---

**문서 끝.**
