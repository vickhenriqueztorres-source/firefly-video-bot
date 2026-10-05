"""Probe headed para listar modelos disponíveis no seletor Firefly."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from patchright.async_api import async_playwright

from .chrome_profile import close_existing_profile_chrome
from .config import Config
from .selectors import locator_for


async def _collect_models(root: Path, output_path: Path) -> dict[str, object]:
    config = Config.from_root(root)
    close_existing_profile_chrome(config.profile_dir)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.profile_dir),
            channel=config.chrome_channel,
            headless=False,
            no_viewport=True,
            args=[
                "--disable-http2",
                "--disable-blink-features=AutomationControlled",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
            ignore_default_args=["--enable-automation"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(config.firefly_url, wait_until="domcontentloaded")
            marker = locator_for(page, "logged_in_marker")
            await marker.wait_for(state="visible", timeout=config.selector_timeout_ms)
            trigger = locator_for(page, "model_dropdown_trigger")
            await trigger.press("Enter")
            await page.wait_for_timeout(1500)
            data = await page.evaluate(
                """() => {
                    const seen = new Set();
                    const nodes = [];
                    function visible(el) {
                        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                        const style = getComputedStyle(el);
                        return !!rect && rect.width > 0 && rect.height > 0 &&
                            style.visibility !== 'hidden' && style.display !== 'none';
                    }
                    function walk(root, path) {
                        if (!root || seen.has(root)) return;
                        seen.add(root);
                        const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                        for (const el of elements) {
                            const testId = el.getAttribute('data-testid') || '';
                            const role = el.getAttribute('role') || '';
                            const tag = el.tagName || '';
                            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                            const value = el.getAttribute('value') || '';
                            if (
                                /firefly-menu-item/i.test(testId) ||
                                /menuitem|option/i.test(role) ||
                                /MENU-ITEM|OPTION/i.test(tag) ||
                                /Kling|Veo|Firefly|Runway|Luma|Pika|Ray|Google/i.test(text)
                            ) {
                                nodes.push({
                                    path,
                                    tag,
                                    testId,
                                    role,
                                    text,
                                    value,
                                    visible: visible(el),
                                    ariaLabel: el.getAttribute('aria-label') || ''
                                });
                            }
                            if (el.shadowRoot) walk(el.shadowRoot, `${path}>${tag}#shadow`);
                        }
                    }
                    walk(document, 'document');
                    return nodes;
                }"""
            )
            result = {
                "schema": "firefly.model-probe.v1",
                "captured_at": datetime.now(UTC).isoformat(),
                "url": page.url,
                "models": data,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return result
        finally:
            await context.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m firefly_bot.model_probe")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_collect_models(args.root.resolve(), args.output.resolve()))
    print(json.dumps({"status": "MODEL_PROBE_COMPLETE", "model_count": len(result["models"]), "output": str(args.output.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
