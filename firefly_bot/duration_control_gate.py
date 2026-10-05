"""Real Firefly duration UI contract micro-gate.

This gate observes the provider UI with the persistent Chrome profile and never
clicks Generate. It exists to classify duration-contract drift before jobs spend
credits or enter the generation state.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchright.async_api import Error as PatchrightError
from patchright.async_api import TimeoutError as PatchrightTimeoutError
from patchright.async_api import async_playwright

from .chrome_profile import ChromeProfileBusyError, close_existing_profile_chrome
from .config import Config
from .duration_control import (
    DurationControlError,
    DurationController,
    classify_duration_request,
)
from .selectors import ACTION_SELECTORS, locator_for

RUN_ID = "FIREFLY-DURATION-CONTROL-001"
DEFAULT_RUN_DIR = Path(r"C:\B2-AI-STUDIO\mission-control\runs") / RUN_ID


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_event(run_dir: Path, event_name: str, **fields: object) -> None:
    path = run_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": utc_now(), "event": event_name, **fields}, ensure_ascii=False) + "\n")


def count_jobs(config: Config) -> dict[str, Any]:
    if not config.db_path.exists():
        return {"db_path": str(config.db_path), "exists": False, "total_jobs": 0, "max_job_id": None}
    with sqlite3.connect(config.db_path) as connection:
        row = connection.execute("SELECT COUNT(*), MAX(id) FROM jobs").fetchone()
        system = connection.execute(
            "SELECT status, reason, updated_at FROM system_state WHERE singleton=1"
        ).fetchone()
    return {
        "db_path": str(config.db_path),
        "exists": True,
        "total_jobs": int(row[0]),
        "max_job_id": row[1],
        "system_state": {
            "status": system[0],
            "reason": system[1],
            "updated_at": system[2],
        }
        if system
        else None,
    }


async def locator_snapshot(locator: object, sample_limit: int = 8) -> dict[str, Any]:
    result: dict[str, Any] = {"count": 0, "items": []}
    try:
        count = await locator.count()
    except (PatchrightTimeoutError, PatchrightError) as exc:
        return {"count": 0, "error": type(exc).__name__}
    result["count"] = count
    for index in range(min(count, sample_limit)):
        item = locator.nth(index)
        data: dict[str, Any] = {"index": index}
        for attr in ("data-testid", "role", "aria-label", "value", "aria-valuemin", "aria-valuemax", "aria-valuenow", "min", "max", "step"):
            try:
                data[attr] = await item.get_attribute(attr, timeout=500)
            except (PatchrightTimeoutError, PatchrightError):
                data[attr] = None
        try:
            data["visible"] = await item.is_visible(timeout=500)
        except (PatchrightTimeoutError, PatchrightError):
            data["visible"] = False
        try:
            data["text"] = (await item.inner_text(timeout=500)).strip()[:500]
        except (PatchrightTimeoutError, PatchrightError):
            data["text"] = ""
        result["items"].append(data)
    return result


async def collect_ui_state(page: object) -> dict[str, Any]:
    positive = {
        "model_picker": await locator_snapshot(page.locator('[data-testid="firefly-picker-model"]'), 2),
        "prompt_input": await locator_snapshot(page.locator('textarea[aria-label="Prompt"], .tiptap.ProseMirror[contenteditable="true"]'), 2),
        "first_frame_button": await locator_snapshot(page.locator('[data-testid="placeholder-upload-button"]'), 4),
        "generate_button": await locator_snapshot(page.locator('[data-testid="video-generation-generate-button"]'), 2),
    }
    body_text = ""
    try:
        body_text = (await page.locator("body").inner_text(timeout=1500)).lower()
    except (PatchrightTimeoutError, PatchrightError):
        pass
    negative_signals = [
        signal
        for signal in ("sign in", "log in", "entrar", "fazer login", "sessão expirada")
        if signal in body_text
    ]
    visible_positive = [
        key
        for key, value in positive.items()
        if any(item.get("visible") for item in value.get("items", []))
    ]
    auth_state = "READY" if visible_positive and not negative_signals else "AUTH_REQUIRED"
    return {
        "url": page.url,
        "title": await page.title(),
        "auth_state": auth_state,
        "positive_ui_signals": positive,
        "visible_positive_signals": visible_positive,
        "negative_auth_signals": negative_signals,
    }


async def collect_duration_candidates(page: object) -> dict[str, Any]:
    return {
        "duration_picker": await locator_snapshot(page.locator('[data-testid="firefly-picker-duration"]'), 4),
        "duration_picker_button": await locator_snapshot(page.locator('[data-testid="firefly-picker-duration"] #button'), 4),
        "old_nested_slider": await locator_snapshot(page.locator('[data-testid="firefly-picker-duration"] [role="slider"], [data-testid="firefly-picker-duration"] input[type="range"]'), 8),
        "prompt_duration_button": await locator_snapshot(page.locator('[data-testid="prompt-duration-button"]'), 4),
        "popover_duration_slider": await locator_snapshot(page.locator('input[data-testid="duration-slider"][aria-label*="Dura"], input[data-testid="duration-slider"][aria-label*="Duration"]'), 8),
        "duration_slider_component": await locator_snapshot(page.locator("firefly-duration-slider"), 8),
        "duration_data_testids": await locator_snapshot(page.locator('[data-testid*="duration" i]'), 20),
        "global_sliders": await locator_snapshot(page.locator('[role="slider"], input[type="range"]'), 20),
        "duration_menu_items": await locator_snapshot(page.locator('[data-testid^="firefly-menu-item-"]'), 20),
    }


def selector_audit() -> dict[str, Any]:
    keys = [
        "duration_dropdown",
        "duration_dropdown_trigger",
        "duration_slider",
        "duration_discrete_option_5",
        "prompt_duration_button",
        "prompt_duration_slider",
        "duration_prompt_trigger",
        "duration_interceptor_trigger",
        "duration_popover",
        "duration_capture_row",
        "duration_track",
        "duration_thumb",
        "duration_current_value",
        "duration_min_label",
        "duration_max_label",
        "model_dropdown",
        "model_option_kling3",
    ]
    return {key: asdict(ACTION_SELECTORS[key]) for key in keys if key in ACTION_SELECTORS}


async def select_kling3(page: object, config: Config) -> dict[str, Any]:
    picker = locator_for(page, "model_dropdown")
    before = await picker.get_attribute("value")
    if before != "kling:firefly:colligo:v3direct":
        trigger = locator_for(page, "model_dropdown_trigger")
        await trigger.press("Enter")
        option = locator_for(page, "model_option_kling3")
        await option.wait_for(state="visible", timeout=config.selector_timeout_ms)
        await option.click()
        await page.wait_for_timeout(600)
        await page.keyboard.press("Escape")
        await page.mouse.click(900, 850)
        await page.wait_for_timeout(250)
    after = await picker.get_attribute("value")
    text = ""
    try:
        text = await locator_for(page, "model_dropdown_trigger").inner_text(timeout=1000)
    except (PatchrightTimeoutError, PatchrightError):
        pass
    return {"before": before, "after": after, "button_text": text, "selected": after == "kling:firefly:colligo:v3direct"}


async def capture_page_artifacts(page: object, base: Path, name: str) -> dict[str, Any]:
    base.mkdir(parents=True, exist_ok=True)
    screenshot = base / f"{name}.png"
    html = base / f"{name}.html"
    await page.screenshot(path=str(screenshot), full_page=True)
    html.write_text(await page.content(), encoding="utf-8", errors="replace")
    return {
        "screenshot": str(screenshot),
        "screenshot_sha256": sha256_file(screenshot),
        "html": str(html),
        "html_sha256": sha256_file(html),
        "url": page.url,
    }


async def collect_shadow_dom(page: object) -> list[dict[str, Any]]:
    return await page.evaluate(
        r"""() => {
            const out = [];
            const seen = new Set();
            function walk(root, path) {
                for (const [index, element] of Array.from(root.children || []).entries()) {
                    if (seen.has(element)) continue;
                    seen.add(element);
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    const text = (element.innerText || element.textContent || '').trim();
                    const item = {
                        path: `${path}/${element.tagName.toLowerCase()}:${index}`,
                        tagName: element.tagName,
                        role: element.getAttribute('role'),
                        class: String(element.className).slice(0, 300),
                        dataTestId: element.getAttribute('data-testid'),
                        ariaLabel: element.getAttribute('aria-label'),
                        ariaLabelledby: element.getAttribute('aria-labelledby'),
                        ariaValueMin: element.getAttribute('aria-valuemin'),
                        ariaValueMax: element.getAttribute('aria-valuemax'),
                        ariaValueNow: element.getAttribute('aria-valuenow'),
                        ariaValueText: element.getAttribute('aria-valuetext'),
                        tabindex: element.getAttribute('tabindex'),
                        type: element.getAttribute('type'),
                        min: element.getAttribute('min'),
                        max: element.getAttribute('max'),
                        step: element.getAttribute('step'),
                        value: element.value ?? element.getAttribute('value'),
                        textContent: text.slice(0, 500),
                        visible: rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
                        boundingBox: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                    };
                    const haystack = [
                        item.tagName, item.role, item.class, item.dataTestId, item.ariaLabel, item.textContent
                    ].join(' ');
                    if (item.visible && /Duração|Duration|Captura|Capture|Total|slider|range|thumb|track|handle|1 s|15 s|\d+ s/i.test(haystack)) {
                        out.push(item);
                    }
                    if (element.shadowRoot) walk(element.shadowRoot, `${item.path}#shadow`);
                    walk(element, item.path);
                }
            }
            walk(document.documentElement, 'html');
            return out;
        }"""
    )


async def run_gate(
    *,
    root: Path,
    run_dir: Path,
    requested_seconds: int,
    logger: logging.Logger,
    run_id: str = RUN_ID,
) -> dict[str, Any]:
    config = Config.from_root(root)
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    before_jobs = count_jobs(config)
    write_json(run_dir / "preflight" / "job_count_before.json", before_jobs)
    append_event(run_dir, "gate_started", run_id=run_id, requested_seconds=requested_seconds)
    try:
        closed = close_existing_profile_chrome(config.profile_dir)
    except ChromeProfileBusyError as exc:
        result = {
            "run_id": run_id,
            "status": "PROFILE_BUSY",
            "started_at": started_at,
            "completed_at": utc_now(),
            "error": str(exc),
            "job_created": False,
            "generate_clicked": False,
            "mock": False,
            "fallback": False,
        }
        write_json(run_dir / "postflight.json", result)
        write_report(run_dir, result)
        return result

    write_json(run_dir / "preflight" / "profile_process_cleanup.json", {"closed_processes": closed})
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.profile_dir),
            channel=config.chrome_channel,
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            restored = list(context.pages)
            page = await context.new_page()
            for restored_page in restored:
                try:
                    await restored_page.close()
                except PatchrightError:
                    pass
            page.set_default_timeout(config.selector_timeout_ms)
            append_event(run_dir, "browser_opened", profile_dir=str(config.profile_dir))
            await page.goto(config.firefly_url, wait_until="domcontentloaded", timeout=config.nav_timeout_ms)
            await page.wait_for_timeout(8000)
            before_artifacts = await capture_page_artifacts(page, run_dir / "before", "firefly_loaded")
            ui_state = await collect_ui_state(page)
            write_json(run_dir / "preflight" / "auth_state.json", {"auth_state": ui_state["auth_state"], **ui_state})
            write_json(run_dir / "preflight" / "ui_state.json", ui_state)
            append_event(run_dir, "ui_state_observed", auth_state=ui_state["auth_state"], url=page.url)
            if ui_state["auth_state"] != "READY":
                result = {
                    "run_id": run_id,
                    "status": "AUTH_REQUIRED",
                    "started_at": started_at,
                    "completed_at": utc_now(),
                    "auth_state": ui_state,
                    "before_artifacts": before_artifacts,
                    "job_created": False,
                    "generate_clicked": False,
                    "mock": False,
                    "fallback": False,
                }
                write_json(run_dir / "postflight.json", result)
                write_report(run_dir, result)
                return result

            model_state = await select_kling3(page, config)
            write_json(run_dir / "duration" / "model_state.json", model_state)
            append_event(run_dir, "model_selected", **model_state)

            controller = DurationController(page, config)
            trigger_artifacts = await capture_page_artifacts(
                page, run_dir / "duration", "duration_trigger_before"
            )
            open_error: dict[str, Any] | None = None
            try:
                await controller.open_menu_for_discovery()
            except DurationControlError as exc:
                open_error = {"code": exc.code, "message": str(exc), "evidence": exc.evidence}
                write_json(run_dir / "duration" / "duration_open_error.json", open_error)
            open_artifacts = await capture_page_artifacts(
                page, run_dir / "duration", "duration_popover_open"
            )
            write_json(
                run_dir / "duration" / "duration_popover_dom.json",
                await collect_shadow_dom(page),
            )
            candidates = await collect_duration_candidates(page)
            write_json(run_dir / "duration" / "candidate_controls.json", candidates)
            write_json(run_dir / "duration" / "selector_audit.json", selector_audit())
            capabilities = await controller.discover(opened=True)
            write_json(run_dir / "duration" / "duration_capabilities.json", capabilities.as_dict())
            signals_before = await controller.duration_value_signals()
            write_json(run_dir / "duration" / "duration_signals_before.json", signals_before)
            before_value = {
                "requested_seconds": requested_seconds,
                "picker_value": await locator_for(page, "duration_dropdown").get_attribute("value"),
                "capabilities": capabilities.as_dict(),
                "signals": signals_before,
            }
            write_json(run_dir / "duration" / "before_value.json", before_value)
            append_event(run_dir, "duration_capabilities_observed", **capabilities.as_dict())

            status = classify_duration_request(requested_seconds, capabilities)
            error: dict[str, Any] | None = None
            after_value: dict[str, Any] | None = None
            if status == "READY":
                try:
                    configured = await controller.configure(requested_seconds)
                    status = "PASS"
                    signals_after = await controller.duration_value_signals()
                    after_value = {
                        "requested_seconds": requested_seconds,
                        "picker_value": await locator_for(page, "duration_dropdown").get_attribute("value"),
                        "capabilities": configured.as_dict(),
                        "signals": signals_after,
                    }
                    write_json(run_dir / "duration" / "duration_signals_after.json", signals_after)
                    write_json(run_dir / "duration" / "after_value.json", after_value)
                    await capture_page_artifacts(page, run_dir / "duration", "duration_configured")
                except DurationControlError as exc:
                    status = exc.code
                    error = {"code": exc.code, "message": str(exc), "evidence": exc.evidence}
                    write_json(run_dir / "duration" / "duration_error.json", error)
            else:
                error = {
                    "code": status,
                    "message": f"{status}: requested {requested_seconds}s is not configurable by observed duration UI",
                    "evidence": capabilities.as_dict(),
                }
                write_json(run_dir / "duration" / "duration_error.json", error)
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
            except PatchrightError:
                pass
            await capture_page_artifacts(page, run_dir / "duration", "duration_unsupported_or_error")

            after_jobs = count_jobs(config)
            job_created = after_jobs["total_jobs"] != before_jobs["total_jobs"]
            capability_state = {
                "requested_seconds": requested_seconds,
                "status": status,
                "duration_supported": status == "PASS",
                "old_slider_selector_found": candidates["old_nested_slider"]["count"] > 0,
                "observed_duration_control": capabilities.observed_control,
                "capability_source": capabilities.capability_source,
                "min": capabilities.min,
                "max": capabilities.max,
                "step": capabilities.step,
                "initial": capabilities.current,
                "supported_values": list(capabilities.supported_values),
                "verified_final": after_value.get("capabilities", {}).get("verified_value_seconds")
                if after_value
                else None,
                "job_created": job_created,
                "generate_clicked": False,
            }
            write_json(run_dir / "preflight" / "capability_state.json", capability_state)
            write_json(run_dir / "postflight" / "job_count_after.json", after_jobs)
            result = {
                "run_id": run_id,
                "status": status,
                "started_at": started_at,
                "completed_at": utc_now(),
                "auth_state": "READY",
                "ui_state": "READY" if status == "PASS" else "PROVIDER_UI_CONTRACT_CHANGED",
                "capability_state": capability_state,
                "model_state": model_state,
                "before_artifacts": before_artifacts,
                "duration_trigger_artifacts": trigger_artifacts,
                "duration_menu_artifacts": open_artifacts,
                "before_jobs": before_jobs,
                "after_jobs": after_jobs,
                "job_created": job_created,
                "generate_clicked": False,
                "mock": False,
                "fallback": False,
                "recorded_provider": False,
                "error": error,
                "open_error": open_error,
                "after_value": after_value,
            }
            write_json(run_dir / "postflight.json", result)
            write_report(run_dir, result)
            append_event(run_dir, "gate_finished", status=status, job_created=job_created)
            return result
        finally:
            await context.close()


def write_report(run_dir: Path, result: dict[str, Any]) -> None:
    capability = result.get("capability_state", {})
    error = result.get("error") or {}
    after_value = result.get("after_value") or {}
    after_capabilities = after_value.get("capabilities") or {}
    content = f"""# {result.get("run_id", RUN_ID)}

