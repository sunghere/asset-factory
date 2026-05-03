# AGENTS.md — 에이전트 운영 규칙

> AI 에이전트가 본 프로젝트에서 작업할 때 반복적으로 하는 실수를 줄이기 위한
> 운영 매뉴얼. 도메인/코드 규칙은 `CLAUDE.md`, 인프라 제약은 `HANDOFF.md`,
> 설계는 `DESIGN.md` 를 참조.
>
> 본 문서의 항목은 모두 **이 프로젝트에서 실제로 일어난 사고** 의 회고에서
> 도출됐다 — 쓸데없이 일반론을 넣지 말 것. 새 사고가 나면 추가, 1년 이상
> 재발 없으면 정리.

---

## 0. 시작 시 항상

새 세션 / 새 작업 진입 시 1회:

1. `NEXT.md` 가 있으면 읽고 직전 세션 상태 파악 (CLAUDE.md §세션 핸드오프).
2. `git status` + `git log --oneline -5` 로 working tree 와 최근 커밋 확인.
3. 미커밋 변경이 있으면 출처/의도 확인 후 처리 (별도 커밋, stash, 새 브랜치
   등) — 본인의 새 작업과 섞지 말 것.

---

## 1. 로컬 검증 — 절대 CI 가 1차 검출자가 되지 말 것

