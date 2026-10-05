from __future__ import annotations

from firefly_bot.session import signed_out_text_marker


def test_signed_out_marker_detects_portuguese_firefly_header() -> None:
    page_text = "Gerar video Kling 2.5 Turbo Fazer logon Comprar agora"

    assert signed_out_text_marker(page_text) == "fazer logon"


def test_signed_out_marker_detects_identity_provider_error() -> None:
    message = "Not signed in with the identity provider."

    assert signed_out_text_marker(message) == "not signed in with the identity provider"


def test_signed_out_marker_does_not_reject_authenticated_composer() -> None:
    page_text = (
        "Modelo Kling 2.5 Turbo Resolucao 1080p Widescreen 24 FPS 5 segundos "
        "sign in"
    )

    assert signed_out_text_marker(page_text) is None
