# CLAUDE.md — Asset Factory

프로젝트 에이전트/작업자용 운영 규칙. 프로젝트 개요·기술스택은 `.cursorrules`,
인프라·제약은 `HANDOFF.md`, 설계는 `DESIGN.md` 를 참조.

> **에이전트 시작 시 — `AGENTS.md` 를 먼저 읽고 따를 것.**
> [`AGENTS.md`](./AGENTS.md) 는 본 프로젝트에서 실제로 발생한 사고의 회고로
> 도출된 운영 규칙 (로컬 ruff/pytest 선검증, PR 머지 전 CI/리뷰 확인,
> `docs/pr/*.md` 본문 컨벤션, frontend↔backend 계약 mismatch, GC 의 file
> ↔ DB row 짝 정합성 등) 을 담는다. 본 문서 (CLAUDE.md) 는 도메인/스크린/
> 데이터 소스 규칙, `AGENTS.md` 는 개발 흐름 자체의 규칙.

---

## 세션 핸드오프 (`NEXT.md`)

다음 작업자를 위한 인수인계는 프로젝트 루트의 [`NEXT.md`](./NEXT.md) 한 파일로
관리한다. Transient 문서 — git 히스토리에는 남지만 현재 시점에는 "지금 픽업해야
할 상태" 만 담는다.

### 구조

```
# NEXT — 세션 핸드오프

## 최근작업          ← 항상 작성
...

## 핸드오프          ← 인계할 내용이 있을 때만 작성 (없으면 섹션 생략)
...
```

- **최근작업**: 이번 세션에 완료한 작업 요약. 파일/라인/API 포인터 위주로 짧게.
  상세는 커밋 메시지 · PR · `docs/*-followups.md` 에 위임.
- **핸드오프**: 다음 세션에서 픽업할 in-flight 작업, 미결 의사결정, 블로커.
  **내용이 없으면 섹션 자체를 넣지 않는다** (빈 "(없음)" 문구도 지양).

### 라이프사이클

1. **세션 종료 직전** — `NEXT.md` 를 이번 세션 기준으로 갱신. 최근작업은 매번,
   핸드오프는 필요할 때만.
2. **다음 세션 시작 시** — `NEXT.md` 를 먼저 읽고 이전 세션 상태 파악.
3. **핸드오프 항목 완료** — 해당 항목 제거. 핸드오프 섹션이 비면 섹션을 삭제.
4. **세션 마무리 + 핸드오프 없음** — `NEXT.md` 파일 자체를 삭제.
   (기록은 git log / 커밋 메시지에 남아있으므로 중복 보관하지 않는다.)

### 주의

- 루트의 `HANDOFF.md` 는 프로젝트 인프라 (SD 서버 주소, Aseprite 경로, 우선순위
  등) 를 담는 **상시 문서** 다. `NEXT.md` 와 혼동하지 말 것.
- `NEXT.md` 가 여러 세션 내용이 누적되어 길어지면 잘못 쓰는 중이다. 오래된 "최근
  작업" 은 지우거나 followups 문서로 옮긴다.

---

## 그 외 작업 규칙

프로젝트 기본 규칙 · 코딩 스타일 · 구현 순서는 [`.cursorrules`](./.cursorrules)
참조. 설계 문서 목록:

- `DESIGN.md` — 전체 설계 (스키마, API, UI 와이어프레임)
- `HANDOFF.md` — 인프라, 우선순위, 제약사항 (상시)
- `REVIEW.md` — 설계 리뷰 보완사항
- `docs/SCREEN_SPEC_v0.2.md` — 11화면 기획 스펙
- `docs/asset-factory-redesign-followups.md` — 리디자인 후속 이슈 추적

## 백엔드 데이터 소스 (Catalog / System)

`/app/catalog`, `/app/system` 화면은 **ComfyUI** (`192.168.50.225:8188`) 를
1차 데이터 소스로 사용한다. A1111 백엔드는 deprecated — 코드는 호환성 위해
유지 중이지만 다음 메이저(v0.4.0)에서 제거 예정. PLAN_comfyui_catalog.md 참조.

- Catalog 화면: `api.comfyuiCatalog()` 1회 호출 + `api.catalogUsage()` (DB 통계).
- System / Dashboard / 글로벌 배너: `api.comfyuiHealth()` 의 `ok` 필드만 평가.
  A1111 다운 단독으로는 배너/경고 발생 안 됨.
