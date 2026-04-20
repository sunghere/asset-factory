# Asset Factory Redesign — backend follow-ups

작성: 2026-04-19 · 상태: 신규 SPA(`/app`) 포팅 완료 직후 발견한 백엔드/스펙 갭 정리.

---

## 1. 썸네일 variant 엔드포인트 부재 (HIGH)

**현상**: cherry-pick 12-grid 가 후보 원본 PNG 를 그대로 로드한다.

- 한 batch 가 60장이면 60 × 1024² PNG → 100 MB+ 다운로드.
- 모바일/원격에서 첫 페인트가 매우 느림.

**현재 어떻게 됐는지**: `static/app/js/screens/CherryPick.jsx` 의 `<Thumb>` 가
`candidate.image_url`(== `/api/asset-candidates/image?...`) 을 그대로 사용. 풀해상도.

**제안 백엔드 변경**:

- `/api/asset-candidates/image?...&size=256` 같은 쿼리 인자 지원, Pillow 로 즉석
  리사이즈 + `etag`/`Cache-Control: public, max-age=31536000, immutable`.
- 또는 정식 엔드포인트 `/api/batches/{batch_id}/candidates/{candidate_id}/thumb`.

**클라이언트 작업** (백엔드 변경 후): `<Thumb>` 가 `?size=256` 을 자동으로 붙이고,
원본은 인스펙터/확대 뷰에서만 로드.

---

## 2. cherry-pick 키 매핑 ↔ 백엔드 의미 차이 (MEDIUM)

**현상**: 스펙 v0.2 §S07 의 키맵은 `space=확정`, `u=반려 토글`, `1-5=별점` 인데
백엔드는 별점을 모르고, "확정" 은 `POST /api/assets/approve-from-candidate` 로
**1장만** 승격한다 (12장 동시 채택 X).

- 결과적으로 grid 에서 마우스/키로 여러 장을 "픽" 한 뒤 한 번에 확정하는 흐름이 없다.
- 또한 별점은 클라이언트 로컬 hint 로만 표시되며 영구 저장되지 않는다 (서버에 컬럼 없음).

**제안**:

- (a) 별점이 정말로 필요한 의사결정 기제인지 office-hours 에서 재검증. 필요 없다면
  스펙에서 빼고 키맵을 정리.
- (b) 필요하다면 `candidates.rating INTEGER NULL` 컬럼 추가 + `POST /candidates/{id}/rate`.
- (c) "여러 장을 픽 후 한 번에 확정" 흐름이 진짜로 필요하면 `POST
  /api/batches/{id}/approve-many` 를 추가하고 grid 에 multi-select state 도입.

지금 SPA 의 `space` 는 "현재 커서 위치 1장 즉시 확정 → 자동 다음 장으로 이동" 으로
구현되어 있다. 이게 가장 빠른 단일 동작이라는 가정.

---

## 3. 프로젝트 카탈로그 응답 모양 (LOW)

**현상**: `/api/projects` 응답을 Regen 화면이 `select` 로 그릴 때 항목 모양 가정이 약함.
`p.id || p.name || p` 로 필드를 더듬는다.

**제안**: 응답을 `{ items: [{ id, name }] }` 로 표준화. 그러면 클라이언트가 추측하지
않아도 됨.

---

## 4. 모니터/SSE 이벤트 타입 명세 부재 (LOW)

**현상**: `/api/events` 가 던지는 이벤트 종류가 코드 안에 점점이 흩어져 있다
(`candidate_rejected`, `asset_approve_undone`, `design_batch_created`, ...).
프론트는 색을 입히기 위해 `KIND_COLOR` 맵을 하드코딩한다.

**제안**:

- `lib/events.py` 같은 단일 모듈에 `EventType = Literal[...]` 와 페이로드 모델을 모은다.
- README 또는 `docs/EVENTS.md` 한 페이지에 표로 정리.
- 클라이언트는 알 수 없는 타입은 회색으로 폴백 (이미 그렇게 함).

---

## 5. 정적 자산 캐시 헤더 (LOW)

**현상**: `/static/app/js/*.jsx` 파일은 `<script type="text/babel">` 로 매번 fetch +
브라우저-side 트랜스파일 (~150–250 ms). 캐시 헤더가 강하지 않으면 매 새로고침마다
재다운로드한다.

**제안**: `StaticFiles` 마운트에 `Cache-Control: public, max-age=600` 정도. 또는
프로덕션 빌드로 전환할 때 hash 기반 파일명 + `immutable`.

---

## 6. legacy `/` 와 `/app` 의 운명 (DONE · 2026-04-20)

**2단계 완료 — 레거시 UI 전면 제거.**

현재 상태:

