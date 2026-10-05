"""Limpeza limitada a overlays não terminais, sempre após leitura de estado."""

from __future__ import annotations

import logging
import re

from patchright.async_api import Error as PatchrightError
from patchright.async_api import TimeoutError as PatchrightTimeoutError

from .human_input import HumanInput
from .logging_utils import event
from .selectors import SELECTORS, locator_for


async def _click_if_visible(locator: object, human_input: HumanInput, timeout: int) -> bool:
    if await locator.is_visible(timeout=timeout):
        await human_input.click(locator)
        return True
    return False


async def _dismiss_storage_warning(
    page: object, human_input: HumanInput, logger: logging.Logger, job_id: int
) -> bool:
    """Adobe pode bloquear a geração com um aviso não terminal de armazenamento."""
    try:
        title = page.get_by_text(
            re.compile(
                r"Espa[cç]o de armazenamento insuficiente|insufficient storage space",
                re.IGNORECASE,
            )
        )
        if not await title.is_visible(timeout=500):
            return False

        try:
            dont_show_again = page.get_by_text(
                re.compile(
                    r"N[aã]o mostrar esta mensagem novamente|do not show this message again",
                    re.IGNORECASE,
                )
            )
            await _click_if_visible(dont_show_again, human_input, 500)
        except (PatchrightTimeoutError, PatchrightError):
            pass

        continue_button = page.get_by_role(
            "button", name=re.compile(r"Continuar|Continue", re.IGNORECASE)
        )
        if await _click_if_visible(continue_button, human_input, 1500):
            event(logger, logging.INFO, "storage_warning_dismissed", job_id=job_id)
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(750)
            return True

        fallback = page.get_by_text(re.compile(r"Continuar|Continue", re.IGNORECASE))
        if await _click_if_visible(fallback, human_input, 1500):
            event(logger, logging.INFO, "storage_warning_dismissed", job_id=job_id)
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(750)
            return True
    except (PatchrightTimeoutError, PatchrightError) as exc:
        event(
            logger,
            logging.WARNING,
            "storage_warning_dismiss_failed",
            job_id=job_id,
            error=type(exc).__name__,
        )
    return False


async def dismiss_overlays(
    page: object, human_input: HumanInput, logger: logging.Logger, job_id: int
) -> None:
    """Overlay ausente é normal; erros são logados sem mascarar o fluxo principal."""
    if await _dismiss_storage_warning(page, human_input, logger, job_id):
        return

    # 1. Termos / modal de modelos de parceiros (ex: "Comece a usar modelos de parceiros")
    try:
        partner_terms = page.get_by_text(re.compile(r"modelos de parceiros|partner models|selecione ok", re.IGNORECASE))
        if await partner_terms.count() > 0:
            for btn_name in ["OK", "Ok", "Concordo", "Aceitar", "Entendi", "Continuar"]:
                btn = page.get_by_role("button", name=re.compile(f"^{btn_name}$", re.IGNORECASE))
                if await btn.is_visible(timeout=500):
                    await human_input.click(btn)
                    event(logger, logging.INFO, "partner_models_terms_dismissed", job_id=job_id)
                    if hasattr(page, "wait_for_timeout"):
                        await page.wait_for_timeout(500)
                    break
    except Exception:
        pass

    # 2. O onboarding do novo compositor e banners promocionais
    try:
        clicked = await page.evaluate(
            r"""() => {
                const seen = new Set();
                const isVisible = (node) => {
                    if (!node || !node.getBoundingClientRect) return false;
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const walk = (root) => {
                    if (!root || seen.has(root)) return false;
                    seen.add(root);
                    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const node of nodes) {
                        const label = (node.innerText || node.textContent || '')
                            .replace(/\s+/g, ' ').trim();
                        const aria = (node.getAttribute?.('aria-label') || '').trim();
                        const actionable = node.matches?.(
                            'button, [role="button"], sp-button, [tabindex="0"]'
                        );
                        if (actionable && isVisible(node) &&
                            /^(Dismiss|Dispensar|Fechar|OK|Ok|Entendi|Aceitar|Concordo)$/i.test(aria || label)) {
                            node.click();
                            return true;
                        }
                        if (node.shadowRoot && walk(node.shadowRoot)) return true;
                    }
                    return false;
                };
                return walk(document);
            }"""
        )
        if clicked:
            event(logger, logging.INFO, "onboarding_dom_dismissed", job_id=job_id)
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(500)
            return
        onboarding = page.get_by_role(
            "button", name=re.compile(r"^Dismiss$|^Dispensar$|^Fechar$|^OK$|^Ok$", re.IGNORECASE)
        )
        if await onboarding.is_visible(timeout=700):
            await human_input.click(onboarding)
            event(logger, logging.INFO, "onboarding_dismissed", job_id=job_id)
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(500)
            return
        onboarding_text = page.get_by_text(re.compile(r"^Dismiss$|^Dispensar$|^Fechar$|^OK$|^Ok$", re.IGNORECASE)).last
        if await onboarding_text.is_visible(timeout=700):
            await human_input.click(onboarding_text)
            event(logger, logging.INFO, "onboarding_text_dismissed", job_id=job_id)
            if hasattr(page, "wait_for_timeout"):
                await page.wait_for_timeout(500)
            return
    except (PatchrightTimeoutError, PatchrightError) as exc:
        event(logger, logging.INFO, "onboarding_not_present", job_id=job_id, error=type(exc).__name__)

    if not SELECTORS["overlay_close_buttons"].confirmed:
        return
    try:
        locator = locator_for(page, "overlay_close_buttons")
        if await locator.is_visible(timeout=500):
            await human_input.click(locator)
            event(logger, logging.INFO, "overlay_dismissed", job_id=job_id)
    except (PatchrightTimeoutError, PatchrightError) as exc:
        event(
            logger,
            logging.WARNING,
            "overlay_dismiss_failed",
            job_id=job_id,
            error=type(exc).__name__,
        )
