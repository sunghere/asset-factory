# chore: post-session hardening — pre-commit hook + CI protection 가이드 + flake fix

> Branch: `chore/post-session-hardening` → `main`
> AGENTS.md §2/§3 의 인프라 보강 + 알려진 flake 영구 제거.

## 동기

이번 세션 PR #48~#54 회고에서 도출된 후속 인프라 항목 3개를 한 PR 로 묶음.
모두 본 repo 의 운영 흔들림을 줄이는 1회 설치 / 영구 가드.

## 변경 (3 commit)

### 1. `fix(test): test_system_db_reports race — disable claim_next_task in lifespan`

- 증상: `test_system_db_reports_tables_and_queue` 가 Python 3.12 + GitHub
  runner 타이밍에서 종종 빨갛게 뜸. 어쌔션 `queued_total + processing == 2`
  가 (2+1=3) 로 깨짐. PR #53 1차 CI 실패의 원인 — 재실행으로 우회했지만
  근본 해결 안 됨.
- 원인: `TestClient(server.app)` 의 lifespan 이 실제 `generation_worker` 를
  띄우고 그 워커가 seeded queued task 를 race window 에서 claim → DB count
  가 의도치 않게 변동.
- 수정: 본 테스트 한정으로 `monkeypatch.setattr(db, "claim_next_task",
  lambda: None)`. 워커는 idle 로만 돌아 DB 가 seed 그대로 유지. 다른
  테스트 (`test_system_worker_contract` 가 워커 heartbeat 의존) 는 영향
  없음 — fixture 가 아닌 함수 단위 monkeypatch 라 격리됨.

### 2. `chore(scripts): pre-commit hook — ruff 자동 실행`

PR #48 시 로컬 ruff 미실행으로 CI 가 1차 검출자가 된 사고 (#49 hotfix 야기)
의 영구 가드.

- `scripts/git-hooks/pre-commit` (트래킹) — staged `.py` 가 있을 때만
  `.venv/bin/ruff check .` 실행. 비파이썬 변경 (jsx/yaml/md) 은 빠르게 통과.
- `scripts/install-hooks.sh` — `.git/hooks/` 에 symlink 설치 (worktree 도
  정확한 hooks 디렉토리로 자동 감지: `git rev-parse --git-path hooks`).
  `--uninstall` 지원. 기존 일반 파일 hook 이 있으면 보존.
- AGENTS.md §2 갱신 — 설치 1회 안내.

설치 시연:
```
$ ./scripts/install-hooks.sh
[install-hooks] linked pre-commit → .../scripts/git-hooks/pre-commit
[install-hooks] 설치 완료. 임시 우회: git commit --no-verify.
```

### 3. `docs(ci): main branch protection setup`

`docs/CI-protection-setup.md` 신규 — GitHub UI 옵션 A + `gh api` 옵션 B.

- 빨간 CI (Lint+Test 3.11/3.12) 머지 차단
- main 직접 push 차단
- force-push 차단
- `enforce_admins=true` 로 admin 본인 우회도 차단 (사고 재발 방지가
  목적이라 본인 기억 못 하는 것까지 막아야 의미)
- 검증 절차 (빨간 PR 만들어 차단 확인)
- 우회가 정말 필요한 케이스 안내

AGENTS.md §3 갱신 — 행동 가드 (gh 폴링) 와 인프라 가드 (branch protection)
의 보완 관계 명시.

## scope 밖

- **`sincerity-skills/asset-factory-api/SKILL.md` 갱신** — 별도 repo (다른
  GitHub origin) 라 본 PR 에 들어갈 수 없음. 본 PR 머지 후 그 repo 에
  별도 PR.

## 검증

```
.venv/bin/python -m ruff check .                          → All checks passed!
.venv/bin/python -m pytest tests/test_api_system.py -q    → 4 passed (flake 재현 안 됨)
.venv/bin/python -m pytest -q                              → 438 passed
./scripts/install-hooks.sh                                → symlink 설치 확인
```
