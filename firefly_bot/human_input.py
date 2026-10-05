"""Interações variáveis; concentradas para não espalhar temporizações pelo fluxo."""

from __future__ import annotations

import asyncio
import random
from typing import Protocol

from humancursor_playwright import PlaywrightCursor


class LocatorLike(Protocol):
    async def press(self, key: str) -> None: ...
    async def fill(self, value: str) -> None: ...


class HumanInput:
    """Camada async para cursor Bézier e digitação com variação não uniforme."""

    def __init__(self, page: object, *, typo_probability: float = 0.02) -> None:
        self.cursor = PlaywrightCursor(page)
        self.typo_probability = typo_probability

    async def click(self, locator: object) -> None:
        await self.cursor.click_on(locator)

    async def click_element(self, locator: object, description: str) -> None:
        """Nomeia a intenção no chamador e mantém o movimento Bézier centralizado."""
        del description
        await self.click(locator)

    async def human_delay(self, minimum: float, maximum: float) -> None:
        """Jitter comportamental; não é usado para concluir que a UI mudou de estado."""
        await asyncio.sleep(random.uniform(minimum, maximum))

    async def type_prompt(
        self, locator: LocatorLike, text: str, page: object = None
    ) -> None:
        """Preenche somente prompts, nunca campos de autenticação."""
        if page is not None and hasattr(page, "evaluate"):
            try:
                await page.evaluate("""() => {
                    const el = document.querySelector('.tiptap.ProseMirror, textarea[aria-label="Prompt"]');
                    if (el) {
                        if (el.tagName === 'TEXTAREA') {
                            el.value = '';
                        } else {
                            el.innerHTML = '<p></p>';
                        }
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }""")
            except Exception:
                pass
        try:
            await locator.fill("")
        except Exception:
            pass
        await locator.fill(text)
        await asyncio.sleep(random.uniform(0.25, 0.60))