- `/`                          → 302 → `/app/`
- `/cherry-pick`               → 302 → `/app/queue`
- `/cherry-pick?batch_id=XYZ`  → 302 → `/app/cherry-pick/XYZ`
- `/legacy*`                   → **제거됨** (404)

삭제된 파일:

- `static/index.html` (구 대시보드)
- `static/cherry-pick.html` (구 체리픽)
- `static/app.js` (구 프론트 JS)
- `static/style.css` (구 CSS 토큰)

`server.py` 는 `RedirectResponse` · `FileResponse` 둘 다 `/app` 진입점에서 계속 쓰므로
import 유지. 회귀 필요 시 git 히스토리에서 복원 (`git log -- static/index.html`).

---

## 7. BatchDetail 실데이터 검증 (2026-04-20)

**현상**: 신규 SPA 의 11화면 sanity 를 실배치로 검증 (프로젝트: `sanity-test`,
배치: `btc_29526a9475d5…`, 2 tasks, 1 candidate, validation=fail).

확인된 것:

- `/app/batches` 리스트 → 배치 1건 정상 노출 (project/asset_key/done/total/cand).
- `/app/batches/{id}` deep-link → SPA 셸 serve (200).
- `/api/batches?limit=500` + `/api/batches/{id}/candidates` 모두 200.
- `/api/asset-candidates/image?...` 실제 PNG 로드 (5.3KB, 64×64).
- reject/unreject round-trip 200.

고쳐진 버그:

- `BatchDetail.jsx SpecView` 가 candidate row 의 *상상 필드* (`seed`/`model`/`prompt`) 를
  찾고 있었음. 실제 컬럼은 `generation_seed`/`generation_model`/`generation_prompt`
  이고 `cfg`/`steps`/`sampler`/`negative_prompt` 등은 `metadata_json` 안에 있음.
  SpecView 를 실제 스키마에 맞게 재매핑 (+ validation_status/message 노출).

§7a & §7b **DONE · 2026-04-20**:

- **§7a validation 뱃지**: `Thumb` 에 `warn` prop 추가 + `.bdg.warn` CSS.
  `CherryPick.jsx` (후보 그리드), `Assets.jsx` (`AssetCard` vbdg title), `BatchDetail.jsx`
  (후보 그리드 우상단 `!`) 세 군데에서 `validation_status='fail'` 시 빨간 뱃지 +
  `validation_message` hover tooltip. Batches 리스트 집계 뱃지는 성능/DB 쿼리 비용
  때문에 보류.
- **§7b batch spec 집계**: `GET /api/batches/{batch_id}` 신규 (`Database.get_batch_detail`).
  응답에 `project`/`asset_key`/`category`/`job_id`/시간, `tasks`(total/done/failed/active),
  `candidates`(total/rejected/picked/validation), `spec`(seeds/models/prompts/
  negative_prompts/loras distinct + common{steps,cfg,sampler,width,height,
  expected_size,max_colors,max_retries} 각각 `{value, uniform}` + `task_count`).
  `BatchDetail.jsx SpecView` 를 이 페이로드 기반으로 재작성: **meta / axes / common
  params / candidates** 4블록. axes 에서 배치 전체의 곱집합이 그대로 보이고,
  common 은 비균일할 때 `mixed` 뱃지.

---

## 8. 로컬 테스트 데이터 시드 (DONE · 2026-04-20)

**현상**: 기존 `sanity-test` 배치 1건만으로는 11화면 UI 를 제대로 dogfood 하기
어렵다 (Queue 가 비어있고, SpecView 의 axes 표시도 값이 1개씩이라 단조로움).

**해결**: `scripts/seed_cat_raising.py` 추가. 멱등하게 아래 3개 배치를
DB + 디스크(기존 13 장 PNG 재사용 + 부족한 슬롯은 64×64 단색 PNG 생성)에 꽂는다.

| batch_id | asset_key | category | tasks | 목적 |
|---|---|---|---|---|
| `btc_seed_cr_baby_idle` | `ksh_baby_idle` | character | 4 done | validation 뱃지 mix(pass/pass/fail/pending) |
| `btc_seed_cr_baby_eat` | `ksh_baby_eat` | character | 4 done (2 seeds × 2 models) | SpecView axes 다축 확인 |
| `btc_seed_cr_btn_feed` | `btn_feed` | ui | 1 done + 1 queued | Queue/Batches active 카운트 채우기 |

queued 태스크는 `next_attempt_at = 2099-01-01` 로 박아서 worker 가 claim 하지
않도록 한다 (실 SD 호출 방지). 재실행 시 이 3개 `batch_id` 를 지우고 다시 심는다.

실행:
```bash
.venv/bin/python scripts/seed_cat_raising.py
```

기존 `ksh_baby_idle` 의 legacy 태스크/asset 행 (batch_id=NULL) 은 건드리지 않는다.
