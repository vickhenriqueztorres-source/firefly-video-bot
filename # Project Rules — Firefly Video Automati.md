# Project Rules — Firefly Video Automation

> Regras operacionais para geração de código neste projeto.
> Formato compatível com `.cursorrules`, `CLAUDE.md`, `AGENTS.md`, `.windsurfrules`.
> Complementa (não substitui) o `AIGUARD.md`, o `PRD.md` e o `ARQUITETURA.md`.

---

## Contexto do projeto

Automação de geração de vídeos no Adobe Firefly **via navegador** (Patchright, não API).
Site pesado, geração assíncrona lenta (minutos), sujeito a anti-bot.
Sessão logada manualmente. Coordenador único, com 1–6 abas isoladas. Execução headed.
Arquitetura: **fila durável (SQLite) + worker resiliente + watchdog externo**.
Princípio central: **nunca inferir sucesso por ausência de sinal**.

---

## Stack fixa (não trocar sem discussão explícita)

- **Linguagem:** Python 3.11+
- **Automação:** Patchright (async API compatível com Playwright) + contexto persistente (`launch_persistent_context`)
- **Interação:** `humancursor-playwright` para cursor Bézier e digitação variável em `human_input.py`
- **Persistência:** SQLite via `sqlite3` da stdlib (sem ORM)
- **Log:** `logging` da stdlib, formato estruturado (key=value)
- **Config:** dataclass única, sem framework de config
- **Testes:** `pytest`
- **Formatação:** `ruff` (lint + format)

Sem Selenium. Sem `undetected-chromedriver`. Sem `playwright-stealth`. Sem SQLAlchemy. Sem FastAPI/Flask nesta versão.

---

## Estrutura de pastas obrigatória

```
firefly_bot/
├── config.py           # dataclass Config, nada mais
├── selectors.py        # dicionário único de seletores da UI
├── job_store.py        # camada SQLite, atômica
├── human_input.py      # clique/digitação variável (somente prompts)
├── session.py          # Session Manager (verifica login)
├── state_reader.py     # read_screen_state() + enum de estados
├── overlays.py         # dismiss_overlays()
├── export_flow.py      # submáquina de exportação
├── downloads.py        # validação MIME + publicação atômica
├── worker.py           # orquestração async de um job
├── watchdog.py         # processo pai que vigia o worker
├── main.py             # entrypoint
└── tests/
```

Cada módulo tem **uma** responsabilidade. Não misturar.

---

## Regras de código

### R1 — Sincronização
- ❌ `time.sleep(N)` para esperar UI
- ❌ `page.wait_for_timeout(N)` no fluxo
- ❌ `page.wait_for_load_state("networkidle")`
- ✅ `page.wait_for_selector(sel, state="visible", timeout=...)`
- ✅ Polling explícito em loop, com `state_reader` decidindo saída
- ✅ `sleep` com jitter entre jobs, no polling explícito de estados e em `human_input.py` para cadência variável por tecla

### R2 — Seletores
- Todos vivem em `selectors.py` como constantes nomeadas
- Preferir `page.get_by_role(...)`, `get_by_text(...)`, `get_by_label(...)`
- CSS/xpath só se `get_by_*` não resolver, com comentário do porquê
- Seletor não confirmado sai como `TODO:SELETOR` explícito com descrição
- **Nunca chutar** classe CSS do Firefly

### R3 — Estados
Enum fixo em `state_reader.py`:

```python
class ScreenState(Enum):
    LOGGED_OUT = "logged_out"
    QUOTA_EXHAUSTED = "quota_exhausted"
    CONTENT_REJECTED = "content_rejected"
    ERROR_TOAST = "error_toast"
    RESULT_READY = "result_ready"
    STILL_GENERATING = "still_generating"
    UNKNOWN = "unknown"
```

- Ordem de checagem = ordem do enum acima
- `read_screen_state()` retorna o **primeiro** que bater
- `UNKNOWN` **nunca** é tratado como sucesso
- Adicionar estado novo = adicionar no enum + na tabela de decisão, nunca `if` solto

### R4 — Job Store
Estados válidos:
```
pending → claimed → generating → done
                   → stale_generating → pending (somente após expirar o budget)
                              → failed-content
                              → failed-infra
                              → dead
```
Estados do sistema (fila inteira):
```
running | paused-auth | paused-quota | paused-blocked
```

- Transições via `UPDATE ... WHERE id=? AND status=?` (atômicas)
- Verificar linhas afetadas — se 0, alguém mudou o estado no meio, aborta
- Nunca `DELETE` de jobs falhos (auditoria)
- `generation_started_at` é distinto de `claimed_at`
- Reconciliação no boot: `claimed` sem geração volta a `pending`; geração dentro do budget vira `stale_generating`; só geração expirada volta a `pending`

### R5 — Timeouts (nomes obrigatórios, valores em `config.py`)
- `SELECTOR_TIMEOUT` — esperar elemento aparecer (~60s)
- `GENERATION_BUDGET` — máximo esperando geração (~300s)
- `WATCHDOG_WALL_CLOCK` — teto total de um job (~600s)
- `NAV_TIMEOUT` — navegação (~90s)
- `JITTER_MIN` / `JITTER_MAX` — pausa entre jobs (~8s–20s)

