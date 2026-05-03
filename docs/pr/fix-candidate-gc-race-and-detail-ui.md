# fix(gc) + feat(asset-detail): rejected GC row 누락 root cause + per-candidate cleanup

> Branch: `fix/candidate-gc-race-and-detail-ui` → `main`
> 작업 위치: git worktree (`asset-factory-followup`).
> 후속: PR #52 (orphan candidate bulk endpoint) 의 두 후속 항목.

## Item 1 — Root cause: rejected GC 가 row 를 정리하지 않음 (실 버그)

PR #52 의 dry-run 이 main 환경에서 검출한 **46건 orphans / 145건 전체 (31%)**
의 직접 원인을 찾았다. `candidate_gc.py` 의 step 1 (rejected) 와 step 2
(bypass) 가 의도적으로는 같은 정합성이어야 하는데 step 1 만 row 정리가
빠져 있었다.

```python
# step 1: rejected (이전 — 버그)
for raw in rejected_paths:
    ...
    path.unlink()  # 파일 unlink
    ...
# 끝. _delete_candidate_rows() 호출 없음 — row 가 dangling 으로 남는다.

# step 2: bypass (참고 — 정상)
if deleted_bypass_paths:
    _delete_candidate_rows(data_root, deleted_bypass_paths)  # ← step 1 에 부재
```

reject + GC 사이클을 돌릴 때마다 row 가 누적되어, 사용자 화면 (AssetDetail
candidate 그리드, /app/cherry-pick) 의 broken-image 카드가 영구화.

### Fix

step 1 도 step 2 와 같은 패턴으로 — 파일 unlink 후 같은 image_path 의 row
를 일괄 DELETE. 추가로 **파일이 이미 없는 rejected row 도 정리 대상에
포함** — 외부 도구가 파일만 지우거나 이전 GC 가 row 를 빠뜨린 historical
케이스를 다음 GC 한 번에 자동 회복.

`run_gc_candidates` 응답에 `deleted_rows` 필드 신설 — UI/로그에서 "이번
사이클에 row 몇 개 정리됐는지" 가시성 확보.

### 회귀 가드

- 기존 `test_run_gc_candidates_purges_only_rejected_files` 확장 — 파일
  뿐 아니라 row 도 사라지는지 verify (이전엔 파일만 검사).
- 신규 `test_run_gc_candidates_cleans_orphan_row_when_file_missing` —
  파일은 처음부터 없는 rejected row 가 다음 GC 호출에서 자동 회복되는지.

## Item 2 — Per-candidate cleanup endpoint + AssetDetail UI

bulk 정리 (PR #52 의 `/api/system/gc/orphan-candidates`) 옆에 **단일 후보
정리 경로** 추가. AssetDetail 의 broken-image 카드 inline 에서 "이 row 만
정리" 가 가능해진다.

### Backend

`DELETE /api/asset-candidates/{candidate_id}`:
- 후보 검증 (없으면 404).
- image_path 가 disk 에 있으면 unlink (실패해도 row 정리는 진행 — orphan
  보존보다 정리 우선).
- `delete_candidates_by_ids` 로 row 삭제.
- 응답: `{ ok, candidate_id, deleted_row, file_existed, file_unlinked }`.

### Frontend

`AssetDetail.jsx` 의 candidate 그리드를 별도 `CandidateCard` 컴포넌트로
분리. `<img onError={() => setBroken(true)}>` 로 broken 상태 감지:
- 정상: 기존 그대로 (이미지 + "primary 로 교체" 버튼).
- broken: thumb 영역을 "이미지 없음 / candidate row 만 남은 dangling 상태"
  플레이스홀더로 교체 + 빨간색 "정리" 버튼. 클릭 시 confirm + 위 endpoint
  호출 → `cands.reload()`.

`api.jsx`: `deleteAssetCandidate(candidateId)` 메서드 추가.

### 회귀 가드

- 신규 `test_delete_asset_candidate_unlinks_file_and_row` — 정상 케이스 (파일
  + row 둘 다 정리) + orphan 케이스 (파일 부재 시에도 row 정리, file_existed
  플래그 false) 둘 다 검증.
- 신규 `test_delete_asset_candidate_404` — 미존재 id 는 404.

## 검증

```
.venv/bin/python -m ruff check .  → All checks passed!
.venv/bin/python -m pytest -q     → 438 passed (+3)
```

## scope 밖

- bypass step 의 row 정리 자체는 이전부터 정상 — 코드 그대로 유지.
- candidate_gc 의 step 3 (일반 retention) / step 4 (용량 상한) 은 reject /
  bypass 가 아닌 일반 후보 (살아 있는 검토 큐) 의 mtime 기반 정리라, row
  정리를 일괄 적용하면 의미가 달라짐 (사용자가 지운 적 없는 row 까지
  사라짐). 본 PR 은 손대지 않음.
