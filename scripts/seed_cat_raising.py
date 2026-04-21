"""Seed realistic cat-raising batches for local UI testing.

Populates three design batches directly into the DB + disk so the new React
SPA (/app/*) has non-trivial data on every screen without running Stable
Diffusion for real:

  1. ``ksh_baby_idle``  — 4 "done" tasks, 4 candidates with mixed validation
                           (pass/fail/pending) so the validation badges in
                           CherryPick / Assets / BatchDetail light up.
  2. ``ksh_baby_eat``   — multi-axis batch (2 seeds × 2 models × 1 prompt =
                           4 tasks). Exercises the SpecView "axes" block.
  3. ``btn_feed``       — ui-category batch, 1 seed × 1 model × 2 prompts.
                           The second task is left in "queued" so the Queue
                           screen / Batches active count is non-empty.

Reuses the 13 existing PNGs under ``data/candidates/cat-raising/`` when
available and falls back to a tiny generated PNG for missing slots.

Idempotent: running twice wipes previous seed output for the three batches
first. Existing non-seeded cat-raising rows (e.g. historical ``ksh_baby_idle``
tasks with NULL batch_id) are left untouched.

Usage::

    .venv/bin/python scripts/seed_cat_raising.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import aiosqlite  # noqa: E402

from models import Database, utc_now  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "asset-factory.db"
CAND_DIR = REPO_ROOT / "data" / "candidates" / "cat-raising"

SEED_BATCH_IDS = [
    "btc_seed_cr_baby_idle",
    "btc_seed_cr_baby_eat",
    "btc_seed_cr_btn_feed",
]


def _make_png(path: Path, color: tuple[int, int, int, int], size: int = 64) -> None:
    """Write a tiny flat-color PNG to ``path`` (no deps)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    w = h = size
    raw = bytearray()
    for _ in range(h):
        raw.append(0)
        raw.extend(bytes(color) * w)
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b""))


def _color_for(key: str) -> tuple[int, int, int, int]:
    """Deterministic per-slot color so UI thumbs are distinguishable."""
    h = hashlib.md5(key.encode()).digest()
    return (h[0], h[1], h[2], 255)


def _existing_images() -> list[Path]:
    if not CAND_DIR.exists():
        return []
    return sorted(CAND_DIR.glob("ksh_baby_idle__*.png"))


async def _wipe_seeds(db_path: Path) -> None:
    async with aiosqlite.connect(str(db_path)) as conn:
        for bid in SEED_BATCH_IDS:
            await conn.execute("DELETE FROM asset_candidates WHERE batch_id=?", (bid,))
            await conn.execute("DELETE FROM generation_tasks WHERE batch_id=?", (bid,))
            await conn.execute("DELETE FROM jobs WHERE id=?", (bid,))
        await conn.commit()


_FAR_FUTURE = "2099-01-01T00:00:00+00:00"


async def _insert_task(
    conn: aiosqlite.Connection,
    *,
    batch_id: str,
    job_id: str,
    project: str,
    asset_key: str,
    category: str,
    prompt: str,
    negative_prompt: str,
    model_name: str,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    width: int,
    height: int,
    max_colors: int,
    status: str,
    lora_spec: list[dict[str, Any]] | None = None,
) -> None:
    now = utc_now()
    # queued tasks 는 worker 가 바로 claim 해서 실제 생성 시도를 하지 않도록
    # next_attempt_at 을 먼 미래로 세팅한다. done/failed 에는 영향 없음.
    next_attempt_at = _FAR_FUTURE if status == "queued" else None
    await conn.execute(
        """
        INSERT INTO generation_tasks (
            job_id, project, asset_key, category, prompt, negative_prompt,
            model_name, width, height, steps, cfg, sampler, status,
            retries, max_retries, last_error, expected_size, max_colors,
            candidate_slot, candidates_total,
            batch_id, lora_spec_json, seed, next_attempt_at,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 3, NULL, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, project, asset_key, category, prompt, negative_prompt,
            model_name, width, height, steps, cfg, sampler, status,
            width, max_colors,
            batch_id, json.dumps(lora_spec or []), seed, next_attempt_at,
            now, now,
        ),
    )


async def _insert_candidate(
    conn: aiosqlite.Connection,
    *,
    batch_id: str,
    job_id: str,
    project: str,
    asset_key: str,
    slot_index: int,
    image_path: str,
    width: int,
    height: int,
    color_count: int,
    validation_status: str,
    validation_message: str | None,
    seed: int,
    model: str,
    prompt: str,
    is_rejected: int = 0,
) -> None:
    now = utc_now()
    await conn.execute(
        """
        INSERT INTO asset_candidates (
            project, asset_key, slot_index, job_id, image_path,
            width, height, color_count, validation_status, validation_message,
            generation_seed, generation_model, generation_prompt, metadata_json,
            batch_id, is_rejected, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project, asset_key, slot_index, job_id, image_path,
            width, height, color_count, validation_status, validation_message,
            seed, model, prompt,
            json.dumps({"seed": seed, "model": model, "prompt": prompt}),
            batch_id, is_rejected, now,
        ),
    )