**사고 회고**: PR #48 머지 직후 main 의 ruff 가 빨간 상태로 도착. 로컬에서
`ruff check .` 한 번도 안 돌렸음 → CI 가 1차 검출자가 됨 → 후속 hotfix
PR (#49) 필요.

### 푸시 전 체크리스트

```bash
.venv/bin/python -m ruff check .                              # ruff
.venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q
```

- ruff 가 빨간 상태로 push 하지 말 것 (CI 의 Lint+Test job 이 즉시 실패).
- pytest 433+ 통과 baseline 유지 (현재 main 기준 438). `tests/test_generator_comfyui.py` 는
  `aioresponses` 미설치 환경 이슈 — `--ignore` 로 우회.
- `.venv/bin/python` 로 호출 (시스템 python 의 라이브러리 mismatch 방지).

### 새 코드 작성 후 자가 점검

- 미사용 import / 변수 — F401, F841 가 ruff 의 단골. 작성 후 한 번 더 훑기.
- 예외 처리 빈 `except: pass` 는 CodeQL 이 잡음. 의도가 있으면 한 줄 코멘트
  (`# expected on shutdown` 같은 식).
- 사용자 입력 (URL path / 외부 source) → log 로 흘릴 때 CRLF strip
  (`s.replace('\r', '').replace('\n', '')`) 안 하면 CodeQL log injection 경고.

---

## 2. PR 워크플로우 — 머지 전에 CI 와 리뷰 둘 다 확인

**사고 회고**: PR #48 을 `gh pr merge --merge --delete-branch` 만 던지고
결과를 보지 않았음. CI Lint+Test 2개 FAILURE + GitHub Advanced Security 의
CodeQL 인라인 리뷰 4건이 달려 있었는데 모두 무시됨. main 이 빨개졌고
사용자가 직접 지적해서야 알아차림.

### 머지 직전 필수 체크

```bash
# 모든 체크가 COMPLETED 될 때까지 대기 — 폴링 루프
until [[ $(gh pr view N --json statusCheckRollup --jq '[.statusCheckRollup[] | select(.status == "COMPLETED")] | length') -ge 4 ]]; do sleep 15; done

# 결과 확인
gh pr view N --json statusCheckRollup --jq '.statusCheckRollup[] | "\(.name)|\(.conclusion)"'

# 인라인 리뷰 댓글 확인 (github-advanced-security 등)
gh api repos/{owner}/{repo}/pulls/N/comments --jq '.[] | {path, line, body: (.body | .[0:200])}'
```

조건 모두 만족해야 머지:
- 모든 status check `SUCCESS`. `FAILURE` 1건이라도 있으면 fix push 후 재대기.
- 인라인 리뷰 댓글이 모두 stale (`line: null`) 이거나 신규 댓글 0건.
- 신규 코멘트가 있으면 본문 검토 후 대응.

### Transient flake 대응

알려진 flake (예: `test_system_db_reports_tables_and_queue` 의 lifespan
worker timing race) 가 단독으로 빨갛게 뜨면 `gh run rerun {run_id} --failed`
로 재시도. 한 번 더 빨개지면 real regression 으로 간주.

### 머지 모드

| 상황 | 모드 | 이유 |
|--|--|--|
| 8 commit 내외의 의미 단위가 분리된 큰 PR | `--merge` (merge commit) | 원자 커밋 보존, 후속 git blame 친화 |
| 1~2 commit 의 작은 fix/chore | `--squash` | 메인 히스토리 정돈 (기존 GGU- 시리즈 패턴) |

`--delete-branch` 항상 같이 — 원격 브랜치 잔여 정리.

---

## 3. PR body 컨벤션 — `docs/pr/*.md`

PR description 은 항상 `docs/pr/{branch-slug}.md` 파일로 먼저 작성하고
`gh pr create --body-file` 로 첨부. 이유:

- 머지 후 `git log` 에 PR body 가 squash 되면서 사라져도 파일이 남음.
- 작성 중 큰 본문을 ephemeral terminal arg 로 다루면 줄바꿈/이스케이프 사고.
- 사용자 / 차후 에이전트가 README / NEXT.md 에서 링크해 원인-결과 추적 가능.

본문 구조 (회고 가능한 형태):
1. **증상** (사용자 리포트 / 관측 사실).
2. **원인** (root cause, 코드 위치 라인 인용).
3. **수정** (코드 + 회귀 가드 테스트).
4. **검증** (ruff/pytest 결과 numeric).
5. **scope 밖 / 후속**.

---

## 4. 브랜치 / 커밋 컨벤션

### 브랜치 prefix

- `feat/...` — 사용자 가시 기능 추가
- `fix/...` — 버그 수정
- `chore/...` — 인프라, 청소, 비기능 변경
- `docs/...` — 문서 단독

### 커밋 메시지

- 첫 줄: `prefix(scope): 한국어 요약` (Conventional Commits, scope 권장)
- 본문: 한국어. **증상 / 원인 / 수정 / 검증** 흐름. 라인 reference (`server.py:NNN`)
  적극 사용.
- 큰 변경은 의미 단위 다수 commit 으로 분할 (PR 머지 모드 `--merge` 와 짝).

---

## 5. 워크트리로 후속 작업 격리

후속 / 분리 가능 작업은 `git worktree add ../{repo}-{slug} -b {branch} main`
로 별도 디렉토리에서 진행. 이유:

- main 디렉토리에서 dev 서버가 떠 있어도 새 작업이 파일을 동시 mutate 하지
  않음.
- 두 개 이상의 PR 를 병렬 진행하기 쉬움.
- 머지 후 `git worktree remove ... --force` + `git branch -D ...` 로 정리.

`.venv` 는 main 디렉토리 것을 그대로 사용 (`/Users/.../asset-factory/.venv/bin/python`).

---

## 6. Frontend ↔ Backend 계약 mismatch — 즉시 잡을 것

**사고 회고**: cherry-pick 화면이 같은 후보를 무한 재반려하던 버그. backend
는 `is_rejected: 0|1` + `picked_at` 만 있고 `status` 컬럼이 없는데, frontend
가 `c.status === 'rejected'` 로 모든 분기를 함. 첫 reject 직후 SSE reload 가
들어오면 로컬 status 가 사라져 가드/필터가 모두 무력화.

### 회피 패턴

- API 응답 dict 에 frontend 가 사용하는 키를 **서버에서 derive 해서 노출**.
  단일 source of truth.
- frontend 가 새 키를 도입하기 전에 backend response shape 를 grep 으로 확인.
- 회귀 가드 테스트로 응답에 키 존재 여부를 hard assert (예:
  `test_list_batch_candidates_derives_status_field`).

---

## 7. 컴포넌트 추가 시 import scope 확인

**사고 회고**: `CherryPick.jsx` 가 hook 충돌 회피 위해 line 23 에서
`useState: useStateCP` 식 alias 를 씀. 새로 추가한 `BatchOps` 컴포넌트가
raw `useState` 를 호출 → ReferenceError → 화면 크래시.

### 회피 패턴

- 새 컴포넌트를 **기존 파일에 추가** 할 때, 그 파일의 import / hook alias
  패턴을 먼저 확인 (`grep "useState\|useEffect\|useMemo" path.jsx`).
- 다른 화면에서 raw `useState` 를 쓴다고 같은 패턴을 가져오지 말 것 — 그
  파일의 컨벤션이 우선.
- JSX 에서 일관된 hook 이름 alias 를 쓰는 파일은 line 1~30 의 destructure
  블록만 보면 알 수 있음.

---

## 8. GC / cleanup 패턴 — file unlink 와 DB row 정리는 항상 짝으로

**사고 회고**: `candidate_gc.py` 의 step 1 (rejected 처리) 가 step 2 (bypass)
와 달리 `_delete_candidate_rows()` 호출이 빠져 있었음. reject + GC 사이클을
돌릴 때마다 dangling row 누적 → 운영 DB 에서 31% (46/145) 가 orphan.

### 규칙

후보 / 자산 / 산출물 디렉토리에서 파일을 unlink 하는 모든 경로는 같은
트랜잭션 안 (또는 직후) 에서 그 파일을 가리키는 DB row 도 정리해야 한다.
짝이 깨지면 frontend 가 broken-image 를 영구 표시.

순서: **DB row DELETE → file unlink** (또는 그 반대) 중 정합성 측면에서
"DB 가 진실, 파일은 best-effort" 가 본 프로젝트의 기본:

```python
# 1) DB row 먼저 정리 (트랜잭션 commit)
deleted = await db.delete_candidates_by_ids([cid])
# 2) file 은 best-effort unlink, 실패해도 row 는 사라진 상태
try:
    Path(path_str).unlink()
except OSError:
    logger.warning(...)  # 로그만, 응답 본문에 path 흘리지 말 것
```

---

## 9. 백엔드 / 프론트엔드 환경 메모

빠른 reference (자세한 건 `HANDOFF.md`).

| 항목 | 값 |
|--|--|
| 본 서버 | `./run-dev.sh start` (포트 8000, .env 의 ComfyUI 호스트 사용) |
| 모킹 UI | `./run-mock-ui.sh` (UI-only 데모 환경) |
| Python | `.venv/bin/python` (3.11 로컬 / CI 는 3.11+3.12 동시 매트릭스) |
| Lint | `ruff check .` |
| Test | `pytest --ignore=tests/test_generator_comfyui.py -q` |
| 1차 백엔드 | ComfyUI (`192.168.50.225:8188`) — `CLAUDE.md` §백엔드 데이터 소스 |
| Deprecated | A1111 backend (v0.4.0 메이저에서 제거) |

---

## 부록 A — 신규 사고 추가 템플릿

새 사고가 났을 때 본 문서에 추가:

```markdown
## N. {제목 — 1줄}

**사고 회고**: {언제 / PR 번호 / 증상 1~2 문장}.

### 회피 패턴

- {action 1}
- {action 2}

### 회귀 가드 (있다면)

- {test 파일 / commit 해시}
```

기존 항목과 겹치는 회피 패턴은 합치고, 1년 이상 재발 없으면 항목 자체를
삭제 — 본 문서가 길어지면 신호 / 잡음 비가 떨어진다.
