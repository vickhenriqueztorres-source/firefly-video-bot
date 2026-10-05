from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

import pytest

import firefly_bot.export_flow as export_flow
import firefly_bot.worker as worker_module
from firefly_bot.config import Config
from firefly_bot.downloads import ValidatedDownload
from firefly_bot.export_flow import (
    DownloadPathOutsideAllowedRoot,
    DownloadResultAmbiguous,
    snapshot_download_dir,
    wait_for_filesystem_candidate,
)
from firefly_bot.job_store import JobStore
from firefly_bot.worker import WORKER_SUCCESS, Worker


class _Locator:
    async def wait_for(self, **_kwargs: object) -> None:
        return None

    async def get_attribute(self, _name: str) -> None:
        return None


class _Human:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self, _target: object) -> None:
        self.clicks += 1


class _Download:
    suggested_filename = "result.mp4"

    async def save_as(self, path: str) -> None:
        Path(path).write_bytes(b"mp4-bytes")


class _DownloadInfo:
    @property
    async def value(self) -> _Download:
        return _Download()


class _ExpectDownload:
    async def __aenter__(self) -> _DownloadInfo:
        return _DownloadInfo()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _PageWithDownload:
    def expect_download(self, **_kwargs: object) -> _ExpectDownload:
        return _ExpectDownload()

    async def screenshot(self, **_kwargs: object) -> None:
        return None


class _PageWithoutMenu:
    def get_by_role(self, *_args: object, **_kwargs: object) -> object:
        class _Missing:
            first = _Locator()

        return _Missing()


class _ProviderResponse:
    ok = True
    status = 200

    async def body(self) -> bytes:
        return b"provider-video-bytes"


class _ProviderRequest:
    def __init__(self) -> None:
        self.requested_url: str | None = None

    async def get(self, url: str, **_kwargs: object) -> _ProviderResponse:
        self.requested_url = url
        return _ProviderResponse()


class _PageWithProviderResult:
    def __init__(self) -> None:
        self.request = _ProviderRequest()


def _validated(path: Path) -> ValidatedDownload:
    return ValidatedDownload(
        path=path,
        file_size_bytes=123456,
        sha256="a" * 64,
        width=720,
        height=1280,
        duration_seconds=5.0,
        codec="h264",
        ffprobe={},
    )


def test_expect_download_event_path_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config.from_root(tmp_path)
    human = _Human()

    monkeypatch.setattr(export_flow, "locator_for", lambda _page, _key: _Locator())
    monkeypatch.setattr(export_flow, "dismiss_overlays", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(export_flow, "validate_and_move_download", lambda temporary_path, **_kwargs: _validated(temporary_path))

    result = asyncio.run(
        export_flow.export_video(
            _PageWithDownload(),
            human,  # type: ignore[arg-type]
            config,
            logging.getLogger("test-export-event"),
            1,
        )
    )

    assert human.clicks == 1
    assert result.path.name.endswith(".incoming.part")


def test_provider_result_url_download_is_physically_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config.from_root(tmp_path)
    page = _PageWithProviderResult()
    observed: dict[str, object] = {}

    def fake_validate(temporary_path: Path, **kwargs: object) -> ValidatedDownload:
        observed["bytes"] = temporary_path.read_bytes()
        observed.update(kwargs)
        return _validated(temporary_path)

    monkeypatch.setattr(export_flow, "validate_and_move_download", fake_validate)
    result = asyncio.run(
        export_flow.export_video_from_provider_url(
            page,  # type: ignore[arg-type]
            "https://provider.invalid/signed-result",
            config,
            logging.getLogger("test-provider-result"),
            9,
        )
    )

    assert page.request.requested_url == "https://provider.invalid/signed-result"
    assert observed["bytes"] == b"provider-video-bytes"
    assert observed["job_id"] == 9
    assert result.file_size_bytes == 123456


def test_filesystem_detection_passes_when_download_event_never_fires(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    before = snapshot_download_dir(downloads)
    candidate = downloads / "direct.mp4"
    candidate.write_bytes(b"x" * 10)
    old = time.time() - 1

    result = asyncio.run(
        wait_for_filesystem_candidate(
            downloads_dir=downloads,
            before=before,
            export_started_at=old,
            timeout_ms=3000,
            stability_checks=1,
            stability_delay_seconds=0.01,
            job_id=2,
            logger=logging.getLogger("test-fs-detection"),
        )
    )

    assert result.path == candidate.resolve()
    assert result.source == "filesystem_download_detection"


def test_ambiguous_filesystem_candidates_are_blocked(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    before = snapshot_download_dir(downloads)
    started = time.time() - 1
    (downloads / "a.mp4").write_bytes(b"a")
    (downloads / "b.mp4").write_bytes(b"b")

    with pytest.raises(DownloadResultAmbiguous):
        asyncio.run(
            wait_for_filesystem_candidate(
                downloads_dir=downloads,
                before=before,
                export_started_at=started,
                timeout_ms=3000,
                stability_checks=1,
                stability_delay_seconds=0.01,
                job_id=3,
                logger=logging.getLogger("test-fs-ambiguous"),
            )
        )


def test_download_path_outside_allowed_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")

    with pytest.raises(DownloadPathOutsideAllowedRoot):
        export_flow._ensure_inside_download_root(outside, tmp_path / "downloads")  # noqa: SLF001


def test_result_ready_recovery_does_not_start_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config.from_root(tmp_path)
    store = JobStore(config.db_path)
    store.initialize()
    with sqlite3.connect(config.db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
              prompt, image_path, status, attempts, updated_at, generation_started_at,
              model, resolution, aspect_ratio, duration_seconds, name, error
            ) VALUES (?, ?, 'failed-infra', 1, ?, ?, 'Kling 3.0', '720p', '9:16', 5, 'SHOT_001_TAKE_01', ?)
            """,
            ("prompt", "image.png", time.time(), time.time(), "DOWNLOAD_EVENT_TIMEOUT"),
        )
    worker = Worker(config, store, logging.getLogger("test-recovery"))

    async def fail_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Generate/start must not run during result-ready recovery")

    async def fake_recover(_self: Worker, _job: object, _page: object) -> int:
        return WORKER_SUCCESS

    class _Context:
        pages: list[object] = []

        async def add_init_script(self, _script: str) -> None:
            return None

        async def new_page(self) -> object:
            return object()

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch_persistent_context(self, **_kwargs: object) -> _Context:
            return _Context()

    class _Playwright:
        chromium = _Chromium()

    class _Manager:
        async def __aenter__(self) -> _Playwright:
            return _Playwright()

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(Worker, "_start_generation", fail_start)
    monkeypatch.setattr(Worker, "_recover_result_ready_page", fake_recover)
    monkeypatch.setattr(worker_module, "close_existing_profile_chrome", lambda _path: [])
    monkeypatch.setattr(worker_module, "async_playwright", lambda: _Manager())

    assert asyncio.run(worker.recover_result_ready_job(1)) == WORKER_SUCCESS
