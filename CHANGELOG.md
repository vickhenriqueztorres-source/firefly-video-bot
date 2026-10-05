# Changelog

Historico de versões dos documentos do projeto.

---

## [1.1] — 2026-07-21

### Adicionado
- `SETUP.md` — Guia de setup do ambiente (Python, Patchright, Chrome, libs, validacao anti-bot)
- `SELECTORS_GUIDE.md` — Guia de mapeamento manual de seletores da UI do Firefly
- `adr/0001-patchright-over-playwright.md` — ADR: Patchright no lugar de Playwright puro
- `adr/0002-sqlite-wal-as-job-store.md` — ADR: SQLite com WAL mode como Job Store
- `adr/0003-persistent-context-headed-mode.md` — ADR: Contexto persistente e execucao headed
- `CHANGELOG.md` — Este arquivo
- `README.md` — Visao geral do projeto, stack, estrutura, quick start

### Alterado
- `AIGUARD.md` secao 4.4: Stack atualizada de "Playwright + SQLite" para "Patchright + SQLite"
- `AIGUARD.md` secao 4.6: Nova regra — hardening aprovado (Patchright, Chrome real, humancursor-playwright), limites inviolaveis
- `RULES.md`: Stack fixa atualizada com Patchright, humancursor-playwright, python-magic
- `RULES.md`: Estrutura de pastas obrigatoria adicionada
- `RULES.md` R4: Estado stale_generating adicionado ao fluxo de estados
- `RULES.md` R5: Timeouts com valores de referencia adicionados
- `RULES.md` R9: Download com validacao de MIME via python-magic

---

## [1.0] — 2026-07-21

### Adicionado
- `PRD.md` — Product Requirements Document (escopo, requisitos, riscos)
- `ARQUITETURA.md` — Documento de arquitetura (componentes, fluxos, decisoes)
- `AIGUARD.md` — AI Guard: regras defensivas para geracao de codigo por IA
- `RULES.md` — Regras operacionais de codigo (.cursorrules / CLAUDE.md / AGENTS.md)
