import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import server
from models import Database


class _DummyEventBroker:
    async def publish(self, event):  # noqa: ANN001
        return None


@pytest.fixture()
def isolated_server_state(tmp_path, monkeypatch):  # noqa: ANN001
    db = Database(tmp_path / "batch.db")
    asyncio.run(db.init())
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "event_broker", _DummyEventBroker())
    return db


def test_generate_batch_with_spec_id_creates_job_and_tasks(isolated_server_state) -> None:  # noqa: ANN001
    async def scenario() -> None:
        request = server.BatchGenerateRequest(spec_id="cat-raising")
        response = await server.generate_batch(request)

        assert response["task_count"] > 0
        assert "job_id" in response

        job = await server.db.get_job(response["job_id"])
        assert job is not None
        assert job.status == "running"
        assert job.total_count == response["task_count"]

    asyncio.run(scenario())


def test_generate_batch_with_invalid_spec_id_raises_404(isolated_server_state) -> None:  # noqa: ANN001
    async def scenario() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await server.generate_batch(server.BatchGenerateRequest(spec_id="missing-spec"))
        assert exc_info.value.status_code == 404

    asyncio.run(scenario())


def test_generate_batch_with_inline_spec_tracks_exact_task_count(isolated_server_state) -> None:  # noqa: ANN001
    async def scenario() -> None:
        spec = {
            "project": "inline-project",
            "characters": [
                {
                    "id": "ksh",
                    "character_prompt": "cat",
                    "stages": [{"stage": "baby", "output_size": 64, "actions": ["idle", "sleep"]}],
                }
            ],
            "ui_assets": [{"id": "btn_feed", "prompt_hint": "food bowl", "size": 32}],
            "generation_config": {"base_prompt": "pixel art"},
        }

        response = await server.generate_batch(server.BatchGenerateRequest(spec=spec))
        assert response["task_count"] == 3

        job = await server.db.get_job(response["job_id"])
        assert job is not None
        assert job.total_count == 3

    asyncio.run(scenario())
