from __future__ import annotations

import json
from pathlib import Path

import pytest

from firefly_bot.job_store import JobStore
from firefly_bot.mateo_execution_contract import (
    MateoExecutionContractError,
    enqueue_execution_package,
)


def write_package(tmp_path: Path, **overrides: object) -> Path:
    frame = tmp_path / "approved.png"
    frame.write_bytes(b"image")
    prompt = "Mateo slowly moves his hand, then holds still. Static camera."
    package = {
        "status": "MOTION_PACKAGE_READY", "agent": "mateo-kling-motion-prompt-engineer",
        "shot_id": "mateo-permission-control", "approved_start_frame": frame.name,
        "technical_prompt": prompt,
        "prompt_characters": len(prompt),
        "negative_prompt": "identity drift",
        "config": {
            "aspect_ratio": "9:16",
            "generation_duration_seconds": 5,
            "target_motion_seconds": 1.8,
            "motion_strength": 3,
            "camera": "static",
        },
        "source_of_motion": "STORYBOARD_ONLY", "external_video_analysis_used": False,
        "next_agent": "kling_or_firefly_executor",
    }
    package.update(overrides)
    path = tmp_path / "motion-package.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    return path


def test_executor_enqueues_exact_approved_contract(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    assert enqueue_execution_package(store, write_package(tmp_path)) == 1
    job = store.list_jobs()[0]
    assert job.name == "mateo-permission-control"
    assert job.duration_seconds == 5
    assert job.aspect_ratio == "9:16"
    assert "slowly moves his hand" in job.prompt


def test_executor_rejects_unapproved_or_external_motion(tmp_path: Path) -> None:
    with pytest.raises(MateoExecutionContractError):
        enqueue_execution_package(
            JobStore(tmp_path / "bad.db"),
            write_package(tmp_path, external_video_analysis_used=True),
        )
