"""Firefly duration UI contract discovery and configuration."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from patchright.async_api import Error as PatchrightError
from patchright.async_api import TimeoutError as PatchrightTimeoutError

from .config import Config
from .selectors import locator_for


class DurationControlError(RuntimeError):
    """Typed duration UI failure that must not be reported as generation failure."""

    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True, slots=True)
class DurationOption:
    value: int
    text: str
    data_testid: str | None
    visible: bool


@dataclass(frozen=True, slots=True)
class DurationCapabilities:
    model: str
    observed_control: str
    min: int | None
    max: int | None
    step: int | None
    current: int | None
    supported_values: tuple[int, ...]
    observed_at: str
    capability_source: str | None = None
    capture_index: int = 1
    verified_value_seconds: int | None = None
    requested_supported: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "control_type": self.observed_control,
            "observed_control": self.observed_control,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "current": self.current,
            "supported_values": list(self.supported_values),
            "observed_at": self.observed_at,
            "capability_source": self.capability_source,
            "capture_index": self.capture_index,
            "verified_value_seconds": self.verified_value_seconds,
            "requested_supported": self.requested_supported,
        }


def parse_duration_seconds(*values: object) -> int | None:
    for value in values:
        if value is None:
            continue
        match = re.search(r"\b(\d{1,2})\b", str(value))
        if match:
            return int(match.group(1))
    return None


def classify_duration_request(
    requested_seconds: int,
    capabilities: DurationCapabilities,
    associated_with_duration: bool = True,
) -> str:
    if not associated_with_duration:
        return "AMBIGUOUS_DURATION_CONTROL"
    if capabilities.observed_control == "missing":
        return "FIREFLY_DURATION_CONTROL_NOT_FOUND"
    if capabilities.observed_control == "fixed_combobox_value":
        return "FIREFLY_DURATION_CONTROL_DISCOVERY_FAILED"
    if capabilities.supported_values and requested_seconds not in capabilities.supported_values:
        return "FIREFLY_DURATION_UNSUPPORTED"
    if capabilities.min is not None and capabilities.max is not None:
        if not capabilities.min <= requested_seconds <= capabilities.max:
            return "FIREFLY_DURATION_UNSUPPORTED"
    return "READY"


class DurationController:
    def __init__(self, page: object, config: Config | None = None):
        self.page = page
        self.config = config or Config()

    async def configure(self, requested_seconds: int) -> DurationCapabilities:
        if requested_seconds <= 0:
            raise DurationControlError(
                "FIREFLY_DURATION_UNSUPPORTED",
                f"requested duration must be positive: {requested_seconds}",
            )
        await self.open_menu_for_discovery()
        capabilities = await self.discover(opened=True)
        decision = classify_duration_request(requested_seconds, capabilities)
        if decision != "READY":
            raise DurationControlError(
                decision,
                f"requested {requested_seconds}s is not configurable by observed duration UI",
                capabilities.as_dict(),
            )

        slider = await self._visible_slider()
        if slider is None:
            raise DurationControlError(
                "FIREFLY_DURATION_CONTROL_DISCOVERY_FAILED",
                "duration popover opened but no visible shot duration slider was observed",
                capabilities.as_dict(),
            )
        await self._set_slider(slider, requested_seconds)
        verified = await self._wait_for_duration_signals(requested_seconds)
        return self._with_request_result(
            await self.discover(opened=True),
            requested_seconds,
            verified,
        )

    async def open_menu_for_discovery(self) -> None:
        if await self._visible_slider() is not None:
            return
        trigger = locator_for(self.page, "duration_prompt_trigger")
        last_error: Exception | None = None
        for action in ("prompt_click", "prompt_enter", "interceptor_click"):
            try:
                if action == "prompt_click":
                    await trigger.click(timeout=min(self.config.selector_timeout_ms, 5000))
                elif action == "prompt_enter":
                    await trigger.press("Enter", timeout=min(self.config.selector_timeout_ms, 5000))
                else:
                    interceptor = locator_for(self.page, "duration_interceptor_trigger")
                    if await interceptor.count() == 0:
                        continue
                    await interceptor.first.click(timeout=5000)
                await self.page.wait_for_timeout(500)
                if await self._visible_slider() is not None:
                    return
            except (PatchrightTimeoutError, PatchrightError) as exc:
                last_error = exc
        raise DurationControlError(
            "FIREFLY_DURATION_CONTROL_DISCOVERY_FAILED",
            "duration trigger did not expose a visible shot duration slider",
            {"last_error": type(last_error).__name__ if last_error else None},
        )

    async def discover(self, opened: bool = False) -> DurationCapabilities:
        picker = locator_for(self.page, "duration_dropdown")
        current = parse_duration_seconds(
            await picker.get_attribute("value"),
            await locator_for(self.page, "duration_dropdown_trigger").inner_text(timeout=1500),
        )
        options = await self._duration_options()
        slider = await self._visible_slider()
        if options:
            values = tuple(sorted({option.value for option in options}))
            return DurationCapabilities(
                model="Kling 3.0",
                observed_control="combobox_discrete_options",
                min=min(values),
                max=max(values),
                step=None,
                current=current,
                supported_values=values,
                observed_at=datetime.now(UTC).isoformat(),
                capability_source="VISIBLE_MENU_OPTIONS",
            )
        if slider is not None:
            minimum = parse_duration_seconds(
                await slider.get_attribute("min"),
                await slider.get_attribute("aria-valuemin"),
                await self._label_text("duration_min_label"),
            )
            maximum = parse_duration_seconds(
                await slider.get_attribute("max"),
                await slider.get_attribute("aria-valuemax"),
                await self._label_text("duration_max_label"),
            )
            step = parse_duration_seconds(await slider.get_attribute("step"))
            value = await self._slider_value(slider)
            source = "DOM_INPUT_ATTRIBUTES" if minimum is not None and maximum is not None else "VISIBLE_RANGE_LABELS"
            return DurationCapabilities(
                model="Kling 3.0",
                observed_control="popover_custom_slider",
                min=minimum,
                max=maximum,
                step=step,
                current=value or current,
                supported_values=(),
                observed_at=datetime.now(UTC).isoformat(),
                capability_source=source,
                capture_index=1,
            )
        if current is not None:
            return DurationCapabilities(
                model="Kling 3.0",
                observed_control="fixed_combobox_value",
                min=current,
                max=current,
                step=None,
                current=current,
                supported_values=(),
                observed_at=datetime.now(UTC).isoformat(),
                capability_source="CURRENT_DISPLAY_VALUE_ONLY",
            )
        return DurationCapabilities(
            model="Kling 3.0",
            observed_control="missing",
            min=None,
            max=None,
            step=None,
            current=current,
            supported_values=(),
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def _duration_options(self) -> list[DurationOption]:
        locator = self.page.locator('[data-testid^="firefly-menu-item-"]')
        options: list[DurationOption] = []
        for index in range(await locator.count()):
            option = locator.nth(index)
            text = ""
            data_testid = await option.get_attribute("data-testid")
            try:
                text = (await option.inner_text(timeout=500)).strip()
            except (PatchrightTimeoutError, PatchrightError):
                pass
            value = parse_duration_seconds(
                await option.get_attribute("value"),
                await option.get_attribute("aria-label"),
                text,
                data_testid,
            )
            if value is None:
                continue
            label = await option.get_attribute("aria-label")
            is_duration = bool(
                re.search(r"segundos?|seconds?", f"{text} {label or ''}", flags=re.IGNORECASE)
            )
            if not is_duration:
                continue
            visible = False
            try:
                visible = await option.is_visible(timeout=500)
            except (PatchrightTimeoutError, PatchrightError):
                pass
            if not visible:
                continue
            options.append(DurationOption(value, text, data_testid, visible))
        return options

    async def _visible_slider(self) -> object | None:
        candidates = [
            locator_for(self.page, "duration_track"),
            self.page.locator('[data-testid="prompt-duration-slider"]'),
            self.page.locator('input[data-testid="duration-slider"][aria-label*="Dura"]'),
            self.page.locator('input[data-testid="duration-slider"][aria-label*="Duration"]'),
            self.page.locator('input[data-testid="duration-slider"][aria-label*="Captura"]'),
            self.page.locator('input[data-testid="duration-slider"][aria-label*="Capture"]'),
        ]
        for locator in candidates:
            if await locator.count() == 0:
                continue
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                try:
                    box = await candidate.bounding_box(timeout=500)
                except (PatchrightTimeoutError, PatchrightError):
                    box = None
                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                    return candidate
        return None

    async def _set_slider(self, slider: object, requested_seconds: int) -> None:
        minimum = parse_duration_seconds(await slider.get_attribute("min")) or 1
        maximum = parse_duration_seconds(await slider.get_attribute("max")) or 15
        if not minimum <= requested_seconds <= maximum:
            raise DurationControlError(
                "FIREFLY_DURATION_UNSUPPORTED",
                f"requested {requested_seconds}s outside slider range {minimum}-{maximum}",
            )
        try:
            await slider.focus()
            await slider.press("Home")
            for _ in range(requested_seconds - minimum):
                await slider.press("ArrowRight")
            await self._wait_for_duration_signals(requested_seconds, timeout_seconds=1.2)
            return
        except DurationControlError:
            pass

        box = await slider.bounding_box()
        if box is None:
            raise DurationControlError(
                "FIREFLY_DURATION_CONTROL_DISCOVERY_FAILED",
                "visible duration slider lost its bounding box before interaction",
            )
        ratio = (requested_seconds - minimum) / (maximum - minimum)
        x = box["x"] + ratio * box["width"]
        y = box["y"] + box["height"] / 2
        await self.page.mouse.click(x, y)

    async def _wait_for_duration_signals(
        self, seconds: int, timeout_seconds: float = 3.0
    ) -> int:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            signals = await self.duration_value_signals()
            matches = [
                name
                for name, value in signals.items()
                if isinstance(value, int) and value == seconds
            ]
            if len(matches) >= 2:
                return seconds
            await asyncio.sleep(0.1)
        raise DurationControlError(
            "FIREFLY_DURATION_SET_FAILED",
            f"duration input action completed but UI did not confirm {seconds}s",
            {"requested_seconds": seconds, "signals": await self.duration_value_signals()},
        )

    async def duration_value_signals(self) -> dict[str, int | None]:
        picker = locator_for(self.page, "duration_dropdown")
        slider = await self._visible_slider()
        prompt = locator_for(self.page, "prompt_duration_button")
        return {
            "duration_trigger": parse_duration_seconds(
                await picker.get_attribute("value"),
                await locator_for(self.page, "duration_dropdown_trigger").inner_text(timeout=1000),
            ),
            "prompt_duration_button": parse_duration_seconds(
                await prompt.inner_text(timeout=1000) if await prompt.count() else None
            ),
            "slider_value": await self._slider_value(slider) if slider is not None else None,
            "capture_row": parse_duration_seconds(await self._capture_row_text()),
        }

    async def _slider_value(self, slider: object) -> int | None:
        try:
            value = await slider.evaluate("(element) => element.value")
            return parse_duration_seconds(value)
        except (PatchrightTimeoutError, PatchrightError):
            return parse_duration_seconds(
                await slider.get_attribute("value"),
                await slider.get_attribute("aria-valuenow"),
                await slider.get_attribute("aria-valuetext"),
            )

    async def _label_text(self, key: str) -> str | None:
        locator = locator_for(self.page, key)
        for index in range(await locator.count()):
            item = locator.nth(index)
            try:
                if await item.bounding_box(timeout=500):
                    return await item.inner_text(timeout=500)
            except (PatchrightTimeoutError, PatchrightError):
                continue
        return None

    async def _capture_row_text(self) -> str | None:
        row = locator_for(self.page, "duration_capture_row")
        for index in range(await row.count()):
            item = row.nth(index)
            try:
                if await item.bounding_box(timeout=500):
                    return await item.inner_text(timeout=500)
            except (PatchrightTimeoutError, PatchrightError):
                continue
        return None

    def _with_request_result(
        self,
        capabilities: DurationCapabilities,
        requested_seconds: int,
        verified_seconds: int | None,
    ) -> DurationCapabilities:
        return DurationCapabilities(
            model=capabilities.model,
            observed_control=capabilities.observed_control,
            min=capabilities.min,
            max=capabilities.max,
            step=capabilities.step,
            current=verified_seconds or capabilities.current,
            supported_values=capabilities.supported_values,
            observed_at=datetime.now(UTC).isoformat(),
            capability_source=capabilities.capability_source,
            capture_index=capabilities.capture_index,
            verified_value_seconds=verified_seconds,
            requested_supported=verified_seconds == requested_seconds,
        )
