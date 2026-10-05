# Firefly Video Automation

Automação de geração de vídeos no Adobe Firefly via navegador, com fila durável, recuperação de crash e vigilância externa.

> **Aviso:** Este projeto automatiza a UI de um site de terceiros (Adobe Firefly). Não usa API oficial. Está sujeito a mudanças de interface, detecção de automação e limites de quota. O sistema pausa — nunca contorna — em caso de bloqueio.

---

## Documentação do projeto

| Arquivo | Função |
|---|---|
| `PRD.md` | O que o sistema faz e não faz (escopo, requisitos, riscos) |
| `ARQUITETURA.md` | Como as peças se encaixam (componentes, fluxos, decisões) |
| `AIGUARD.md` | Regras defensivas para IA gerar código (proibições, checagens) |
| `RULES.md` | Regras de código (.cursorrules / CLAUDE.md / AGENTS.md) |
| `SETUP.md` | Como preparar o ambiente (Python, Patchright, Chrome, libs) |
| `SELECTORS_GUIDE.md` | Como mapear os seletores da UI do Firefly manualmente |
| `adr/` | Architecture Decision Records (decisões arquiteturais formais) |
| `CHANGELOG.md` | Histórico de versões dos documentos |

---

## Stack

- **Python 3.11+**
- **Patchright** (fork undetected do Playwright) — automação do browser
- **Chrome real** em perfil persistente (`launch_persistent_context`)
- **humancursor-playwright** — movimentos de mouse com curvas de Bezier
- **SQLite** (WAL mode) — fila de jobs duravel
- **python-magic** — validação de MIME type dos downloads
- **pytest** — testes
- **ruff** — lint e formatação

Sem Selenium. Sem undetected-chromedriver. Sem playwright-stealth. Sem ORM.

---

## Principios centrais

1. **Nunca inferir sucesso por ausência de sinal.** Todo estado é verificado positivamente.
2. **Estado mora no banco, não na memória.** Se o processo morrer, retoma do ponto certo.
3. **Login é sempre manual.** O sistema detecta logout e pausa — nunca autentica sozinho.
4. **Worker único, serial.** Escala é multi-conta/multi-máquina, fora do escopo v1.
5. **Watchdog é processo externo.** Se a página travar, o watchdog mata e reinicia.

---

## Estrutura de pastas

```
firefly_bot/
├── config.py           # dataclass Config, nada mais
├── selectors.py        # dicionário único de seletores da UI
├── job_store.py         # camada SQLite, atomica
├── human_input.py       # clique/digitacao variavel
├── session.py           # Session Manager (verifica login)
├── state_reader.py      # read_screen_state() + enum de estados
├── overlays.py          # dismiss_overlays()
├── export_flow.py       # submaquina de exportacao
├── downloads.py         # validacao MIME + publicacao atomica
├── worker.py            # orquestracao async de um job
├── watchdog.py          # processo pai que vigia o worker
├── main.py              # entrypoint
├── tests/
├── downloads/           # arquivos baixados pelo worker
├── screenshots/         # capturas de tela de falhas
├── data/
│   └── firefly_jobs.db  # banco SQLite
└── docs/
```

---

## Quick start

> Pre-requisitos detalhados em `SETUP.md`.

```bash
# 1. Clonar e criar venv
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependencias
pip install patchright humancursor-playwright python-magic ruff pytest

# 3. Instalar Chrome do Patchright
patchright install chromium

# 4. Fazer login manual no Firefly (primeira vez)
#    O script abre o browser, voce faz login, fecha.
#    O perfil persistente salva a sessao.

# 5. Mapear seletores da UI (ver SELECTORS_GUIDE.md)
#    Sem isso, o State Reader nao funciona.

# 6. Alimentar a fila
python main.py --feed prompts.json

# 7. Rodar o worker (com watchdog)
python main.py --run
```

---

## Comandos

| Comando | Descrição |
|---|---|
| `python main.py --feed prompts.json` | Carrega prompts no Job Store |
| `python main.py --run` | Inicia watchdog + worker |
| `python main.py --status` | Mostra status da fila |
| `python main.py --reconcile` | Roda reconciliação manual |
| `python main.py --resume` | Retoma fila pausada (após login manual) |
| `ruff check .` | Lint |
| `pytest` | Testes |

---

## Licença

Uso interno. Não distribuir.
