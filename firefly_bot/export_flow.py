"""Export the ready Firefly result using browser and filesystem evidence."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from patchright.async_api import TimeoutError as PatchrightTimeoutError

from .config import Config
from .downloads import ValidatedDownload, validate_and_move_download
from .human_input import HumanInput
from .logging_utils import event
from .overlays import dismiss_overlays
from .selectors import locator_for


MEDIA_SUFFIXES = {".mp4", ".mov", ".webm"}
TEMP_SUFFIXES = {".part", ".crdownload", ".tmp", ".download"}


class ExportState(StrEnum):
    RESULT_READY = "result_ready"
    DOWNLOAD_CLICKED = "download_clicked"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETE = "download_complete"


class ExportFlowError(RuntimeError):
    pass


class DownloadResultAmbiguous(ExportFlowError):
    pass


class DownloadPathOutsideAllowedRoot(ExportFlowError):
    pass


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class DownloadCandidate:
    path: Path
    suggested_filename: str
    source: str


async def export_video(
    page: object,
    human_input: HumanInput,
    config: Config,
    logger: logging.Logger,
    job_id: int,
) -> ValidatedDownload:
    """Export the visible RESULT_READY media without relying only on download events."""
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    state = ExportState.RESULT_READY
    await dismiss_overlays(page, human_input, logger, job_id)
    download_button = locator_for(page, "download_button")
    await _confirm_download_button_ready(download_button, config)
    before = snapshot_download_dir(config.downloads_dir)
    export_started_at = time.time()
    event(
        logger,
        logging.INFO,
        "export_preflight",
        job_id=job_id,
        export_state=state.value,
        downloads_dir=str(config.downloads_dir),
        before_files=len(before),
        locator="download_button",
    )
    await _safe_screenshot(page, config.downloads_dir / f"job_{job_id}_before_download_click.png")

    candidate = await _click_and_resolve_candidate(
        page=page,
        human_input=human_input,
        button=download_button,
        config=config,
        logger=logger,
        job_id=job_id,
        before=before,
        export_started_at=export_started_at,
    )
    state = ExportState.DOWNLOAD_STARTED
    event(
        logger,
        logging.INFO,
        "export_transition",
        job_id=job_id,
        export_state=state.value,
        detection_source=candidate.source,
        candidate=str(candidate.path),
    )

    validated = _validate_candidate(candidate, config, job_id)
    state = ExportState.DOWNLOAD_COMPLETE
    event(
        logger,
        logging.INFO,
        "export_transition",
        job_id=job_id,
        export_state=state.value,
        output_path=str(validated.path),
        file_size_bytes=validated.file_size_bytes,
        sha256=validated.sha256,
    )
    return validated


async def export_video_from_provider_url(
    page: object,
    provider_url: str,
    config: Config,
    logger: logging.Logger,
    job_id: int,
) -> ValidatedDownload:
    """Baixa o MP4 nativo quando o backend termina mas a UI mantém o spinner."""
    config.downloads_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = config.downloads_dir / f".{job_id}_{time.time_ns()}.provider.mp4"
    event(
        logger,
        logging.INFO,
        "provider_api_download_started",
        job_id=job_id,
        source="firefly_provider_result",
    )
    response = await page.request.get(
        provider_url,
        timeout=config.download_completion_timeout_ms,
        fail_on_status_code=False,
    )
    if not response.ok:
        raise ExportFlowError(
            f"FIREFLY_PROVIDER_DOWNLOAD_HTTP_{response.status}"
        )
    payload = await response.body()
    temporary_path.write_bytes(payload)
    validated = validate_and_move_download(
        temporary_path=temporary_path,
        downloads_dir=config.downloads_dir,
        job_id=job_id,
        suggested_filename=f"firefly_job_{job_id}.mp4",
        min_file_size_bytes=config.min_file_size_bytes,
    )
    event(
        logger,
        logging.INFO,
        "provider_api_download_validated",
        job_id=job_id,
        output_path=str(validated.path),
        file_size_bytes=validated.file_size_bytes,
        width=validated.width,
        height=validated.height,
        duration_seconds=validated.duration_seconds,
        codec=validated.codec,
    )
    return validated


async def _click_and_resolve_candidate(
    *,
    page: object,
    human_input: HumanInput,
    button: object,
    config: Config,
    logger: logging.Logger,
    job_id: int,
    before: dict[Path, FileSnapshot],
    export_started_at: float,
) -> DownloadCandidate:
    event(logger, logging.INFO, "download_click_attempt", job_id=job_id, selector="download_button")
    candidate = await _click_with_short_download_event(
        page=page,
        human_input=human_input,
        target=button,
        config=config,
        logger=logger,
        job_id=job_id,
        click_description="primary_download_button",
    )
    if candidate is not None:
        return candidate

    menu_candidate = await _try_menu_or_popover_option(
        page, human_input, config, logger, job_id
    )
    if menu_candidate is not None:
        return menu_candidate

    event(logger, logging.INFO, "download_event_absent_filesystem_watch_continues", job_id=job_id)
    return await wait_for_filesystem_candidate(
        downloads_dir=config.downloads_dir,
        before=before,
        export_started_at=export_started_at,
        timeout_ms=config.download_completion_timeout_ms,
        stability_checks=config.download_stability_checks,
        stability_delay_seconds=config.download_stability_delay_seconds,
        job_id=job_id,
        logger=logger,
    )


async def _click_with_short_download_event(
    *,
    page: object,
    human_input: HumanInput,
    target: object,
    config: Config,
    logger: logging.Logger,
    job_id: int,
    click_description: str,
) -> DownloadCandidate | None:
    try:
        async with page.expect_download(timeout=config.download_start_timeout_ms) as download_info:
            await human_input.click(target)
        download = await download_info.value
    except PatchrightTimeoutError:
        event(
            logger,
            logging.WARNING,
            "download_event_timeout",
            job_id=job_id,
            timeout_ms=config.download_start_timeout_ms,
            click_description=click_description,
        )
        return None
    suggested = Path(download.suggested_filename).name or f"job_{job_id}.mp4"
    temp_path = config.downloads_dir / f".{job_id}_{suggested}.event.part"
    await download.save_as(str(temp_path))
    event(
        logger,
        logging.INFO,
        "download_event_received",
        job_id=job_id,
        suggested_filename=suggested,
        temporary_path=str(temp_path),
    )
    return DownloadCandidate(temp_path, suggested, "browser_download_event")


async def _try_menu_or_popover_option(
    page: object,
    human_input: HumanInput,
    config: Config,
    logger: logging.Logger,
    job_id: int,
) -> DownloadCandidate | None:
    option = page.get_by_role("menuitem", name=_download_option_regex()).first
    try:
        await option.wait_for(state="visible", timeout=config.ui_response_timeout_ms)
    except PatchrightTimeoutError:
        option = page.get_by_role("button", name=_download_option_regex()).first
        try:
            await option.wait_for(state="visible", timeout=config.ui_response_timeout_ms)
        except PatchrightTimeoutError:
            event(logger, logging.INFO, "download_menu_not_detected", job_id=job_id)
            return None
    label = await _locator_text(option)
    event(logger, logging.INFO, "download_menu_option_detected", job_id=job_id, label=label)
    return await _click_with_short_download_event(
        page=page,
        human_input=human_input,
        target=option,
        config=config,
        logger=logger,
        job_id=job_id,
        click_description=f"menu_option:{label}",
    )


def _download_option_regex() -> object:
    import re

    return re.compile(r"\b(download|baixar|mp4|video|vídeo)\b", re.IGNORECASE)


async def _locator_text(locator: object) -> str:
    try:
        value = await locator.text_content(timeout=1000)
        return (value or "").strip()
    except Exception:
        return ""


async def _confirm_download_button_ready(download_button: object, config: Config) -> None:
    await download_button.wait_for(state="visible", timeout=config.ui_response_timeout_ms)
    disabled = await download_button.get_attribute("aria-disabled")
    if disabled == "true":
        raise ExportFlowError("RESULT_READY_DOWNLOAD_BUTTON_DISABLED")
    enabled_attr = await download_button.get_attribute("disabled")
    if enabled_attr is not None:
        raise ExportFlowError("RESULT_READY_DOWNLOAD_BUTTON_DISABLED")


async def _safe_screenshot(page: object, output_path: Path) -> None:
    try:
        await page.screenshot(path=str(output_path), full_page=True)
    except Exception:
        return


def snapshot_download_dir(downloads_dir: Path) -> dict[Path, FileSnapshot]:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    snapshot: dict[Path, FileSnapshot] = {}
    for path in downloads_dir.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        snapshot[path.resolve()] = FileSnapshot(size=stat.st_size, mtime=stat.st_mtime)
    return snapshot


async def wait_for_filesystem_candidate(
    *,
    downloads_dir: Path,
    before: dict[Path, FileSnapshot],
    export_started_at: float,
    timeout_ms: int,
    stability_checks: int,
    stability_delay_seconds: float,
    job_id: int,
    logger: logging.Logger,
) -> DownloadCandidate:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        candidates = _new_or_modified_media_candidates(downloads_dir, before, export_started_at)
        stable = [
            candidate
            for candidate in candidates
            if await _is_stable(candidate, checks=stability_checks, delay_seconds=stability_delay_seconds)
        ]
        if len(stable) == 1:
            path = stable[0]
            event(logger, logging.INFO, "filesystem_download_detected", job_id=job_id, path=str(path))
            return DownloadCandidate(path, path.name, "filesystem_download_detection")
        if len(stable) > 1:
            raise DownloadResultAmbiguous(
                "DOWNLOAD_RESULT_AMBIGUOUS: "
                + ", ".join(str(path) for path in stable)
            )
        await asyncio.sleep(0.5)
    raise ExportFlowError("DOWNLOAD_COMPLETION_TIMEOUT")


def _new_or_modified_media_candidates(
    downloads_dir: Path, before: dict[Path, FileSnapshot], export_started_at: float
) -> list[Path]:
    root = downloads_dir.resolve()
    candidates: list[Path] = []
    for path in downloads_dir.iterdir():
        if not path.is_file():
            continue
        resolved = path.resolve()
        _ensure_inside_download_root(resolved, root)
        suffix = path.suffix.lower()
        if suffix in TEMP_SUFFIXES or suffix not in MEDIA_SUFFIXES:
            continue
        stat = path.stat()
        old = before.get(resolved)
        changed = old is None or old.size != stat.st_size or old.mtime != stat.st_mtime
        if changed and stat.st_size > 0 and stat.st_mtime >= export_started_at - 1:
            candidates.append(resolved)
    return sorted(candidates, key=lambda item: item.stat().st_mtime)


async def _is_stable(path: Path, *, checks: int, delay_seconds: float) -> bool:
    previous = -1
    stable_samples = 0
    for _ in range(checks + 1):
        if not path.is_file() or path.suffix.lower() in TEMP_SUFFIXES:
            return False
        size = path.stat().st_size
        if size <= 0:
            return False
        if size == previous:
            stable_samples += 1
        else:
            stable_samples = 0
            previous = size
        if stable_samples >= checks:
            return True
        await asyncio.sleep(delay_seconds)
    return False


def _validate_candidate(candidate: DownloadCandidate, config: Config, job_id: int) -> ValidatedDownload:
    root = config.downloads_dir.resolve()
    candidate_path = candidate.path.resolve()
    _ensure_inside_download_root(candidate_path, root)
    suggested = Path(candidate.suggested_filename).name or candidate_path.name
    temp_path = root / f".{job_id}_{suggested}.incoming.part"
    if candidate_path != temp_path:
        if temp_path.exists():
            temp_path.unlink()
        candidate_path.replace(temp_path)
    return validate_and_move_download(
        temporary_path=temp_path,
        downloads_dir=config.downloads_dir,
        job_id=job_id,
        suggested_filename=suggested,
        min_file_size_bytes=config.min_file_size_bytes,
    )


def _ensure_inside_download_root(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise DownloadPathOutsideAllowedRoot(
            f"DOWNLOAD_PATH_OUTSIDE_ALLOWED_ROOT: {path}"
        ) from exc


def has_relevant_temp_files(downloads_dir: Path, names: Iterable[str]) -> list[Path]:
    stems = {Path(name).stem for name in names}
    return [
        path
        for path in downloads_dir.iterdir()
        if path.is_file() and path.suffix.lower() in TEMP_SUFFIXES and Path(path.name).stem in stems
    ]
