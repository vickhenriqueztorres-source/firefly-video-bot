"""Verificação semanal de compatibilidade do navegador endurecido."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from patchright.async_api import async_playwright

from .config import Config
from .logging_utils import event

CHECK_TARGETS: dict[str, str] = {
    "sannysoft": "https://bot.sannysoft.com/",
    "fingerprint": "https://fingerprint.com/",
    "creepjs": "https://abrahamjuliot.github.io/creepjs/",
    "browserscan": "https://www.browserscan.net/",
}
DETECTION_PHRASES = (
    "automation detected",
    "bot detected",
    "webdriver detected",
    "you are detected",
)


@dataclass(frozen=True, slots=True)
class HealthcheckResult:
    target: str
    detected: bool
    screenshot_path: Path


def reports_detection(body_text: str) -> bool:
    """Só acusa frases explícitas para não confundir documentação da própria página com falha."""
    normalized = " ".join(body_text.lower().split())
    return any(phrase in normalized for phrase in DETECTION_PHRASES)


async def run_healthcheck(config: Config, logger: logging.Logger) -> list[HealthcheckResult]:
    """Executa os quatro checks e registra screenshot para auditoria humana posterior."""
    config.screenshots_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[HealthcheckResult] = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.profile_dir),
            channel=config.chrome_channel,
            headless=False,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
        )
        try:
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.pages[0] if context.pages else await context.new_page()
            for target, url in CHECK_TARGETS.items():
                await page.goto(url, wait_until="domcontentloaded", timeout=config.nav_timeout_ms)
                text = await page.locator("body").inner_text(timeout=config.selector_timeout_ms)
                screenshot_path = config.screenshots_dir / f"healthcheck_{target}_{stamp}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                detected = reports_detection(text)
                event(
                    logger,
                    logging.ERROR if detected else logging.INFO,
                    "healthcheck_result",
                    target=target,
                    detected=detected,
                    screenshot=screenshot_path,
                )
                results.append(HealthcheckResult(target, detected, screenshot_path))
        finally:
            await context.close()
    return results