Result: {result.get("status")}

| Field | Value |
| --- | --- |
| Auth state | {result.get("auth_state")} |
| UI state | {result.get("ui_state", "")} |
| Model | {result.get("model_state", {}).get("button_text", "").replace(chr(10), " / ")} |
| Requested duration | {capability.get("requested_seconds", "")}s |
| Duration supported | {"YES" if capability.get("duration_supported") else "NO"} |
| Control type | {capability.get("observed_duration_control", "")} |
| Range source | {capability.get("capability_source", "")} |
| Min / max / step | {capability.get("min", "")} / {capability.get("max", "")} / {capability.get("step", "")} |
| Initial value | {capability.get("initial", "")} |
| Verified final | {after_capabilities.get("verified_value_seconds", "")} |
| Old nested slider selector found | {"YES" if capability.get("old_slider_selector_found") else "NO"} |
| Generate clicked | {"YES" if result.get("generate_clicked") else "NO"} |
| Job created | {"YES" if result.get("job_created") else "NO"} |
| Mock | {"YES" if result.get("mock") else "NO"} |
| Fallback | {"YES" if result.get("fallback") else "NO"} |

## Classification

{error.get("message", "Duration UI contract is ready for the requested value.")}

## Evidence

- auth state: {run_dir / "preflight" / "auth_state.json"}
- UI state: {run_dir / "preflight" / "ui_state.json"}
- capability state: {run_dir / "preflight" / "capability_state.json"}
- selector audit: {run_dir / "duration" / "selector_audit.json"}
- candidates: {run_dir / "duration" / "candidate_controls.json"}
- capabilities: {run_dir / "duration" / "duration_capabilities.json"}
- duration trigger before: {run_dir / "duration" / "duration_trigger_before.png"}
- duration popover: {run_dir / "duration" / "duration_popover_open.png"}
- duration popover DOM: {run_dir / "duration" / "duration_popover_dom.json"}
- duration configured: {run_dir / "duration" / "duration_configured.png"}
"""
    write_text(run_dir / "REPORT.md", content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="firefly-duration-control-gate")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument("--run-id", default=RUN_ID)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()
    result = asyncio.run(
        run_gate(
            root=args.root.resolve(),
            run_dir=args.run_dir.resolve(),
            requested_seconds=args.seconds,
            logger=logging.getLogger("firefly_duration_control_gate"),
            run_id=args.run_id,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "FIREFLY_DURATION_UNSUPPORTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
