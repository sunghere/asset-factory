# fix(cherry-pick): 같은 후보 무한 재반려 + skip 1장 + 반려해도 안 사라짐

> Branch: `fix/cherrypick-status-derivation` → `main`

## 증상 (사용자 리포트)

A. 오늘의 큐에서 한 장씩 넘길 때 가끔 2장이 한꺼번에 넘어감 (1장 skip).
B. 반려해도 같은 후보가 목록에 그대로 남고, 토스트 "반려됨 · 121" 이 같은 id
   로 여러 번 누적 (스크린샷 — 121 이 3번 반려된 상태).

## 원인

**스키마 mismatch** — backend 와 frontend 가 후보 상태를 다른 키로 관리.

| 레이어 | 키 |
|--|--|
| `asset_candidates` 테이블 | `is_rejected: 0|1` + `picked_at: TEXT|NULL` |
| `CherryPick.jsx` | `c.status: 'pending'|'rejected'|'approved'` |

Frontend 의 모든 분기가 `c.status === 'rejected'` 로 이뤄짐:
- reject() 의 early-return 가드 (`if (cur.status === 'rejected') return`)
- `visibleItems` 의 hideRejected 필터
- `remaining` / `approvedCount` / `rejectedCount` 카운트

`setStatusLocal(id, 'rejected')` 가 optimistic 으로 `c.status` 를 붙여
**첫 번째 reject 는 정상 동작**. 그러나 그 직후 server 가
`EVT_CANDIDATE_REJECTED` SSE 를 발행하면 `useSSE` 핸들러가
`candidates.reload()` 를 호출 → `/api/batches/{id}/candidates` 가 `is_rejected:
1` 만 반환하고 `status` 필드는 부재 → **로컬 status 가 사라짐**.

그 시점부터:
- early-return 가드 false → 같은 후보 재반려 가능 (`x` 누를 때마다 toast 누적)
- hideRejected 필터 동작 안 함 → 반려 항목 그대로 보임 (증상 B)
- visibleItems 인덱스가 reload 전후로 흔들리며 cursor 가 1장 건너뛴 것처럼
  보임 (증상 A)

## 수정

`asset_candidates` 응답에서 서버가 `status` 를 derive 해 single-source-of-truth
유지. 두 endpoint:

```python
def _derive_candidate_status(row: dict[str, Any]) -> str:
    if int(row.get("is_rejected") or 0) == 1:
        return "rejected"
    if row.get("picked_at"):
        return "approved"
    return "pending"
```

- `GET /api/batches/{id}/candidates` (cherry-pick 화면 본 데이터)
- `GET /api/assets/{id}/candidates` (AssetDetail 일관성)

frontend 는 손대지 않음 — 이미 `c.status` 를 정상적으로 읽고 있고, optimistic
업데이트 후 SSE reload 가 들어와도 서버가 같은 키로 같은 값을 돌려주니 흐름
유지.

## 회귀 가드

`tests/test_cherry_pick_flow.py::test_list_batch_candidates_derives_status_field`
신규 테스트 — pending → reject → rejected → unreject → pending 의 status
파생을 검증. is_rejected 와 status 가 둘 다 응답에 있어 일관성 확인.

## 검증

```
.venv/bin/python -m ruff check .
  → All checks passed!

.venv/bin/python -m pytest --ignore=tests/test_generator_comfyui.py -q
  → 434 passed (433 + 신규 1)
```

수기 UI smoke (사용자 검증):
- `/app/cherry-pick/{batch}` 에서 `x` 로 반려 → 같은 id 가 toast 한 번만,
  cursor 가 정확히 1장씩 이동, hideRejected 토글 동작
