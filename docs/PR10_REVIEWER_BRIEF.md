# PR #10 — 리뷰어 전달 브리프

## PR / Git

| 항목 | 값 |
|------|-----|
| **PR** | https://github.com/sunghere/asset-factory/pull/10 |
| **제목(요약)** | `feat(spa): v0.2 follow-ups` — #9(`feat/spa-redesign-v0.2`) 위에 스택된 후속 PR |
| **베이스 브랜치** | `feat/spa-redesign-v0.2` |
| **헤드 브랜치** | `feat/spa-redesign-v0.2-followups` |
| **리모트** | `https://github.com/sunghere/asset-factory.git` |

## 로컬 체크아웃(리뷰어 워크스페이스 예시)

아래는 **이번 작업이 수행된 클론의 절대 경로**이다. 리뷰어 본인 환경에서는 동일 구조로 맞추면 된다.

| 설명 | 절대 경로 |
|------|-----------|
| **저장소 루트** | `/Users/yoons/workspace/asset-factory` |
| **백엔드 엔트리** | `/Users/yoons/workspace/asset-factory/server.py` |
| **DB/모델** | `/Users/yoons/workspace/asset-factory/models.py` |
| **이벤트 상수·SSE** | `/Users/yoons/workspace/asset-factory/lib/events.py` |
| **SPA 부트스트랩** | `/Users/yoons/workspace/asset-factory/static/app/index.html` |
| **스크립트 매니페스트** | `/Users/yoons/workspace/asset-factory/static/app/manifest.json` |
| **공유 React 유틸** | `/Users/yoons/workspace/asset-factory/static/app/js/hooks.jsx` |
| **API 클라이언트** | `/Users/yoons/workspace/asset-factory/static/app/js/api.jsx` |
| **라우터·쉘** | `/Users/yoons/workspace/asset-factory/static/app/js/app.jsx`, `chrome.jsx`, `router.jsx` |
| **화면 컴포넌트 디렉터리** | `/Users/yoons/workspace/asset-factory/static/app/js/screens/` |
| **테스트** | `/Users/yoons/workspace/asset-factory/tests/` |
| **시드 스크립트** | `/Users/yoons/workspace/asset-factory/scripts/seed_cat_raising.py` |
| **제품 스펙(기획 단일 소스)** | `/Users/yoons/workspace/asset-factory/docs/SCREEN_SPEC_v0.2.md` |
| **SSE 이벤트 표** | `/Users/yoons/workspace/asset-factory/docs/EVENTS.md` |
| **세션 핸드오프 / 갭 재분류** | `/Users/yoons/workspace/asset-factory/NEXT.md` |
| **수동 스모크 체크리스트** | `/Users/yoons/workspace/asset-factory/docs/BROWSER_SMOKE_CHECKLIST.md` |

```bash
cd /Users/yoons/workspace/asset-factory
git fetch origin
git checkout feat/spa-redesign-v0.2-followups
git pull origin feat/spa-redesign-v0.2-followups
```

## PR 범위(한 줄)

v0.2 SPA(`/app/*`) 후속: **CodeQL 대응 리다이렉트**, **배치 태스크·시스템·카탈로그 등 백엔드 계약**, **CherryPick/대시보드/큐/에셋/배치/카탈로그/익스포트 등 화면 구현**, **SSE·배너·API 키·manifest 부트스트랩**, 그리고 **검수 후속**으로 SSE 배치 처리·테스트·문서 보강.

## 검수 후속(최상단 커밋 근처)

- **커밋 예:** `07e011d` — `fix: post-audit SSE, tests, and handoff docs`
- **내용:** `AssetDetail`의 `useSSE`가 `hooks.jsx`와 동일하게 **이벤트 배열 배치**를 처리; `Assets`/`Export`의 SSE 무효화; `GET /`→`/app/`·`retry-failed`+`API_KEY` 테스트; `NEXT.md` 재분류; `docs/BROWSER_SMOKE_CHECKLIST.md` 추가.

## 리뷰 시 권장 포커스

1. **보안** — [`/Users/yoons/workspace/asset-factory/server.py`](/Users/yoons/workspace/asset-factory/server.py) 내 `cherry_pick_redirect`, [`/Users/yoons/workspace/asset-factory/tests/test_redirect.py`](/Users/yoons/workspace/asset-factory/tests/test_redirect.py)
2. **API 계약** — `tasks` / `retry-failed` / `system/*` / projects envelope; [`tests/test_api_tasks.py`](/Users/yoons/workspace/asset-factory/tests/test_api_tasks.py), [`tests/test_api_system.py`](/Users/yoons/workspace/asset-factory/tests/test_api_system.py)
3. **SSE** — [`lib/events.py`](/Users/yoons/workspace/asset-factory/lib/events.py) ↔ [`docs/EVENTS.md`](/Users/yoons/workspace/asset-factory/docs/EVENTS.md) ↔ 각 화면의 `useSSE` 사용처(`grep useSSE /Users/yoons/workspace/asset-factory/static/app/js/screens`)
4. **스펙 정합(샘플)** — [`docs/SCREEN_SPEC_v0.2.md`](/Users/yoons/workspace/asset-factory/docs/SCREEN_SPEC_v0.2.md) §6·§7 vs [`CherryPick.jsx`](/Users/yoons/workspace/asset-factory/static/app/js/screens/CherryPick.jsx)

## 검증 커맨드(로컬)

저장소 루트에서:

```bash
cd /Users/yoons/workspace/asset-factory
PYTHONPATH=. uv run pytest tests/ -q
uv run ruff check .
```

(선택) UI 스모크: [`docs/BROWSER_SMOKE_CHECKLIST.md`](/Users/yoons/workspace/asset-factory/docs/BROWSER_SMOKE_CHECKLIST.md) — 시드는 `python /Users/yoons/workspace/asset-factory/scripts/seed_cat_raising.py` (프로젝트 venv 기준으로 경로 조정).

## PR 본문에 없을 수 있는 메모

- **GitHub PR 본문**에 Phase 0–D·테스트 플랜이 이미 상세히 있음 — 위 링크의 **Conversation / Files changed**와 함께 보면 된다.
- **의도적으로 PR에 포함하지 않은 로컬 파일**(리뷰 범위 아님): 예) `Asset Factory-handoff.zip`, 중첩 `asset-factory/`, 일부 untracked `docs/*.md` 등 — 트리에 나타나면 무시해도 된다.

## 질문/응답 연락

PR #10 **Review** 스레드 또는 **Conversation**에 코멘트 남겨 주면 된다.
