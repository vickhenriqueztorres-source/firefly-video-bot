from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import pytest

import firefly_bot.worker as worker_module
from firefly_bot.config import Config
from firefly_bot.job_store import Job
from firefly_bot.worker import Worker


class FakeStore:
    def transition(self, *_args: object, **_kwargs: object) -> None:
        return None


class FakeKeyboard:
    async def press(self, _key: str) -> None:
        return None


class FakePage:
    keyboard = FakeKeyboard()


class FakeHuman:
    def __init__(self, _page: object) -> None:
        pass

    async def click(self, _locator: object) -> None:
        return None

    async def type_prompt(self, *_args: object, **_kwargs: object) -> None:
        return None


def test_onboarding_is_dismissed_before_first_frame_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def record(name: str) -> None:
        calls.append(name)

    async def dismiss(*_args: object, **_kwargs: object) -> None:
        await record("dismiss")

    monkeypatch.setattr(worker_module, "HumanInput", FakeHuman)
    monkeypatch.setattr(worker_module, "dismiss_overlays", dismiss)
    monkeypatch.setattr(worker_module, "locator_for", lambda *_args: object())
    monkeypatch.setattr(Worker, "_open_video_generation", lambda self: record("open"))
    monkeypatch.setattr(Worker, "_configure_model", lambda self, value: record(f"model:{value}"))
    monkeypatch.setattr(Worker, "_configure_resolution", lambda self, value: record(f"resolution:{value}"))
    monkeypatch.setattr(Worker, "_configure_aspect_ratio", lambda self, value: record(f"aspect:{value}"))
    monkeypatch.setattr(Worker, "_verify_frame_rate", lambda self, value: record(f"fps:{value}"))
    monkeypatch.setattr(Worker, "_configure_audio", lambda self, value: record(f"audio:{value}"))
    monkeypatch.setattr(Worker, "_upload_first_frame", lambda self, value: record("upload"))
    monkeypatch.setattr(Worker, "_configure_duration", lambda self, seconds, model: record(f"duration:{seconds}"))
    monkeypatch.setattr(Worker, "_ensure_single_capture_mode", lambda self, job, page: record("single"))
    monkeypatch.setattr(Worker, "_capture_pre_generation_snapshot", lambda self, job, page: record("snapshot"))
    monkeypatch.setattr(Worker, "_install_provider_network_diagnostics", lambda self, job, page: None)
    monkeypatch.setattr(Worker, "_audit_credit_spend", lambda self, job, page: record("credit"))
    monkeypatch.setattr(Worker, "_click_generate_and_confirm", lambda self, job, page, human: record("generate"))

    worker = Worker(
        Config.from_root(tmp_path),
        FakeStore(),  # type: ignore[arg-type]
        logging.getLogger("overlay-order-test"),
    )
    job = Job(
        id=1,
        prompt="move subtly",
        image_path=str(tmp_path / "frame.png"),
        status="claimed",
        attempts=1,
        output_path=None,
        error=None,
        claimed_at=time.time(),
        generation_started_at=None,
        updated_at=time.time(),
        model="Kling 2.5 Turbo",
        resolution="1080p",
        aspect_ratio="16:9",
        duration_seconds=5,
        name="TEST",
        download_started_at=None,
        download_completed_at=None,
        media_validated_at=None,
        media_validation_status=None,
        media_validation_error=None,
        file_size_bytes=None,
        sha256=None,
        width=None,
        height=None,
        codec=None,
        generate_audio=False,
    )

    asyncio.run(worker._start_generation(job, FakePage()))

    assert calls.index("dismiss") < calls.index("upload")
    assert calls.index("model:Kling 2.5 Turbo") < calls.index("upload")
    assert calls.index("fps:24") < calls.index("upload")
