"""Seed deterministic mock data for all SPA screens."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import Database, utc_now  # noqa: E402
DATA_DIR = Path(os.getenv("ASSET_FACTORY_DATA_DIR", str(REPO_ROOT / "data"))).expanduser().resolve()
DB_PATH = Path(os.getenv("ASSET_FACTORY_DB_PATH", str(DATA_DIR / "asset-factory.db"))).expanduser().resolve()
EXPORT_ROOT = Path(
    os.getenv("ASSET_FACTORY_EXPORT_ROOT", str(Path.home() / "workspace" / "assets"))
).expanduser().resolve()

PROJECT = "ui-mock"
BATCH_IDS = ("btc_uimock_char", "btc_uimock_ui", "btc_uimock_bg")


def _png(path: Path, rgba: tuple[int, int, int, int], size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = h = size
    raw = bytearray()
    for _ in range(h):
        raw.append(0)
        raw.extend(bytes(rgba) * w)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b""))


def _img(rel: str, color: tuple[int, int, int, int], size: int = 64) -> Path:
    path = DATA_DIR / rel
    _png(path, color, size=size)
    return path


async def _wipe() -> None:
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("DELETE FROM asset_history WHERE asset_id IN (SELECT id FROM assets WHERE project=?)", (PROJECT,))
            conn.execute("DELETE FROM asset_candidates WHERE project=?", (PROJECT,))
            conn.execute("DELETE FROM generation_tasks WHERE project=?", (PROJECT,))
            conn.execute("DELETE FROM assets WHERE project=?", (PROJECT,))
            conn.execute("DELETE FROM jobs WHERE id LIKE 'job_uimock_%'")
            conn.execute("DELETE FROM jobs WHERE id IN (?, ?, ?)", BATCH_IDS)
            conn.commit()
        finally:
            conn.close()


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    db = Database(DB_PATH)
    await db.init()
    await _wipe()

    conn = sqlite3.connect(DB_PATH)
    now = utc_now()
    try:
        conn.execute("INSERT INTO jobs VALUES (?, 'design-batch', 'completed_with_errors', 4, 3, 1, NULL, ?, ?, ?)", ("job_uimock_char", '{"project":"ui-mock"}', now, now))
        conn.execute("INSERT INTO jobs VALUES (?, 'design-batch', 'running', 3, 1, 0, NULL, ?, ?, ?)", ("job_uimock_ui", '{"project":"ui-mock"}', now, now))
        conn.execute("INSERT INTO jobs VALUES (?, 'design-batch', 'running', 2, 0, 0, NULL, ?, ?, ?)", ("job_uimock_bg", '{"project":"ui-mock"}', now, now))
        conn.execute("INSERT INTO jobs VALUES (?, 'design-batch', 'completed', 0, 0, 0, NULL, ?, ?, ?)", ("job_uimock_assets", '{"project":"ui-mock"}', now, now))

        tasks = [
            ("job_uimock_char", BATCH_IDS[0], "hero_idle", "character", "done", None, None, 11, "pixelArtDiffusionXL"),
            ("job_uimock_char", BATCH_IDS[0], "hero_run", "character", "done", None, None, 12, "pixelArtDiffusionXL"),
            ("job_uimock_char", BATCH_IDS[0], "hero_attack", "character", "failed", "code:sd_server_error mock failure", None, 13, "dreamshaper_8"),
            ("job_uimock_char", BATCH_IDS[0], "hero_hurt", "character", "processing", None, None, 14, "dreamshaper_8"),
            ("job_uimock_ui", BATCH_IDS[1], "btn_start", "ui", "done", None, None, 21, "pixelArtDiffusionXL"),
            ("job_uimock_ui", BATCH_IDS[1], "btn_exit", "ui", "queued", None, "2099-01-01T00:00:00+00:00", 22, "pixelArtDiffusionXL"),
            ("job_uimock_ui", BATCH_IDS[1], "panel_inv", "ui", "queued", None, "2099-01-01T00:00:00+00:00", 23, "pixelArtDiffusionXL"),
            ("job_uimock_bg", BATCH_IDS[2], "bg_forest_day", "background", "queued", None, "2099-01-01T00:00:00+00:00", 31, "dreamshaper_8"),
            ("job_uimock_bg", BATCH_IDS[2], "bg_forest_night", "background", "queued", None, "2099-01-01T00:00:00+00:00", 32, "dreamshaper_8"),
        ]
        for job_id, batch_id, key, cat, status, err, due, seed, model in tasks:
            conn.execute(
                """
                INSERT INTO generation_tasks (
                    job_id, project, asset_key, category, prompt, negative_prompt, model_name, width, height,
                    steps, cfg, sampler, status, retries, max_retries, last_error, expected_size, max_colors,
                    candidate_slot, candidates_total, next_attempt_at, batch_id, lora_spec_json, seed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, 64, 64, 20, 7.0, 'DPM++ 2M', ?, 1, 3, ?, 64, 32, 0, 1, ?, ?, '[]', ?, ?, ?)
                """,
                (job_id, PROJECT, key, cat, f"mock prompt {key}", model, status, err, due, batch_id, seed, now, now),
            )

        candidates = [
            (BATCH_IDS[0], "job_uimock_char", "hero_idle", 0, "pass", 0, now, "asset_hero_idle", (220, 80, 80, 255), 64),
            (BATCH_IDS[0], "job_uimock_char", "hero_idle", 1, "fail", 0, None, None, (80, 220, 80, 255), 64),
            (BATCH_IDS[0], "job_uimock_char", "hero_idle", 2, "pending", 0, None, None, (80, 80, 220, 255), 64),
            (BATCH_IDS[0], "job_uimock_char", "hero_idle", 3, "pass", 1, None, None, (220, 200, 80, 255), 64),
            (BATCH_IDS[1], "job_uimock_ui", "btn_start", 0, "pass", 0, None, None, (180, 120, 255, 255), 32),
        ]
        for batch_id, job_id, key, slot, vstatus, rejected, picked_at, picked_asset, color, size in candidates:
            path = _img(f"candidates/{PROJECT}/{key}/{job_id}/slot_{slot}.png", color, size=size)
            conn.execute(
                """
                INSERT INTO asset_candidates (
                    project, asset_key, slot_index, job_id, image_path, width, height, color_count,
                    validation_status, validation_message, generation_seed, generation_model, generation_prompt,
                    metadata_json, batch_id, is_rejected, picked_at, picked_asset_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 24, ?, NULL, ?, 'pixelArtDiffusionXL', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    PROJECT, key, slot, job_id, str(path), size, size, vstatus, 1000 + slot, f"mock prompt {key}",
                    json.dumps({"seed": 1000 + slot, "model": "pixelArtDiffusionXL"}), batch_id, rejected, picked_at, picked_asset, now,
                ),
            )

        assets = [
            ("asset_hero_idle", "hero_idle", "character", "approved", (255, 90, 120, 255), 64),
            ("asset_hero_run", "hero_run", "character", "pending", (120, 255, 90, 255), 64),
            ("asset_btn_start", "btn_start", "ui", "approved", (120, 120, 255, 255), 32),
            ("asset_bg_forest_day", "bg_forest_day", "background", "rejected", (100, 180, 120, 255), 64),
        ]
        for aid, key, cat, status, color, size in assets:
            path = _img(f"approved/{PROJECT}/{key}.png", color, size=size)
            conn.execute(
                """
                INSERT INTO assets (
                    id, job_id, project, asset_key, category, status, image_path, width, height, color_count,
                    has_alpha, validation_status, validation_message, generation_seed, generation_model, generation_prompt,
                    metadata_json, created_at, updated_at
                ) VALUES (?, 'job_uimock_assets', ?, ?, ?, ?, ?, ?, ?, 24, 1, ?, NULL, 2026, 'pixelArtDiffusionXL',
                          ?, ?, ?, ?)
                """,
                (
                    aid, PROJECT, key, cat, status, str(path), size, size, "fail" if status == "rejected" else "pass",
                    f"{key} base prompt", json.dumps({"steps": 20, "cfg": 7.0, "max_colors": 32}), now, now,
                ),
            )

        h1 = _img(f"approved/{PROJECT}/hero_idle_v1.png", (200, 60, 90, 255))
        h2 = _img(f"approved/{PROJECT}/hero_idle_v2.png", (170, 50, 120, 255))
        conn.execute(
            """
            INSERT INTO asset_history (
                asset_id, version, job_id, image_path, width, height, color_count, has_alpha, validation_status,
                validation_message, generation_seed, generation_model, generation_prompt, metadata_json, created_at
            ) VALUES ('asset_hero_idle', 1, 'job_uimock_char', ?, 64, 64, 24, 1, 'pass', NULL, 7, 'pixelArtDiffusionXL', 'hero idle old', '{"steps":20}', ?)
            """,
            (str(h1), now),
        )
        conn.execute(
            """
            INSERT INTO asset_history (
                asset_id, version, job_id, image_path, width, height, color_count, has_alpha, validation_status,
                validation_message, generation_seed, generation_model, generation_prompt, metadata_json, created_at
            ) VALUES ('asset_hero_idle', 2, 'job_uimock_char', ?, 64, 64, 24, 1, 'pass', NULL, 8, 'dreamshaper_8', 'hero idle older', '{"steps":24}', ?)
            """,
            (str(h2), now),
        )

        export_dir = EXPORT_ROOT / PROJECT / "character"
        export_dir.mkdir(parents=True, exist_ok=True)
        _png(export_dir / "hero_idle.png", (255, 90, 120, 255), size=64)
        conn.commit()
    finally:
        conn.close()

    print(f"[seed] mock UI data seeded at {DB_PATH}")
    print(f"[seed] project={PROJECT} batches={','.join(BATCH_IDS)}")


if __name__ == "__main__":
    asyncio.run(main())
