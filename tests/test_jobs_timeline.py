import asyncio
from pathlib import Path

from models import Database


def test_list_recent_jobs_orders_by_created_at_desc(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "jobs.db")
        await db.init()

        await db.create_job("job-1", "generate_single", {"a": 1})
        await db.create_job("job-2", "generate_batch", {"a": 2})
        await db.create_job("job-3", "scan", {"a": 3})

        recent = await db.list_recent_jobs(limit=2)
        assert len(recent) == 2
        assert recent[0]["id"] == "job-3"
        assert recent[1]["id"] == "job-2"

    asyncio.run(scenario())
