# fix: cherry-pick crash + PR #48 CI/리뷰 후속 정리

> Branch: `fix/cherrypick-usestate-hotfix` → `main`
> 후속: PR #48 (`feat: ComfyUI workflow request paths`)

## 컨텍스트 / 사과

PR #48 을 머지하면서 CI 결과와 리뷰를 확인하지 않고 `gh pr merge` 를 던진 게
잘못이었다. 머지 직후 main 이 다음 두 갈래로 빨개진 상태:

1. **사용자가 본 런타임 크래시** — `/app/cherry-pick/btc_*` 진입 시 화면이
   "useState is not defined" 로 ErrorBoundary 까지 올라감.
2. **CI Lint + Test (Python 3.11/3.12) FAILURE** — ruff 4건.
3. **CodeQL inline 4건** (advisory, CI 는 통과지만 리뷰 댓글로 달려 있었음).

본 hotfix 가 이 모두를 한 번에 정리한다.

## 1. CherryPick 화면 크래시 (런타임)

### 증상
`/app/cherry-pick/btc_*` 진입 시:
```
useState is not defined
```
ErrorBoundary 가 잡아 "화면이 크래시했습니다" 로 표시. cherry-pick 큐 자체
동작 불가 — manual / batch 결과 검토 흐름 전부 차단.

### 원인
`static/app/js/screens/CherryPick.jsx` 는 line 23 에서 hook 충돌 회피를 위해
suffix alias 를 씁니다:
```js
const { useMemo: useMemoCP, useState: useStateCP, useEffect: useEffectCP, ... } = React;
```
→ 이 파일에서 raw `useState` 는 미정의.

PR #48 에서 추가한 `BatchOps` 컴포넌트가 다른 화면의 패턴 (raw `useState`) 을
그대로 가져다 써서 ReferenceError. BatchOps 가 mount 되는 순간 컴포넌트
트리 전체 unmount.

### 수정
```diff
- const [confirm, setConfirm] = useState(null);
- const [busy, setBusy] = useState(false);
+ const [confirm, setConfirm] = useStateCP(null);
+ const [busy, setBusy] = useStateCP(false);
```

`BatchOps` / `ConfirmDialog` 는 다른 hook 사용 안 함.

## 2. CI Ruff 4건

### 2.1 `server.py:308` F401 — `WorkflowRegistryError` unused
```python
from workflow_registry import WorkflowRegistryError, get_default_registry  # ← Error 부분 미사용
```
함수 본체는 `except Exception:` 으로만 처리 → 임포트만 제거.

### 2.2 `server.py:3384` F821 — `"ValidationResult"` 미정의
forward-ref 문자열만 있고 import 부재. `validator.ValidationResult` 를 정식
import 하고 string quote 제거 → 타입 체커도 더 친절해짐.
```diff
- from validator import validate_asset
+ from validator import ValidationResult, validate_asset

- def _validate_asset_with_policy(asset: dict[str, Any]) -> "ValidationResult":
+ def _validate_asset_with_policy(asset: dict[str, Any]) -> ValidationResult:
```

### 2.3 `server.py:3431` F841 — `result_passed` 미사용
`except Exception as exc:` 분기에서 `result_passed = False` 했지만 그 변수가
이후 어디서도 안 읽힘 (`result_msg` 만 사용). 라인 제거.

### 2.4 `tests/test_workflow_endpoints.py:1480` F841 — `real_registry` 미사용
테스트가 `WorkflowRegistry` 를 만들지만 그 객체를 어디에도 안 씀. 실제로는
`_srv._resolve_validation_args` 가 내부에서 `get_default_registry()` 를 따로
호출. 미사용 인스턴스 + 미사용 import 라인 함께 제거.

## 3. CodeQL inline 4건 (advisory)

### 3.1 `server.py:2581` Information exposure through an exception (PR #48 도입)

`delete_batch_api` 의 응답 본문에 unlink 실패 시 path + OSError 메시지를
배열로 노출 (`failed_unlinks: list[str]`). CodeQL 가이드 위반 — stack/path
는 응답으로 흘리지 말 것.

수정: 응답에는 카운트만 (`failed_unlink_count: int`), 상세는 서버 logger
로:
```python
except OSError:
    failed_unlink_count += 1
    logging.getLogger(__name__).warning(
        "delete_batch: unlink 실패 batch_id=%s path=%s",
        batch_id, path_str, exc_info=True,
    )
```
`logger.warning` 의 `exc_info=True` 로 stack 은 운영 로그에만.

### 3.2 `server.py:2360` Empty except (PR #48 도입)
`_enqueue_design_batch` 의 disk guard 가 variant 누락 시 카운트 추정만 1 로
유지하려 한 코드인데 `pass` 만 있어 의도가 안 보였음. 설명 주석 + `continue`
로 명확화.

### 3.3 `server.py:1264-1277` Empty except (pre-existing, advisory에서 1270 만 명시되었지만 같은 idiom 3개 모두 정리)
lifespan 종료 시 background task 의 `asyncio.CancelledError` 는 의도된 흐름.
그룹 위에 설명 주석 + 각 `pass` 옆 `# expected on shutdown` inline 코멘트.

### 3.4 `server.py:3431` Unused local variable
ruff F841 (§2.3) 와 동일 — 같이 제거됨.

## 검증

```bash
.venv/bin/python -m ruff check .
  → All checks passed!

.venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q
  → 433 passed
```

`/app/cherry-pick/btc_*` 화면 smoke 는 사용자 검증 단계.

## 회귀 가드 후보 (별도 PR)

- **로컬 pre-commit hook** — `ruff check .` 가 머지 전에 자동으로 돌도록.
  본 PR 의 1차 사고는 "내가 로컬에서 ruff 를 안 돌렸다" 가 직접 원인.
- **`--require-passing-checks` 머지 정책** — `gh pr merge` 가 빨간 CI 를
  무시하지 못하도록 branch protection rule + required status check 추가.
- **JSX `no-undef` 린터** — CherryPick 의 hook alias 같은 import-scope
  버그를 정적으로 잡으려면 ESLint + babel preset 한 번 도는 정적 검사.
  프로젝트에 jsx 린터 자체가 없음 (인프라 PR 분리).

## 커밋

```
12dd347 fix(cherry-pick): BatchOps 가 useState 미정의로 화면 크래시
+ pending: ruff 4 + codeql 4 정리 (이 메시지 작성 후 amend 또는 별도 커밋)
```
