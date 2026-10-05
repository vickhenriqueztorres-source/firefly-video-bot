from __future__ import annotations

import asyncio

import pytest

from firefly_bot.duration_control import (
    DurationCapabilities,
    DurationController,
    DurationControlError,
    classify_duration_request,
    parse_duration_seconds,
)
from firefly_bot.duration_control_gate import selector_audit


def capabilities(**overrides: object) -> DurationCapabilities:
    data = {
        "model": "Kling 3.0",
        "observed_control": "combobox_discrete_options",
        "min": 5,
        "max": 5,
        "step": None,
        "current": 5,
        "supported_values": (5,),
        "observed_at": "2026-08-10T00:00:00+00:00",
    }
    data.update(overrides)
    return DurationCapabilities(**data)


def test_observed_discrete_kling_duration_contract_is_ready_for_5s() -> None:
    assert classify_duration_request(5, capabilities()) == "READY"


def test_fixed_combobox_duration_is_incomplete_discovery_not_unsupported() -> None:
    result = classify_duration_request(
        3,
        capabilities(observed_control="fixed_combobox_value", supported_values=()),
    )

    assert result == "FIREFLY_DURATION_CONTROL_DISCOVERY_FAILED"


def test_popover_slider_range_does_not_classify_supported_values_as_current_only() -> None:
    observed = capabilities(
        observed_control="popover_custom_slider",
        min=1,
        max=15,
        current=5,
        supported_values=(),
        capability_source="DOM_INPUT_ATTRIBUTES",
    )

    assert observed.supported_values == ()
    assert classify_duration_request(3, observed) == "READY"


def test_missing_duration_control_blocks_job_creation() -> None:
    result = classify_duration_request(
        3,
        capabilities(
            observed_control="missing",
            min=None,
            max=None,
            current=None,
            supported_values=(),
        ),
    )

    assert result == "FIREFLY_DURATION_CONTROL_NOT_FOUND"


def test_unassociated_global_slider_is_ambiguous() -> None:
    assert (
        classify_duration_request(3, capabilities(), associated_with_duration=False)
        == "AMBIGUOUS_DURATION_CONTROL"
    )


def test_requested_duration_outside_discrete_capabilities_is_unsupported() -> None:
    assert classify_duration_request(3, capabilities()) == "FIREFLY_DURATION_UNSUPPORTED"


def test_requested_duration_outside_slider_range_is_unsupported() -> None:
    result = classify_duration_request(
        16,
        capabilities(
            observed_control="popover_custom_slider",
            min=1,
            max=15,
            supported_values=(),
        ),
    )

    assert result == "FIREFLY_DURATION_UNSUPPORTED"


def test_picker_value_not_changed_has_specific_error_code() -> None:
    error = DurationControlError(
        "FIREFLY_DURATION_SET_FAILED",
        "duration input action completed but picker did not become 3s",
        {"requested_seconds": 3},
    )

    assert error.code == "FIREFLY_DURATION_SET_FAILED"
    assert "FIREFLY_DURATION_SET_FAILED" in str(error)


def test_open_menu_for_discovery_is_idempotent_when_slider_already_visible() -> None:
    class AlreadyOpenDurationController(DurationController):
        def __init__(self) -> None:
            super().__init__(page=None)  # type: ignore[arg-type]
            self.visible_slider_checks = 0

        async def _visible_slider(self) -> object | None:  # type: ignore[override]
            self.visible_slider_checks += 1
            return object()

    controller = AlreadyOpenDurationController()

    asyncio.run(controller.open_menu_for_discovery())

    assert controller.visible_slider_checks == 1


def test_selector_audit_contains_current_duration_contract() -> None:
    audit = selector_audit()

    assert audit["duration_discrete_option_5"]["value"] == "firefly-menu-item-5"
    assert audit["prompt_duration_button"]["value"] == "prompt-duration-button"
    assert audit["duration_prompt_trigger"]["value"] == "prompt-duration-button"
    assert audit["duration_track"]["value"].startswith('input[data-testid="duration-slider"]')
    assert audit["duration_slider"]["value"].startswith('[data-testid="firefly-picker-duration"]')


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5 segundos", 5),
        ("Duration 3 seconds", 3),
        ("firefly-menu-item-15", 15),
        ("720p", None),
    ],
)
def test_parse_duration_seconds(raw: str, expected: int | None) -> None:
    assert parse_duration_seconds(raw) == expected
