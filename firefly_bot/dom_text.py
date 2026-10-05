"""Leitura de texto composto, incluindo controles dentro de Shadow DOM."""

from __future__ import annotations


async def deep_composed_text(page: object) -> str:
    return await page.evaluate(r'''() => {
        const seenRoots = new Set();
        const values = new Set();
        const add = (value) => {
            const normalized = String(value || '').replace(/\s+/g, ' ').trim();
            if (normalized) values.add(normalized);
        };
        function walk(root) {
            if (!root || seenRoots.has(root)) return;
            seenRoots.add(root);
            const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
            for (const el of elements) {
                for (const node of Array.from(el.childNodes || [])) {
                    if (node.nodeType === Node.TEXT_NODE) add(node.textContent);
                }
                add(el.getAttribute?.('aria-label'));
                add(el.getAttribute?.('aria-valuetext'));
                add(el.getAttribute?.('value'));
                if (el.shadowRoot) walk(el.shadowRoot);
            }
        }
        add(document.body?.innerText);
        walk(document);
        return Array.from(values).join('\n');
    }''')
