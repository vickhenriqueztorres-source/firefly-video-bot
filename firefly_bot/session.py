"""Verificação de sessão: detecta, pausa e nunca automatiza login."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from patchright.async_api import TimeoutError as PatchrightTimeoutError, async_playwright

from .config import Config
from .dom_text import deep_composed_text
from .generation_profile import GENERATION_PROFILE
from .human_input import HumanInput
from .overlays import dismiss_overlays
from .selectors import SELECTORS, UnconfirmedSelectorError, locator_for
from .state_reader import StateReader


class SessionNotAuthenticatedError(RuntimeError):
    pass


SIGNED_OUT_TEXT_MARKERS = (
    "fazer logon",
    "iniciar sessao",
    "crie gratis todos os dias",
    "not signed in with the identity provider",
)


def signed_out_text_marker(text: str) -> str | None:
    """Retorna o marcador que prova que a UI esta deslogada."""
    normalized = " ".join((text or "").casefold().split())
    return next((marker for marker in SIGNED_OUT_TEXT_MARKERS if marker in normalized), None)


async def _detect_signed_out_in_context(context: object) -> str | None:
    url = str(getattr(context, "url", "")).casefold()
    if any(token in url for token in ("/login", "/auth", "/signin")):
        return f"redirecionamento de autenticacao em {getattr(context, 'url', '')}"

    dom_match = await context.evaluate(r'''() => {
        const markers = [
            'fazer logon', 'iniciar sessao', 'iniciar sessão', 'sign in', 'log in',
            'crie gratis todos os dias', 'crie grátis todos os dias',
            'not signed in with the identity provider'
        ];
        const seen = new Set();
        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
        function visible(el) {
            if (!el?.getBoundingClientRect) return false;
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
        }
        function walk(root) {
            if (!root || seen.has(root)) return null;
            seen.add(root);
            const all = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
            for (const el of all) {
                if (visible(el)) {
                    const values = [
                        el.innerText,
                        el.textContent,
                        el.getAttribute?.('aria-label'),
                        el.getAttribute?.('title')
                    ].map(normalize);
                    for (const value of values) {
                        const marker = markers.find((candidate) => value.includes(candidate));
                        if (marker) return marker;
                    }
                }
                if (el.shadowRoot) {
                    const nested = walk(el.shadowRoot);
                    if (nested) return nested;
                }
            }
            return null;
        }
        return walk(document);
    }''')
    if dom_match:
        return f"marcador visivel: {dom_match}"

    composed_match = signed_out_text_marker(await deep_composed_text(context))
    if composed_match:
        return f"texto composto: {composed_match}"
    return None


async def detect_signed_out(page: object) -> str | None:
    """Inspeciona a pagina e todos os iframes usados pelo header Adobe."""
    import re
    try:
        # Verificacao direta via Playwright no cabeçalho universal/shadow DOM
        login_btn = getattr(page, "get_by_role", None)
        if login_btn:
            btn = page.get_by_role("button", name=re.compile(r"^Fazer logon$|^Iniciar sess[aã]o$|^Sign in$|^Log in$", re.IGNORECASE))
            if await btn.is_visible(timeout=1000):
                return "botão Fazer logon visível no cabeçalho Adobe"
    except Exception:
        pass

    contexts = [page]
    for frame in getattr(page, "frames", []):
        if frame not in contexts:
            contexts.append(frame)

    for context in contexts:
        try:
            reason = await _detect_signed_out_in_context(context)
        except Exception:
            # Frames efemeros podem ser destacados durante a hidratacao.
            continue
        if reason:
            context_url = str(getattr(context, "url", ""))
            return f"{reason}; contexto={context_url}"
    return None


class SessionManager:
    def __init__(
        self, page: object, state_reader: StateReader, config: Config | None = None
    ):
        self.page = page
        self.state_reader = state_reader
        self.config = config or Config()

    async def require_authenticated(self, job_id: int | None = None) -> None:
        signed_out_reason = await detect_signed_out(self.page)
        if signed_out_reason:
            raise SessionNotAuthenticatedError(
                f"sessao expirada ou tela de login detectada ({signed_out_reason})"
            )
        if not SELECTORS["logged_in_marker"].confirmed:
            raise UnconfirmedSelectorError("logged_in_marker precisa ser confirmado manualmente")
        marker = locator_for(self.page, "logged_in_marker")
        try:
            await marker.wait_for(
                state="visible", timeout=self.config.selector_timeout_ms
            )
        except PatchrightTimeoutError as exc:
            raise SessionNotAuthenticatedError(
                "marcador de sessão autenticada não está visível"
            ) from exc


async def probe_session(config: Config) -> dict[str, Any]:
    """Valida a mesma sessão Chrome headed usada pelo worker de produção."""
    profile_dir = config.profile_dir
    if not profile_dir.exists():
        return {
            "authenticated": False,
            "reason": f"PROFILE_NOT_FOUND: Diretório de perfil não existe em {profile_dir}",
            "profile_dir": str(profile_dir),
        }
    
    default_dir = profile_dir / "Default"
    if not default_dir.exists():
        return {
            "authenticated": False,
            "reason": f"PROFILE_INCOMPLETE: Subdiretório Default ausente em {profile_dir}",
            "profile_dir": str(profile_dir),
        }

    try:
        from .chrome_profile import close_existing_profile_chrome
        close_existing_profile_chrome(profile_dir)
    except Exception:
        pass

    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
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
                try:
                    await page.goto(config.firefly_url, wait_until="domcontentloaded", timeout=config.nav_timeout_ms)
                    await asyncio.sleep(4)
                except Exception as nav_err:
                    return {
                        "authenticated": False,
                        "reason": f"NAVIGATION_FAILED: {nav_err}",
                        "profile_dir": str(profile_dir),
                    }

                # O header de autenticacao pode hidratar depois do compositor. Faca
                # mais de uma leitura antes de aceitar controles visiveis como prova.
                for attempt in range(3):
                    signed_out_reason = await detect_signed_out(page)
                    if signed_out_reason:
                        return {
                            "authenticated": False,
                            "production_ui_ready": False,
                            "reason": f"FIREFLY_SESSION_SIGNED_OUT: {signed_out_reason}",
                            "profile_dir": str(profile_dir),
                        }
                    if attempt < 2:
                        await page.wait_for_timeout(2000)

                human_input = HumanInput(page)
                logger = logging.getLogger("firefly_bot.session_probe")
                await dismiss_overlays(page, human_input, logger, -1)

                prompt = locator_for(page, "prompt_input")
                generate = locator_for(page, "generate_button")
                controls = {
                    "model_picker_visible": await locator_for(page, "model_dropdown").is_visible(timeout=3000),
                    "resolution_picker_visible": await locator_for(page, "resolution_dropdown").is_visible(timeout=3000),
                    "aspect_ratio_picker_visible": await locator_for(page, "aspect_ratio_dropdown").is_visible(timeout=3000),
                    "prompt_input_visible": await prompt.is_visible(timeout=3000),
                    "generate_control_visible": await generate.is_visible(timeout=3000),
                }

                async def ensure_picker(
                    picker_key: str,
                    trigger_key: str,
                    option_key: str,
                    expected_values: set[str],
                    expected_label: str,
                ) -> bool:
                    import re
                    picker = locator_for(page, picker_key)
                    trigger = locator_for(page, trigger_key)
                    picker_value = (await picker.get_attribute("value") or "").casefold()
                    trigger_text = (await trigger.inner_text() or "").casefold()
                    normalized_values = {value.casefold() for value in expected_values}
                    if picker_value in normalized_values or expected_label.casefold() in trigger_text:
                        return True
                    
                    await dismiss_overlays(page, human_input, logger, -1)
                    try:
                        await trigger.press("Enter")
                    except Exception:
                        try:
                            await trigger.click()
                        except Exception:
                            pass
                    await page.wait_for_timeout(300)
                    await dismiss_overlays(page, human_input, logger, -1)

                    try:
                        option = locator_for(page, option_key)
                        await option.wait_for(state="visible", timeout=4000)
                        await option.click()
                    except Exception:
                        try:
                            fallback = page.get_by_role("menuitem", name=re.compile(expected_label, re.IGNORECASE))
                            if not await fallback.is_visible(timeout=1500):
                                fallback = page.get_by_text(re.compile(expected_label, re.IGNORECASE)).first
                            await fallback.wait_for(state="visible", timeout=3000)
                            await fallback.click()
                        except Exception:
                            pass

                    await page.wait_for_timeout(500)
                    picker_value = (await picker.get_attribute("value") or "").casefold()
                    trigger_text = (await trigger.inner_text() or "").casefold()
                    return picker_value in normalized_values or expected_label.casefold() in trigger_text

                profile_confirmed = False
                model_confirmed = False
                resolution_confirmed = False
                aspect_confirmed = False
                fps_confirmed = False
                duration_confirmed = False
                settings_excerpt: list[str] = []
                try:
                    model_confirmed = await ensure_picker(
                        "model_dropdown",
                        "model_dropdown_trigger",
                        "model_option_kling25_turbo",
                        {str(GENERATION_PROFILE["model_value"])},
                        str(GENERATION_PROFILE["model"]),
                    )
                    resolution_confirmed = await ensure_picker(
                        "resolution_dropdown",
                        "resolution_dropdown_trigger",
                        "resolution_option_1080p",
                        {"1080", "1080p"},
                        str(GENERATION_PROFILE["resolution"]),
                    )
                    aspect_confirmed = await ensure_picker(
                        "aspect_ratio_dropdown",
                        "aspect_ratio_dropdown_trigger",
                        "aspect_ratio_widescreen",
                        {'{"height":720,"width":1280}', '{"height":1080,"width":1920}', "16:9"},
                        str(GENERATION_PROFILE["aspect_ratio_label"]),
                    )
                    body_text = (await deep_composed_text(page)).casefold()
                    settings_excerpt = [
                        line.strip()
                        for line in body_text.splitlines()
                        if line.strip() and len(line.strip()) <= 180
                        and any(
                            token in line
                            for token in (
                                "modelo", "model", "resolucao", "resolução", "resolution",
                                "proporcao", "proporção", "widescreen", "quadros", "fps",
                                "duracao", "duração", "duration", "segundos", "seconds",
                                "kling", "1080p",
                            )
                        )
                    ][:30]
                    fps_confirmed = f"{GENERATION_PROFILE['fps']} fps".casefold() in body_text
                    duration_confirmed = any(
                        label in body_text
                        for label in (
                            f"{GENERATION_PROFILE['duration_seconds']} segundos".casefold(),
                            f"{GENERATION_PROFILE['duration_seconds']} seconds".casefold(),
                        )
                    )
                    profile_confirmed = all(
                        (model_confirmed, resolution_confirmed, aspect_confirmed, fps_confirmed, duration_confirmed)
                    )
                finally:
                    await page.keyboard.press("Escape")

                blocking_overlay = await page.evaluate(r'''() => {
                    const seen = new Set();
                    function visible(el) {
                        if (!el?.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 &&
                            style.display !== 'none' && style.visibility !== 'hidden';
                    }
                    function walk(root) {
                        if (!root || seen.has(root)) return null;
                        seen.add(root);
                        const all = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                        for (const el of all) {
                            const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                            const aria = (el.getAttribute?.('aria-label') || '').trim();
                            if (visible(el) && /^(Dismiss|Dispensar|Fechar)$/i.test(aria || text)) {
                                let ancestor = el;
                                while (ancestor) {
                                    const role = ancestor.getAttribute?.('role') || '';
                                    const modal = ancestor.getAttribute?.('aria-modal') === 'true';
                                    const style = ancestor.getBoundingClientRect ? getComputedStyle(ancestor) : null;
                                    const rect = ancestor.getBoundingClientRect?.();
                                    const coversComposer = !!rect && rect.width >= innerWidth * 0.35 && rect.height >= innerHeight * 0.25;
                                    const fixedLayer = style && ['fixed', 'absolute'].includes(style.position) && Number(style.zIndex || 0) >= 10;
                                    if (modal || role === 'dialog' || (coversComposer && fixedLayer)) {
                                        return {label: aria || text, role, modal, coversComposer, fixedLayer};
                                    }
                                    ancestor = ancestor.parentElement || ancestor.getRootNode?.().host || null;
                                }
                            }
                            if (el.shadowRoot) {
                                const nested = walk(el.shadowRoot);
                                if (nested) return nested;
                            }
                        }
                        return null;
                    }
                    return walk(document);
                }''')
                blocking_onboarding = blocking_overlay is not None
                signed_out_reason = await detect_signed_out(page)
                if signed_out_reason:
                    return {
                        "authenticated": False,
                        "production_ui_ready": False,
                        "reason": f"FIREFLY_SESSION_SIGNED_OUT: {signed_out_reason}",
                        "profile_dir": str(profile_dir),
                    }
                production_ui_ready = (
                    all(controls.values()) and profile_confirmed and not blocking_onboarding
                )
                return {
                    "authenticated": True,
                    "production_ui_ready": production_ui_ready,
                    "model": GENERATION_PROFILE["model"] if profile_confirmed else None,
                    "resolution": GENERATION_PROFILE["resolution"] if profile_confirmed else None,
                    "aspect_ratio": GENERATION_PROFILE["aspect_ratio"] if profile_confirmed else None,
                    "fps": GENERATION_PROFILE["fps"] if profile_confirmed else None,
                    "duration_seconds": GENERATION_PROFILE["duration_seconds"],
                    "profile_checks": {
                        "model": model_confirmed,
                        "resolution": resolution_confirmed,
                        "aspect_ratio": aspect_confirmed,
                        "fps": fps_confirmed,
                        "duration": duration_confirmed,
                    },
                    "settings_excerpt": settings_excerpt,
                    "blocking_onboarding": blocking_onboarding,
                    "blocking_overlay": blocking_overlay,
                    "controls": controls,
                    "reason": (
                        "Sessao autenticada e perfil Kling 2.5 Turbo pronto"
                        if production_ui_ready
                        else "FIREFLY_PRODUCTION_UI_NOT_READY: controles, modelo ou overlay inválidos"
                    ),
                    "profile_dir": str(profile_dir),
                }
            finally:
                await context.close()
    except Exception as exc:
        return {
            "authenticated": False,
            "reason": f"PROBE_EXCEPTION: {exc}",
            "profile_dir": str(profile_dir),
        }
