# feat(gc): orphan candidate 일괄 정리 endpoint + System UI

> Branch: `feat/gc-orphan-candidates` → `main`

## 컨텍스트

`/app/assets/{id}` 의 candidate 그리드에서 broken-image 카드만 잔뜩 보이는
상태 — image_path 가 disk 에 없는데 `asset_candidates` row 는 남아 있음.

진단 (asset id `62a0d3d2-…`):
```
count=32, all is_rejected=1 / status=rejected
all image_path 가 .../slot_NNN.png 이지만 Path.exists()=False
```

원인 가설: candidate_gc 가 reject 후보의 disk 파일을 unlink 한 뒤 일부 row
삭제가 누락됐거나 (또는 외부 도구가 파일만 지웠거나). 어쨌든 일단 일괄
정리할 수단이 필요.

## 변경

### Backend

`models.Database`:
- `list_all_candidate_id_paths()` — 모든 후보의 (id, image_path) 페어 일괄
  조회 (orphan 검사용 가벼운 select).
- `delete_candidates_by_ids(ids)` — id 리스트로 일괄 DELETE. SQLite default
  variable 한계 (999) 회피 위해 500 단위 chunking. 호출자는 disk 파일이 이미
  unlink 됐거나 부재함을 보장.

`server.py`:
- `POST /api/system/gc/orphan-candidates?dry_run=bool&limit=int`
  - 모든 candidate row 의 image_path 를 `Path.exists()` 로 검사.
  - dry_run=true (기본): 카운트 + sample path 만 반환.
  - dry_run=false: orphan 만 일괄 DELETE, 응답에 deleted 카운트.
  - stat 실패 (NFS/권한) 는 false-positive 우려로 건너뜀.

### Frontend

`api.jsx`: `gcOrphanCandidates({dryRun, limit})`.

`screens/System.jsx`: 기존 GC 카드 위에 신규 패널 — "스캔 (dry-run)" 버튼,
검출되면 sample path expandable + "N건 영구 삭제" 빨간 버튼 (window.confirm
한 번 거침). 결과 toast 로 노출.

## 검증

```
.venv/bin/python -m ruff check .  → All checks passed!
.venv/bin/python -m pytest -q     → 435 passed (+1)
```

회귀 가드:
`tests/test_cherry_pick_flow.py::test_gc_orphan_candidates_dry_run_then_delete` —
3개 후보 중 1개 image 파일을 일부러 unlink → dry_run 응답에 orphan_count=1,
DB 보존 확인 → dry_run=false 호출 → orphan row 만 삭제, 정상 row 2건 보존.

## 사용 예 (curl)

```bash
# dry-run 으로 검출
curl -X POST localhost:8000/api/system/gc/orphan-candidates | jq .
# 실 삭제
curl -X POST 'localhost:8000/api/system/gc/orphan-candidates?dry_run=false' | jq .
```

## scope 밖 (별도 PR)

- candidate_gc 의 unlink → row 삭제 race window 가 발생한 근본 원인 분석.
  지금은 후속 청소 도구만 제공.
- AssetDetail.jsx 의 broken-image 카드 자체를 더 친절하게 (예: "이미지 없음
  · 정리 안내") — 본 PR 머지 후 같은 endpoint 호출 버튼 inline 으로 둘 수
  있음.
