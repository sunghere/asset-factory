# CI Protection Setup — `main` branch protection rule

> 1회 설정 가이드. 빨간 CI 가 main 으로 직접 머지되는 사고 (PR #48) 의 영구
> 가드. 본 repo 의 admin 권한 보유자가 한 번만 누르면 됨.
>
> AGENTS.md §3 (PR 워크플로우) 의 인프라 보강.

## 무엇을 막는가

- 빨간 CI 상태에서 머지 금지 (`Lint + Test (Python 3.11)`, `Lint + Test
  (Python 3.12)` 가 SUCCESS 여야만 머지 버튼 활성).
- main 으로 직접 push 금지 (PR 거치도록 강제).
- force-push 금지 (히스토리 변조 방지).
- 머지된 브랜치 자동 삭제 (이미 `gh pr merge --delete-branch` 패턴이지만
  안전 그물).

CodeQL / Analyze 는 advisory 라 required 로 묶지 않음 (인라인 댓글로 충분).

## 옵션 A — GitHub UI (권장, 가장 명확)

1. https://github.com/sunghere/asset-factory/settings/branches 접속.
2. **Add branch protection rule** → Branch name pattern: `main`.
3. 다음 항목 체크:
   - ☑ **Require a pull request before merging**
     - ☑ Require approvals: `0` (솔로 운영) 또는 `1` (팀)
     - ☑ Dismiss stale pull request approvals when new commits are pushed
   - ☑ **Require status checks to pass before merging**
     - ☑ Require branches to be up to date before merging
     - 다음 status check 검색·추가:
       - `Lint + Test (Python 3.11)`
       - `Lint + Test (Python 3.12)`
   - ☑ **Require conversation resolution before merging** (CodeQL 인라인
     댓글이 unresolved 상태로 머지되지 않도록)
   - ☑ **Do not allow bypassing the above settings**
   - ☐ Require linear history (선택 — `--merge` 머지 모드 쓰는 본 repo
     관행과 충돌해서 OFF 권장)
4. **Create** 클릭.

## 옵션 B — `gh api` (자동화)

CLI 한 줄로 같은 설정. admin 토큰이 필요 (`gh auth refresh -s admin:repo_hook`).

```bash
gh api -X PUT repos/sunghere/asset-factory/branches/main/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.contexts[]=Lint + Test (Python 3.11)' \
  -F 'required_status_checks.contexts[]=Lint + Test (Python 3.12)' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.required_approving_review_count=0 \
  -F required_pull_request_reviews.dismiss_stale_reviews=true \
  -F restrictions= \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F required_conversation_resolution=true
```

`enforce_admins=true` 는 admin 본인 (예: 솔로 운영자) 도 우회 못하게 — 사고
재발 방지가 본 가드의 목적이라 본인이 기억 못 하는 것까지 막아야 의미.

## 검증

설정 후 임시 빨간 PR 을 만들어 머지 시도 → "Required status check has not
been completed" 로 머지 버튼 비활성이면 정상.

```bash
# 빨간 PR 만들기 (test 의존성 일부러 깨기) → 머지 시도 → 차단 확인 → revert.
echo "import nonexistent_module  # noqa" >> /tmp/red.py
git checkout -b chore/test-protection
mv /tmp/red.py tests/_test_red.py
git add tests/_test_red.py && git commit -m "test: trigger CI red"
gh pr create --title "WIP red" --body "test"
# CI 가 빨갛게 뜬 후
gh pr merge {N} --merge --delete-branch
# → "Resource protected by organization SAML enforcement" 또는 "required check"
# 메시지로 차단되면 OK.
git push origin :chore/test-protection  # 정리
```

## 우회가 정말 필요할 때

- 핫픽스 / 보안 패치: `enforce_admins=false` 로 임시 토글 (1회 우회 후 즉시
  되돌리기). 변경 자체를 PR 로 추적하는 편이 안전.
- 인프라 장애 (CI 자체가 다운): GitHub Actions 가 응답 안 하면 본 가드도
  의미 무효 — 장애 끝날 때까지 기다리거나 protection 일시 해제.

## AGENTS.md §3 와의 관계

`AGENTS.md §3` 은 *에이전트 / 사용자 행동* 측면의 가드 (`gh pr view` 폴링).
본 문서는 *GitHub 측 인프라* 측면의 가드. 두 층은 보완 관계 — 행동은 누락 가능
하지만 인프라는 영구. 둘 다 켜야 의미.
