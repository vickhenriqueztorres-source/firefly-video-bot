"""Executa um canário isolado para um modelo do Firefly."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from .config import Config
from .job_store import JobStore
from .logging_utils import configure_logging
from .worker import Worker


IMAGE_PROMPT = (
    "Use the first frame exactly. Create a realistic industrial documentary shot with a "
    "subtle camera push-in and physically plausible machinery and atmospheric movement. "
    "Preserve subject geometry, scale, lighting, colors and scene identity. No text. No logos."
)

TEXT_PROMPT = (
    "A carbon-fiber agricultural octocopter flies two and a half meters above a wet soybean "
    "field at midnight, spraying a precise mist between crop rows. Realistic rotor motion, "
    "subtle forward camera tracking, sodium-vapor work lights, sparse cyan telemetry lights, "
    "volumetric ground fog, cinematic 35mm anamorphic industrial documentary photography, "
    "deep carbon blacks, no text, no logos, no people."
)


def _insert_canary(config: Config, job_id: int, model: str, duration: int, image_path: Path | None, name: str, resolution: str) -> None:
    conn = sqlite3.connect(config.db_path)
    try:
        now = time.time()
        with conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.execute(
                """
                INSERT INTO jobs (
                    id, prompt, image_path, status, attempts, updated_at, model,
                    resolution, aspect_ratio, duration_seconds, generate_audio, name
                ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, '16:9', ?, 0, ?)
                """,
                (
                    job_id,
                    IMAGE_PROMPT if image_path else TEXT_PROMPT,
                    str(image_path.resolve()) if image_path else None,
                    now,
                    model,
                    resolution,
                    duration,
                    name,
                ),
            )
            conn.execute(
                "UPDATE system_state SET status = ?, reason = NULL, updated_at = ? WHERE singleton = 1",
                ("running", now),
            )
    finally:
        conn.close()


def _read_job(config: Config, job_id: int) -> dict[str, object]:
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, name, status, attempts, model, duration_seconds, error, output_path FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else {"id": job_id, "status": "MISSING"}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m firefly_bot.model_canary")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--model", required=True)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--resolution", choices=("720p", "1080p"), default="1080p")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--job-id", type=int, default=-9001)
    parser.add_argument("--name", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.model == "Kling 2.5 Turbo" and not args.image:
        raise SystemExit("MODEL_CANARY_IMAGE_REQUIRED: Kling 2.5 Turbo exige --image com primeiro quadro 16:9.")

    configure_logging()
    config = Config.from_root(args.root.resolve())
    name = args.name or f"MODEL_CANARY_{args.model.upper().replace(' ', '_').replace('.', '_')}"
    _insert_canary(config, args.job_id, args.model, args.duration, args.image, name, args.resolution)
    logger = logging.getLogger("firefly_bot.model_canary")
    store = JobStore(config.db_path)
    store.initialize()
    exit_code = asyncio.run(Worker(config, store, logger).run_batch(1))
    row = _read_job(config, args.job_id)
    result = {
        "schema": "firefly.model-canary.v1",
        "model": args.model,
        "duration_seconds": args.duration,
        "resolution": args.resolution,
        "worker_exit_code": exit_code,
        "job": row,
        "captured_at": time.time(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if row.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
