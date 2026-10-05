"""Strict adapter from an approved Mateo motion package to one Firefly job.

This module only validates and enqueues. It makes no creative decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .job_store import JobStore


class MateoExecutionContractError(ValueError):
    pass


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or any(part in name for part in ("/", "\\", "..")):
        raise MateoExecutionContractError("unsafe or empty shot_id")
    return name


def load_execution_package(package_path: Path) -> dict[str, Any]:
    path = package_path.resolve()
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MateoExecutionContractError(f"invalid JSON: {exc}") from exc
    if not isinstance(package, dict):
        raise MateoExecutionContractError("package root must be an object")
    required = {
        "status": "MOTION_PACKAGE_READY",
        "agent": "mateo-kling-motion-prompt-engineer",
        "source_of_motion": "STORYBOARD_ONLY",
        "external_video_analysis_used": False,
        "next_agent": "kling_or_firefly_executor",
    }
    for key, expected in required.items():
        if package.get(key) != expected:
            raise MateoExecutionContractError(f"{key} must be {expected!r}")
    prompt = str(package.get("technical_prompt", "")).strip()
    if not prompt or len(prompt) > 1500 or package.get("prompt_characters") != len(prompt):
        raise MateoExecutionContractError(
            "technical_prompt is empty, oversized or has an invalid character count"
        )
    config = package.get("config")
    if not isinstance(config, dict):
        raise MateoExecutionContractError("config is required")
    expected_config = {
        "aspect_ratio": "9:16",
        "generation_duration_seconds": 5,
        "motion_strength": 3,
        "camera": "static",
    }
    for key, expected in expected_config.items():
        if config.get(key) != expected:
            raise MateoExecutionContractError(f"config.{key} must be {expected!r}")
    target = config.get("target_motion_seconds")
    if not isinstance(target, (int, float)) or not 1 <= float(target) <= 5:
        raise MateoExecutionContractError("config.target_motion_seconds must be between 1 and 5")
    image_value = str(package.get("approved_start_frame", "")).strip()
    image_path = Path(image_value)
    image = (
        image_path.resolve()
        if image_path.is_absolute()
        else (path.parent / image_path).resolve()
    )
    if not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise MateoExecutionContractError(f"approved start frame is missing or invalid: {image}")
    return {"package": package, "image": image, "shot_id": _safe_name(package.get("shot_id"))}


def enqueue_execution_package(store: JobStore, package_path: Path) -> int:
    contract = load_execution_package(package_path)
    package = contract["package"]
    return store.add_mateo_execution_job(
        prompt=package["technical_prompt"],
        image_path=contract["image"],
        name=contract["shot_id"],
    )
