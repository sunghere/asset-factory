# chore: track tests/test_monitoring.py + ignore runtime artifacts

> Branch: `chore/track-monitoring-test` → `main`

## 변경

1. `tests/test_monitoring.py` (4 tests) 를 추가 트래킹.
   - 본 세션의 `feat(worker): timeout-based stuck-task recovery`
     (`7b7c676`) 커밋과 짝을 이루는 테스트인데 누가 만들어 두고 git add 만
     안 한 상태로 main 에 미반영 채로 흘러가고 있었음.
   - 커버리지: `_create_or_update_incident_issue` (env 미설정 skip /
     신규 issue 생성 / 24h 안 코멘트만), `_monitoring_tick` 의 stuck +
     queue_depth+comfyui_down alert 분기.

2. `.gitignore` 보강
   - `.omc/` — OMC 오케스트레이션 state.
   - `asset_factory.db` — `run-dev.sh` 가 repo root 에 떨어뜨리는 SQLite
     runtime 파일 (`data/*.db` 는 이미 ignored 였으나 root 위치는 누락).

## 검증

```bash
.venv/bin/python -m ruff check .
  → All checks passed!

.venv/bin/python -m pytest tests/test_monitoring.py -q
  → 4 passed

.venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q
  → 433 passed
```

(pytest 가 `tests/test_*.py` glob 으로 untracked 도 collect 하던 상태라
total 카운트는 변동 없음 — git 만 모르고 있었던 케이스. 본 PR 은 그
sync 만 맞춤.)