Cada um documentado com comentário do porquê do valor.

### R6 — Log
Formato:
```
2026-07-21 14:03:22 | INFO  | job_id=42 state=STILL_GENERATING action=poll elapsed=45s
```
- Sempre incluir `job_id` quando houver
- `screenshot=path/to/file.png` quando capturar
- Nunca `print()`
- Nunca `logging.exception()` sem contexto adicional

### R7 — Exceções
- ❌ `except:` nu
- ❌ `except Exception: pass`
- ✅ Capturar específico (`TimeoutError`, `PatchrightError`) quando dá
- ✅ Toda captura loga com contexto + tira screenshot antes de decidir fluxo
- ✅ Erros terminais (`failed-content`, `dead`) não têm retry automático

### R8 — Watchdog
- Processo separado (`subprocess.Popen` do worker), não thread
- Em POSIX mata o process group com `SIGKILL`; em Windows usa `taskkill /T /F` para encerrar a árvore
- Reinicia com backoff exponencial (limite: 5 restarts em 1h → aborta)
- Nunca fecha browser "educado" quando decide que travou

### R9 — Download
- `page.expect_download()` com timeout dedicado e submáquina para modal/qualidade/renderização
- Salvar em `downloads/{job_id}_{filename}`
- Validar: arquivo existe, `os.path.getsize() > MIN_FILE_SIZE_BYTES`, MIME real `video/*` via `python-magic` e extensão aceita
- Só marca `done` **depois** da validação

### R10 — Overlays
- `dismiss_overlays()` roda **antes** de: digitar prompt, clicar gerar, clicar baixar
- Lista de seletores conhecidos em `overlays.py`
- Falha silenciosa aceitável aqui (overlay não existir é o caso normal)

### R11 — Login
- ❌ Nunca automatizar login/MFA/cookie injection
- ✅ Detectar logout via URL contendo `/login|/auth|/signin`
- ✅ Detectar via ausência de `logged_in_marker`
- ✅ Ao detectar: job atual volta pra `pending`, sistema vai pra `paused-auth`, sai limpo

### R12 — Paralelismo
- Um único processo coordenador é dono do perfil persistente
- ✅ Uma instância isolada de `Worker` por aba, reunidas pelo coordenador
- ✅ `asyncio.gather` somente no coordenador, sem compartilhar `page` ou `HumanInput`
- ❌ Dois processos usando ou encerrando o mesmo perfil Chrome
- ❌ ThreadPoolExecutor no fluxo de geração
- Multi-conta continua sendo outro processo, outro perfil e outro SQLite

---

## Regras de comportamento da IA

### C1 — Não expandir escopo
Se pedi worker serial, não entrega multi-conta "porque é melhor". Sugestões vão em comentário no fim.

### C2 — Não trocar stack silenciosamente
Patchright, SQLite, Python e `humancursor-playwright`. Alternativa vira proposta explícita, nunca troca.

### C3 — Não inventar seletor da Adobe
Todo seletor real da UI do Firefly sai como `TODO:SELETOR` até eu confirmar.

### C4 — Não engolir erro
Melhor um `raise` explícito que um `except: pass` disfarçado.

### C5 — Não gerar código-fantasma
Se depende de seletor não preenchido, o comentário no topo do arquivo avisa que **precisa preencher pra rodar**.

### C6 — Perguntar quando ambíguo
Melhor perguntar 3 coisas de uma vez que codar 3 hipóteses erradas.

### C7 — Comentários explicam *por que*, não *o que*
`# incrementa i` é ruído. `# aceita UNKNOWN como falha porque sucesso silencioso é o pior bug possível aqui` é útil.

### C8 — Idioma
Código em inglês (variáveis, funções, classes). Comentários e docstrings em português. Log em português.

### C9 — Type hints obrigatórios
Toda função pública tem type hints. `Any` só como último recurso, com comentário.

### C10 — Sem magia
Sem decorators exóticos, sem metaclasses, sem `exec`/`eval`, sem monkey patch. Código óbvio ganha de código esperto.

---

## Checklist antes de entregar código

A IA precisa marcar explicitamente ao final:

- [ ] Nenhum `sleep` fixo sincroniza com UI
- [ ] Nenhum `networkidle`
- [ ] Nenhum sucesso inferido por ausência
- [ ] Seletores todos em `selectors.py`
- [ ] Transições de Job Store são atômicas
- [ ] Reconciliação de boot existe
- [ ] Download tem validação de tamanho
- [ ] Retries têm teto explícito
- [ ] Nenhuma exceção engolida
- [ ] Watchdog é processo separado
- [ ] Zero tentativa de login automático
- [ ] `UNKNOWN` vira falha com screenshot

Se algum item não marca, a entrega **não está pronta** — explicita o que falta.

---

## Referências deste projeto

- `PRD.md` — o que o sistema faz e não faz
- `ARQUITETURA.md` — como as peças se encaixam
- `AIGUARD.md` — regras defensivas expandidas
- `RULES.md` — este arquivo, regras de código