async def _ensure_job(conn: aiosqlite.Connection, job_id: str, total: int, done: int) -> None:
    now = utc_now()
    status = "done" if done >= total else "running"
    await conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, total_count, completed_count, failed_count,
                          error_message, payload_json, created_at, updated_at)
        VALUES (?, 'design-batch', ?, ?, ?, 0, NULL, NULL, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            total_count=excluded.total_count,
            completed_count=excluded.completed_count,
            updated_at=excluded.updated_at
        """,
        (job_id, status, total, done, now, now),
    )


async def seed_idle(conn: aiosqlite.Connection) -> None:
    """Batch 1: 4 done tasks, 4 candidates (mixed validation)."""
    batch_id = "btc_seed_cr_baby_idle"
    job_id = batch_id
    project = "cat-raising"
    asset_key = "ksh_baby_idle"
    prompt = (
        "pixel art, game sprite, transparent background, cute orange tabby cat, "
        "pixel art sprite, baby stage, idle pose, 64x64"
    )
    neg = "blurry, realistic, 3d render"
    model = "pixelArtDiffusionXL_spriteShaper"
    existing = _existing_images()[:4]

    await _ensure_job(conn, job_id, total=4, done=4)

    seeds = [1001, 1337, 2077, 4242]
    validations = [
        ("pass", None),
        ("pass", None),
        ("fail", "색상 수가 max_colors(32) 초과: 47"),
        ("pending", None),
    ]
    for i, seed in enumerate(seeds):
        await _insert_task(
            conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
            category="character", prompt=prompt, negative_prompt=neg, model_name=model,
            seed=seed, steps=20, cfg=7.0, sampler="DPM++ 2M", width=64, height=64,
            max_colors=32, status="done",
        )
        if i < len(existing):
            img_path = str(existing[i])
        else:
            p = CAND_DIR / f"seed_idle_slot{i}.png"
            _make_png(p, _color_for(f"{batch_id}:{i}"))
            img_path = str(p)
        vs, vm = validations[i]
        await _insert_candidate(
            conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
            slot_index=i, image_path=img_path, width=64, height=64, color_count=30 if vs != "fail" else 47,
            validation_status=vs, validation_message=vm, seed=seed, model=model, prompt=prompt,
        )


async def seed_eat(conn: aiosqlite.Connection) -> None:
    """Batch 2: multi-axis (2 seeds × 2 models × 1 prompt)."""
    batch_id = "btc_seed_cr_baby_eat"
    job_id = batch_id
    project = "cat-raising"
    asset_key = "ksh_baby_eat"
    prompt = (
        "pixel art, game sprite, transparent background, cute orange tabby cat, "
        "baby stage, eating from food bowl, 64x64"
    )
    neg = "blurry, realistic, 3d render"
    models = ["pixelArtDiffusionXL_spriteShaper", "dreamshaper_8"]
    seeds = [555, 888]

    await _ensure_job(conn, job_id, total=len(models) * len(seeds), done=len(models) * len(seeds))

    slot = 0
    for model in models:
        for seed in seeds:
            await _insert_task(
                conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
                category="character", prompt=prompt, negative_prompt=neg, model_name=model,
                seed=seed, steps=20, cfg=7.0, sampler="DPM++ 2M", width=64, height=64,
                max_colors=32, status="done",
                lora_spec=[{"name": "pixel_cute_v1", "weight": 0.6}],
            )
            p = CAND_DIR / f"seed_eat_{slot}.png"
            _make_png(p, _color_for(f"{batch_id}:{slot}"))
            await _insert_candidate(
                conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
                slot_index=slot, image_path=str(p), width=64, height=64, color_count=28,
                validation_status="pass" if slot % 3 else "fail",
                validation_message=None if slot % 3 else "투명도 부족(alpha<200 pixel 비율 12%)",
                seed=seed, model=model, prompt=prompt,
            )
            slot += 1


async def seed_btn(conn: aiosqlite.Connection) -> None:
    """Batch 3: ui asset, 1 task queued (active batch visualization)."""
    batch_id = "btc_seed_cr_btn_feed"
    job_id = batch_id
    project = "cat-raising"
    asset_key = "btn_feed"
    model = "pixelArtDiffusionXL_spriteShaper"
    neg = "blurry, realistic, 3d render"
    prompts = [
        "pixel art, transparent background, food bowl icon, ui button, 32x32",
        "pixel art, transparent background, paw print feed icon, ui button, 32x32",
    ]
    seed = 7777

    await _ensure_job(conn, job_id, total=2, done=1)

    # task 1 — done with candidate
    await _insert_task(
        conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
        category="ui", prompt=prompts[0], negative_prompt=neg, model_name=model,
        seed=seed, steps=15, cfg=6.5, sampler="Euler a", width=32, height=32,
        max_colors=16, status="done",
    )
    p = CAND_DIR / "seed_btn_feed_0.png"
    _make_png(p, _color_for(f"{batch_id}:0"), size=32)
    await _insert_candidate(
        conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
        slot_index=0, image_path=str(p), width=32, height=32, color_count=14,
        validation_status="pass", validation_message=None,
        seed=seed, model=model, prompt=prompts[0],
    )

    # task 2 — still queued (shows up in Queue screen, contributes to Batches active count)
    await _insert_task(
        conn, batch_id=batch_id, job_id=job_id, project=project, asset_key=asset_key,
        category="ui", prompt=prompts[1], negative_prompt=neg, model_name=model,
        seed=seed, steps=15, cfg=6.5, sampler="Euler a", width=32, height=32,
        max_colors=16, status="queued",
    )


async def main() -> None:
    assert DB_PATH.exists(), f"DB not found: {DB_PATH}. Run the server once first."
    CAND_DIR.mkdir(parents=True, exist_ok=True)

    db = Database(DB_PATH)
    await db.init()

    await _wipe_seeds(DB_PATH)

    async with aiosqlite.connect(str(DB_PATH)) as conn:
        await seed_idle(conn)
        await seed_eat(conn)
        await seed_btn(conn)
        await conn.commit()

    print("[seed] cat-raising: 3 batches seeded")
    for bid in SEED_BATCH_IDS:
        print(f"       - {bid}")
    print(f"[seed] images in {CAND_DIR}")
    # secrets imported to keep parity with random seeds if extended later
    _ = secrets.token_hex


if __name__ == "__main__":
    asyncio.run(main())
