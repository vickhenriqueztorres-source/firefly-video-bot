from __future__ import annotations

import pytest

from firefly_bot.healthcheck import reports_detection
from firefly_bot.selectors import (
    ACTION_SELECTORS,
    STATE_SELECTORS,
    UnconfirmedSelectorError,
    locator_for,
)
from firefly_bot.state_reader import ScreenState


def test_unconfirmed_selector_blocks_automation() -> None:
    with pytest.raises(UnconfirmedSelectorError, match="generate_media_icon"):
        locator_for(object(), "generate_media_icon")


def test_healthcheck_only_flags_explicit_detection() -> None:
    assert reports_detection("The browser reports: automation detected")
    assert not reports_detection("This page explains how bot detection works")


def test_veo_31_fast_selector_is_registered_and_confirmed() -> None:
    selector = ACTION_SELECTORS["model_option_veo31_fast"]
    assert selector.confirmed is True
    assert selector.value == "Veo 3.1 Fast"


def test_veo_31_premium_selector_is_registered_and_confirmed() -> None:
    selector = ACTION_SELECTORS["model_option_veo31"]
    assert selector.confirmed is True
    assert selector.value == "Veo 3.1"


def test_kling_25_turbo_selector_is_registered_and_confirmed() -> None:
    selector = ACTION_SELECTORS["model_option_kling25_turbo"]
    assert selector.confirmed is True
    assert selector.value.endswith("kling_v2_5_turbo_pro_i2v")


def test_provider_error_selector_is_confirmed() -> None:
    selector = STATE_SELECTORS[ScreenState.ERROR_TOAST]
    assert selector.confirmed is True
    assert selector.method == "text"
    assert selector.value == "Ocorreu um erro"


def test_still_generating_uses_active_generation_text() -> None:
    selector = STATE_SELECTORS[ScreenState.STILL_GENERATING]
    assert selector.confirmed is True
    assert selector.method == "text"
    assert selector.value == "Gerando vídeo"
