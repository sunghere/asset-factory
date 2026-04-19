"""Cherry-pick e2e: list → reject → approve → asset_history.

테스트는 SD를 호출하지 않고 ``insert_asset_candidate`` 로 직접 후보를 만든 뒤
HTTP API로 list / reject / approve를 검증한다."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server
from candidate_gc import run_gc_candidates
from models import Database


class _DummyBroker:
    async def publish(self, _event):  # noqa: ANN001
        return None


def _make_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), (8, 16, 32, 255)).save(path)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "cp.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyBroker())
    monkeypatch.setattr(server, "api_key", None)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "DATA_DIR", data)
    return db, data


async def _seed_candidates(db: Database, data: Path, batch_id: str) -> list[int]:
    """3개 후보를 ``data/candidates/proj/key/job1/slot_n.png`` 에 만들고 DB에 삽입."""
    ids: list[int] = []
    for slot in range(3):
        img = data / "candidates" / "proj" / "key" / "job1" / f"slot_{slot}.png"
        _make_png(img)
        await db.insert_asset_candidate(
            project="proj",
            asset_key="key",
            slot_index=slot,
            job_id="job1",
            image_path=str(img),
            width=32,
            height=32,
            color_count=4,
            validation_status="pass",
            validation_message="ok",
            generation_seed=100 + slot,
            generation_model=f"model_{slot % 2}",
            generation_prompt="p",
            metadata_json='{"steps":20,"max_colors":32}',
            batch_id=batch_id,
        )
        cand = (await db.list_batch_candidates(batch_id))[-1]
        ids.append(int(cand["id"]))
    return ids


def test_list_batch_candidates_returns_image_url(isolated) -> None:
    db, data = isolated
    asyncio.run(_seed_candidates(db, data, "btc_test1"))
    with TestClient(server.app) as client:
        r = client.get("/api/batches/btc_test1/candidates")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert all("image_url" in item for item in body["items"])
    assert all(item["batch_id"] == "btc_test1" for item in body["items"])


def test_reject_marks_is_rejected_and_orders_last(isolated) -> None:
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_test2"))
    with TestClient(server.app) as client:
        r = client.post(f"/api/batches/btc_test2/candidates/{ids[1]}/reject")
        assert r.status_code == 200
        r2 = client.get("/api/batches/btc_test2/candidates")
    items = r2.json()["items"]
    rejected_item = next(i for i in items if int(i["id"]) == ids[1])
    assert rejected_item["is_rejected"] == 1
    # rejected는 마지막에 정렬된다
    assert int(items[-1]["id"]) == ids[1]


def test_reject_404_for_other_batch(isolated) -> None:
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_owner"))
    with TestClient(server.app) as client:
        # 다른 batch_id로 reject 시도하면 404
        r = client.post(f"/api/batches/btc_other/candidates/{ids[0]}/reject")
    assert r.status_code == 404


def test_approve_from_candidate_creates_asset_when_new(isolated) -> None:
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_app1"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "key", "project": "proj"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    asset_id = body["asset_id"]

    async def check() -> None:
        asset = await db.get_asset(asset_id)
        assert asset is not None
        assert asset["status"] == "approved"
        # 새 unique 경로에 복사되어 있어야 한다 (원본 후보 파일은 보존)
        assert "primary" in asset["image_path"]
        assert Path(asset["image_path"]).exists()

    asyncio.run(check())


def test_approve_from_candidate_replaces_existing_and_writes_history(isolated) -> None:
    db, data = isolated
    # 기존 primary asset 미리 존재시킴
    existing = data / "existing.png"
    _make_png(existing)
    ids = asyncio.run(_seed_candidates(db, data, "btc_app2"))

    async def prep_existing() -> str:
        await db.upsert_scanned_asset(
            project="proj",
            asset_key="key",
            category="character",
            image_path=str(existing),
            width=32,
            height=32,
            color_count=4,
            has_alpha=True,
            validation_status="pass",
            validation_message="ok",
        )
        row = await db.get_asset_by_key("proj", "key")
        assert row is not None
        return row["id"]

    asset_id = asyncio.run(prep_existing())

    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[2], "asset_key": "key", "project": "proj"},
        )
    assert r.status_code == 200

    async def verify_history() -> None:
        hist = await db.list_asset_history(asset_id)
        assert len(hist) == 1
        # 이전 primary 파일 경로가 history에 남고 디스크 파일도 보존됨
        assert hist[0]["image_path"] == str(existing)
        assert existing.exists()
        # 신규 primary는 다른 unique 경로
        asset = await db.get_asset(asset_id)
        assert asset is not None
        assert asset["image_path"] != str(existing)

    asyncio.run(verify_history())


def test_unreject_round_trip(isolated) -> None:
    """5초 undo toast 시나리오: reject → unreject → is_rejected=0."""
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_undo"))
    with TestClient(server.app) as client:
        r = client.post(f"/api/batches/btc_undo/candidates/{ids[0]}/reject")
        assert r.status_code == 200
        r2 = client.post(f"/api/batches/btc_undo/candidates/{ids[0]}/unreject")
        assert r2.status_code == 200
        body = r2.json()
        assert body["is_rejected"] is False

    async def confirm() -> None:
        cand = await db.get_candidate_by_id(ids[0])
        assert cand is not None
        assert cand["is_rejected"] == 0

    asyncio.run(confirm())


def test_unreject_404_for_other_batch(isolated) -> None:
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_undo2"))
    with TestClient(server.app) as client:
        client.post(f"/api/batches/btc_undo2/candidates/{ids[0]}/reject")
        r = client.post(f"/api/batches/btc_other/candidates/{ids[0]}/unreject")
    assert r.status_code == 404


def test_undo_approve_deletes_newly_created_asset(isolated) -> None:
    """approve 후 undo: 신규 생성된 asset이면 삭제, primary 파일도 정리된다."""
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_undo_app1"))
    with TestClient(server.app) as client:
        approve = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "fresh_key", "project": "proj"},
        )
        assert approve.status_code == 200
        asset_id = approve.json()["asset_id"]
        # 새 primary 파일이 디스크에 생긴다
        from pathlib import Path as _P
        async def get_primary() -> str:
            asset = await db.get_asset(asset_id)
            assert asset is not None
            return asset["image_path"]
        primary = asyncio.run(get_primary())
        assert _P(primary).exists()

        undo = client.post(f"/api/assets/{asset_id}/undo-approve")
        assert undo.status_code == 200, undo.text
        body = undo.json()
        assert body["result"] == "ok-deleted"

    async def gone() -> None:
        asset = await db.get_asset(asset_id)
        assert asset is None
        from pathlib import Path as _P
        assert not _P(primary).exists()
        # 원본 후보 파일은 보존
        cand = await db.get_candidate_by_id(ids[0])
        assert cand is not None
        assert _P(cand["image_path"]).exists()

    asyncio.run(gone())


def test_undo_approve_restores_previous_primary_from_history(isolated) -> None:
    """기존 primary가 있던 asset을 approve로 교체했다가 undo → 이전 primary 복원."""
    db, data = isolated
    existing = data / "old_primary.png"
    _make_png(existing)
    ids = asyncio.run(_seed_candidates(db, data, "btc_undo_app2"))

    async def prep_existing() -> str:
        await db.upsert_scanned_asset(
            project="proj", asset_key="key", category="character",
            image_path=str(existing),
            width=32, height=32, color_count=4, has_alpha=True,
            validation_status="pass", validation_message="ok",
        )
        row = await db.get_asset_by_key("proj", "key")
        assert row is not None
        return row["id"]

    asset_id = asyncio.run(prep_existing())

    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "key", "project": "proj"},
        )
        assert r.status_code == 200
        # history 1건, 현재 primary는 새 파일
        async def state_after_approve() -> tuple[str, int]:
            asset = await db.get_asset(asset_id)
            assert asset is not None
            hist = await db.list_asset_history(asset_id)
            return asset["image_path"], len(hist)
        new_primary, hist_len = asyncio.run(state_after_approve())
        assert hist_len == 1
        assert new_primary != str(existing)

        undo = client.post(f"/api/assets/{asset_id}/undo-approve")
        assert undo.status_code == 200, undo.text
        body = undo.json()
        assert body["result"] == "ok-restored"
        assert body["restored_from"] == str(existing)

    async def restored() -> None:
        asset = await db.get_asset(asset_id)
        assert asset is not None
        assert asset["image_path"] == str(existing)
        hist = await db.list_asset_history(asset_id)
        assert hist == []
        from pathlib import Path as _P
        assert _P(existing).exists()
        assert not _P(new_primary).exists()

    asyncio.run(restored())


def test_inline_edit_asset_key_creates_new_asset(isolated) -> None:
    """meta 패널 inline 편집으로 다른 asset_key를 보낼 때 새 asset이 만들어진다."""
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_inline"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "edited_key_v2", "project": "proj"},
        )
    assert r.status_code == 200, r.text
    asset_id = r.json()["asset_id"]

    async def check() -> None:
        asset = await db.get_asset(asset_id)
        assert asset is not None
        assert asset["asset_key"] == "edited_key_v2"
        assert asset["status"] == "approved"

    asyncio.run(check())


def test_approved_image_lives_outside_candidate_gc_tree(isolated) -> None:
    """승격된 primary 이미지는 ``data/candidates/`` 가 아닌 ``data/approved/`` 에 들어가야 한다.

    회귀 방지: codex P1 — ``/api/assets/approve-from-candidate`` 가 dest를
    ``DATA_DIR/candidates/...`` 에 두던 시절에는 ``run_gc_candidates`` 가
    age/size 정책으로 primary 파일을 삭제해서 ``image_path`` 가 dangling이 됐다.
    """
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_dest_loc"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "key", "project": "proj"},
        )
    assert r.status_code == 200, r.text
    asset_id = r.json()["asset_id"]

    async def get_primary() -> str:
        a = await db.get_asset(asset_id)
        assert a is not None
        return a["image_path"]
    primary = Path(asyncio.run(get_primary()))
    candidates_root = (data / "candidates").resolve()
    approved_root = (data / "approved").resolve()
    # primary는 candidates/ 트리 밖 + approved/ 트리 안에 있어야 한다
    assert approved_root in primary.resolve().parents, primary
    assert candidates_root not in primary.resolve().parents, primary

    # GC를 공격적으로 돌려도 (max_age=0초 = 모두 노쇠) primary는 살아남아야 한다.
    # GC가 보는 DB는 별도 핸들이라 같은 SQLite 파일을 가리키게 옮겨준다.
    target_db = data / "asset-factory.db"
    target_db.write_bytes(Path(db.db_path).read_bytes())
    run_gc_candidates(data, max_age_seconds=0, max_total_bytes=0)
    assert primary.exists(), "approved primary 파일이 GC에 잡혔다 (회귀)"


def test_first_time_approval_preserves_generation_provenance(isolated) -> None:
    """신규 asset 첫 승인이어도 candidate가 들고 있던 seed/model/prompt/metadata 가
    asset 행에 남아 있어야 한다 (``/api/assets/{id}/regenerate`` 가 디폴트로
    폴백하지 않게).

    회귀 방지: codex P2 — 첫 승인 경로가 ``upsert_scanned_asset`` 만 호출하고
    generation_* 필드를 비워두던 버그.
    """
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_provenance"))
    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[0], "asset_key": "fresh", "project": "proj"},
        )
    assert r.status_code == 200, r.text
    asset_id = r.json()["asset_id"]

    async def check() -> None:
        cand = await db.get_candidate_by_id(ids[0])
        asset = await db.get_asset(asset_id)
        assert asset is not None and cand is not None
        assert asset["generation_seed"] == cand["generation_seed"]
        assert asset["generation_model"] == cand["generation_model"]
        assert asset["generation_prompt"] == cand["generation_prompt"]
        assert asset["metadata_json"] == cand["metadata_json"]

    asyncio.run(check())


def test_approve_marks_candidate_picked_at(isolated) -> None:
    """approve-from-candidate 가 candidate 행의 picked_at 을 채워야 한다.

    회귀 방지: codex P1 — batch 완료 추적이 picked_at 기반이 아니라
    ``(project, asset_key)`` 휴리스틱이던 버그.
    """
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_picked"))
    # 승인 전: picked_at 비어있음
    async def before() -> None:
        c = await db.get_candidate_by_id(ids[1])
        assert c is not None and c["picked_at"] is None
    asyncio.run(before())

    with TestClient(server.app) as client:
        r = client.post(
            "/api/assets/approve-from-candidate",
            json={"candidate_id": ids[1], "asset_key": "k2", "project": "proj"},
        )
    assert r.status_code == 200

    async def after() -> None:
        c = await db.get_candidate_by_id(ids[1])
        assert c is not None and c["picked_at"] is not None
        # batch 단위 헬퍼도 True
        assert await db.batch_has_picked_candidate("btc_picked")
    asyncio.run(after())


def test_gc_prioritizes_rejected_candidates(isolated) -> None:
    """reject된 후보 파일은 mtime이 새것이라도 즉시 GC된다."""
    db, data = isolated
    ids = asyncio.run(_seed_candidates(db, data, "btc_gc1"))
    # DB 파일을 GC 디폴트 위치로 옮긴다 (DATA_DIR/asset-factory.db)
    target_db = data / "asset-factory.db"
    target_db.write_bytes(Path(db.db_path).read_bytes())
    # GC가 이 새 DB 파일을 보도록 새 Database 핸들 설정
    new_db = Database(target_db)
    asyncio.run(new_db.init())
    # 새 DB에 같은 후보 다시 삽입 + 1번 reject
    async def seed_and_reject() -> int:
        new_ids: list[int] = []
        for slot in range(3):
            img = data / "candidates" / "proj" / "key" / "job1" / f"slot_{slot}.png"
            await new_db.insert_asset_candidate(
                project="proj",
                asset_key="key",
                slot_index=slot,
                job_id="job1",
                image_path=str(img),
                width=32, height=32, color_count=4,
                validation_status="pass", validation_message="ok",
                generation_seed=slot, generation_model="m", generation_prompt="p",
                metadata_json="{}",
                batch_id="btc_gc1",
            )
            cand = (await new_db.list_batch_candidates("btc_gc1"))[-1]
            new_ids.append(int(cand["id"]))
        await new_db.reject_candidate(new_ids[1])
        return new_ids[1]

    rejected_id = asyncio.run(seed_and_reject())
    # max_age_seconds=10**9 (사실상 비활성), max_total_bytes=0 (용량 GC도 비활성)
    # → 오직 rejected만 지워져야 한다.
    # Note: list_batch_candidates 가 같은 후보를 두 번 적재할 수 있으므로
    # 실제 디스크 상 파일은 3개(slot 0/1/2) 그대로.
    assert (data / "candidates" / "proj" / "key" / "job1" / "slot_1.png").exists()
    result = run_gc_candidates(data, max_age_seconds=10**9, max_total_bytes=0)
    assert result["deleted_files"] >= 1
    assert not (data / "candidates" / "proj" / "key" / "job1" / "slot_1.png").exists()
    # 다른 슬롯은 보존
    assert (data / "candidates" / "proj" / "key" / "job1" / "slot_0.png").exists()
    assert (data / "candidates" / "proj" / "key" / "job1" / "slot_2.png").exists()
    _ = ids, rejected_id  # 미사용 명시
