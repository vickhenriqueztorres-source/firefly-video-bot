"""Read confirmed Firefly UI states; UNKNOWN never means success."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from patchright.async_api import Error as PatchrightError
from patchright.async_api import TimeoutError as PatchrightTimeoutError

from .config import Config
from .logging_utils import event


class ScreenState(Enum):
    LOGGED_OUT = "logged_out"
    QUOTA_EXHAUSTED = "quota_exhausted"
    CONTENT_REJECTED = "content_rejected"
    ERROR_TOAST = "error_toast"
    RESULT_READY = "result_ready"
    STILL_GENERATING = "still_generating"
    UNKNOWN = "unknown"


class PageLike(Protocol):
    url: str

    async def screenshot(self, *, path: str, full_page: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class ScreenObservation:
    """State plus the selector evidence used to reach it."""

    state: ScreenState
    screenshot_path: Path | None = None
    url: str | None = None
    selectors_found: tuple[str, ...] = ()
    selectors_absent: tuple[str, ...] = ()
    selector_errors: tuple[dict[str, str], ...] = ()
    read_duration_ms: float = 0.0


async def read_screen_state(
    page: PageLike,
    config: Config | None = None,
    logger: logging.Logger | None = None,
) -> ScreenState:
    """Return only a positively visible state, in priority order."""
    observation = await inspect_screen_state(page, config=config, logger=logger)
    return observation.state


async def inspect_screen_state(
    page: PageLike,
    config: Config | None = None,
    logger: logging.Logger | None = None,
) -> ScreenObservation:
    """Return state and selector-level diagnostics for audit evidence."""
    from .selectors import STATE_PRIORITY, STATE_SELECTORS, locator_for

    active_config = config or Config()
    active_logger = logger or logging.getLogger("firefly_bot.state_reader")
    started = time.perf_counter()
    selectors_found: list[str] = []
    selectors_absent: list[str] = []
    selector_errors: list[dict[str, str]] = []

    for state in STATE_PRIORITY:
        definition = STATE_SELECTORS[state]
        if not definition.confirmed:
            continue
        try:
            if definition.method == "url_pattern":
                if re.search(definition.value, page.url, flags=re.IGNORECASE):
                    selectors_found.append(state.value)
                    return _observation(
                        state, page, started, selectors_found, selectors_absent, selector_errors
                    )
                selectors_absent.append(state.value)
                continue

            locator = locator_for(page, state.value)
            if await locator.is_visible(timeout=min(500, active_config.SELECTOR_TIMEOUT)):
                selectors_found.append(state.value)
                return _observation(
                    state, page, started, selectors_found, selectors_absent, selector_errors
                )
            selectors_absent.append(state.value)
        except (PatchrightTimeoutError, PatchrightError) as exc:
            selector_errors.append({"selector_key": state.value, "error": type(exc).__name__})
            event(
                active_logger,
                logging.WARNING,
                "state_selector_error",
                selector_key=state.value,
                error=type(exc).__name__,
            )

    return _observation(
        ScreenState.UNKNOWN, page, started, selectors_found, selectors_absent, selector_errors
    )


def _observation(
    state: ScreenState,
    page: PageLike,
    started: float,
    selectors_found: list[str],
    selectors_absent: list[str],
    selector_errors: list[dict[str, str]],
) -> ScreenObservation:
    return ScreenObservation(
        state,
        url=page.url,
        selectors_found=tuple(selectors_found),
        selectors_absent=tuple(selectors_absent),
        selector_errors=tuple(selector_errors),
        read_duration_ms=(time.perf_counter() - started) * 1000,
    )


class StateReader:
    """Thin adapter that can emit poll diagnostics during RC investigations."""

    def __init__(
        self,
        page: PageLike,
        screenshots_dir: Path,
        logger: logging.Logger,
        config: Config | None = None,
    ):
        self.page = page
        self.screenshots_dir = screenshots_dir
        self.logger = logger
        self.config = config or Config()

    async def read_screen_state(self, job_id: int | None = None) -> ScreenObservation:
        observation = await inspect_screen_state(self.page, config=self.config, logger=self.logger)
        event(
            self.logger,
            logging.INFO,
            "screen_state_read",
            job_id=job_id,
            state=observation.state.value,
            url=observation.url,
            selectors_found=",".join(observation.selectors_found),
            selectors_absent=",".join(observation.selectors_absent),
            read_duration_ms=round(observation.read_duration_ms, 1),
        )
        await self._write_poll_diagnostic(job_id, observation)
        return observation

    async def read_dom_state_fast(self, job_id: int | None = None) -> ScreenObservation:
        """Fallback for locator stalls: inspect the DOM directly for terminal states."""
        started = time.perf_counter()
        payload = await self.page.evaluate(
            """() => {
                const seen = new Set();
                const visibleTexts = [];
                let download = null;
                let generate = null;
                const videos = [];
                const generatedCandidates = [];

                function isVisible(el) {
                    if (!el || !el.getBoundingClientRect) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return (rect.width > 0 || rect.height > 0) && style.visibility !== 'hidden' && style.display !== 'none';
                }

                function walk(root) {
                    if (!root || seen.has(root)) return;
                    seen.add(root);
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        if (!download && el.getAttribute && el.getAttribute('data-testid') === 'generate-video-download-button') download = el;
                        if (!generate && el.getAttribute && el.getAttribute('data-testid') === 'video-generation-generate-button') generate = el;
                        const testId = el.getAttribute && el.getAttribute('data-testid');
                        if (el.tagName === 'VIDEO') {
                            videos.push({
                                src: el.currentSrc || el.src || el.getAttribute('src') || '',
                                poster: el.getAttribute('poster') || '',
                                visible: isVisible(el)
                            });
                        }
                        if (testId && /history|thumbnail|generation|result|download/i.test(testId)) {
                            generatedCandidates.push({
                                tag: el.tagName,
                                testId,
                                text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 500),
                                visible: isVisible(el),
                                disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'
                            });
                        }
                        if (isVisible(el)) {
                            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (text) visibleTexts.push(text);
                        }
                        if (el.shadowRoot) walk(el.shadowRoot);
                    }
                }

                walk(document);
                const bodyText = [document.body ? document.body.innerText : '', ...visibleTexts].join('\\n');
                const downloadVisible = !!download && !!(download.offsetWidth || download.offsetHeight || download.getClientRects().length);
                const downloadDisabled = !download || download.hasAttribute('disabled') || download.getAttribute('aria-disabled') === 'true';
                const generateVisible = !!generate && !!(generate.offsetWidth || generate.offsetHeight || generate.getClientRects().length);
                const generateDisabled = !generate || generate.hasAttribute('disabled') || generate.getAttribute('aria-disabled') === 'true';
                const generateText = generate ? (generate.textContent || generate.getAttribute('aria-label') || '') : '';
                return {
                    url: location.href,
                    bodyText,
                    downloadVisible,
                    downloadDisabled,
                    generateVisible,
                    generateDisabled,
                    generateText,
                    videoCount: videos.length,
                    videos,
                    generatedCandidates
                };
            }"""
        )
        body_text = str(payload.get("bodyText") or "").lower()
        selectors_found: list[str] = []
        selectors_absent: list[str] = []
        if "não foi possível processar esse prompt" in body_text:
            state = ScreenState.CONTENT_REJECTED
            selectors_found.append("dom_fallback:content_rejected")
        elif (
            "ocorreu um erro" in body_text
            or "tente novamente mais tarde" in body_text
            or "something went wrong" in body_text
            or "try again later" in body_text
        ):
            state = ScreenState.ERROR_TOAST
            selectors_found.append("dom_fallback:error_toast")
        elif (
            "quota esgotada" in body_text
            or "créditos esgotados" in body_text
            or "credito esgotado" in body_text
            or "crédito esgotado" in body_text
            or "saldo insuficiente" in body_text
            or "insufficient credits" in body_text
            or "not enough credits" in body_text
            or "out of credits" in body_text
        ):
            state = ScreenState.QUOTA_EXHAUSTED
            selectors_found.append("dom_fallback:quota_exhausted")
        elif payload.get("downloadVisible") and not payload.get("downloadDisabled"):
            state = ScreenState.RESULT_READY
            selectors_found.append("dom_fallback:result_ready_download_button")
        elif "gerando vídeo" in body_text or "generating video" in body_text:
            state = ScreenState.STILL_GENERATING
            selectors_found.append("dom_fallback:active_generation_text")
        elif (
            payload.get("downloadVisible")
            and payload.get("downloadDisabled")
            and payload.get("generateVisible")
            and not payload.get("generateDisabled")
        ):
            state = ScreenState.UNKNOWN
            selectors_found.append("dom_fallback:idle_generate_button_active")
        elif payload.get("downloadVisible") and payload.get("downloadDisabled"):
            state = ScreenState.STILL_GENERATING
            selectors_found.append("dom_fallback:still_generating_disabled_download_button")
        else:
            state = ScreenState.UNKNOWN
            selectors_absent.append("dom_fallback:download_button")

        observation = ScreenObservation(
            state,
            url=str(payload.get("url") or ""),
            selectors_found=tuple(selectors_found),
            selectors_absent=tuple(selectors_absent),
            read_duration_ms=(time.perf_counter() - started) * 1000,
        )
        event(
            self.logger,
            logging.INFO,
            "screen_state_dom_fallback",
            job_id=job_id,
            state=observation.state.value,
            url=observation.url,
            selectors_found=",".join(observation.selectors_found),
            read_duration_ms=round(observation.read_duration_ms, 1),
        )
        await self._write_poll_diagnostic(job_id, observation)
        return observation

    async def _write_poll_diagnostic(
        self, job_id: int | None, observation: ScreenObservation
    ) -> None:
        diagnostics_dir = os.environ.get("FIREFLY_DIAG_DIR")
        if not diagnostics_dir:
            return

        target = Path(diagnostics_dir)
        target.mkdir(parents=True, exist_ok=True)
        poll = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "job_id": job_id,
            "url": observation.url,
            "state": observation.state.value,
            "selectors_found": list(observation.selectors_found),
            "selectors_absent": list(observation.selectors_absent),
            "selector_errors": list(observation.selector_errors),
            "read_duration_ms": observation.read_duration_ms,
            "screenshot_path": None,
        }

        if os.environ.get("FIREFLY_CAPTURE_POLL_SCREENSHOTS") == "true" and job_id is not None:
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = self.screenshots_dir / f"job_{job_id}_poll_{int(time.time() * 1000)}.png"
            try:
                await self.page.screenshot(path=str(screenshot_path), full_page=True)
                poll["screenshot_path"] = str(screenshot_path)
            except (PatchrightTimeoutError, PatchrightError) as exc:
                poll["screenshot_error"] = type(exc).__name__

        with (target / "state_reader_polls.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(poll, ensure_ascii=False) + "\n")
