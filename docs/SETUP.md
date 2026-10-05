# Setup — Ambiente de Desenvolvimento

Guia passo a passo para preparar o ambiente da automação Firefly.

---

## 1. Pre-requisitos do sistema

| Requisito | Versao | Por que |
|---|---|---|
| Python | 3.11+ | Async improvements, match statements, type hints |
| Google Chrome | Estavel (nao Dev/Canary) | Patchright usa channel="chrome" com Chrome real |
| libmagic | Sistema operacional | python-magic precisa da lib nativa |
| Display grafico | X11/Wayland (Linux) ou nativo (macOS/Windows) | Execucao headed obrigatoria (RNF1) |

### Linux (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv libmagic1 google-chrome-stable
```

### macOS

```bash
brew install python@3.11 libmagic
# Chrome: baixar de https://www.google.com/chrome/
```

### Windows

- Python 3.11+ de https://python.org
- Google Chrome de https://www.google.com/chrome/
- libmagic: `pip install python-magic-bin` (alternativa ao libmagic nativo)

---

## 2. Ambiente Python

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate  # Windows

pip install --upgrade pip
```

---

## 3. Dependencias do projeto

```bash
pip install patchright humancursor-playwright python-magic ruff pytest
```

### Por cada dependencia

| Pacote | Funcao | Notas |
|---|---|---|
| patchright | Automacao do browser (fork undetected do Playwright) | API identica ao Playwright. Drop-in replacement |
| humancursor-playwright | Movimentos de mouse com curvas de Bezier | Async, compativel com Patchright |
| python-magic | Validacao de MIME type dos downloads | Precisa de libmagic nativo (ou python-magic-bin no Windows) |
| ruff | Lint + formatacao | Substitui flake8 + black + isort |
| pytest | Testes | |

> **Importante:** NAO instalar `playwright` — Patchright ja inclui a API. Instalar ambos causa conflito de versao.

---

## 4. Instalar o browser do Patchright

```bash
patchright install chromium
```

> Patchright baixa um Chromium modificado. Mas na execucao, usamos `channel="chrome"` para usar o Chrome real instalado no sistema. O Chromium do Patchright e fallback.

---

## 5. Estrutura de diretorios

```bash
mkdir -p firefly_bot/{tests,downloads,screenshots,data,docs/adr}
```

---

## 6. Primeiro login (manual)

O sistema nunca faz login automatico (AI Guard 1.4, RULES R11). A sessao e estabelecida manualmente na primeira execucao e persistida no perfil.

```python
# Script auxiliar: scripts/first_login.py
# Abre o browser, voce faz login manualmente no Firefly,
# fecha o browser. O perfil persistente salva a sessao.

from patchright.sync_api import sync_playwright

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="./data/chrome_profile",
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    page = context.pages[0]
    page.goto("https://firefly.adobe.com/")
    input("Pressione Enter apos fazer login e confirmar que esta na pagina de geracao...")
    context.close()
```

Rode uma vez, faca login, confirme que esta logado, feche. Nas proximas execucoes o perfil ja tem a sessao.

---

## 7. Validacao anti-bot

Antes de codificar a automacao, valide que o browser hardening passa nos checks de deteccao:

1. Abrir o browser com o perfil persistente (script acima)
2. Navegar para `https://bot.sannysoft.com/` — todos os campos devem estar em verde
3. Navegar para `https://fingerprint.com/` — score de confianca deve ser alto
4. Navegar para `https://abrahamjuliot.github.io/creepjs/` — sem flags vermelhas de CDP

Se qualquer check falhar, revisar launch args e init scripts antes de continuar.

---

## 8. Configuracao do ruff

Criar `pyproject.toml` na raiz:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.format]
quote-style = "double"
```

---

## 9. Checklist de setup

- [ ] Python 3.11+ instalado
- [ ] Chrome estavel instalado
- [ ] libmagic instalado (python-magic importa sem erro)
- [ ] venv criado e ativado
- [ ] patchright instalado e `patchright install chromium` rodou
- [ ] humancursor-playwright instalado e importa sem erro
- [ ] Perfil persistente criado com login manual feito
- [ ] bot.sannysoft.com — tudo verde
- [ ] fingerprint.com — score alto
- [ ] creepjs — sem flags de CDP
- [ ] ruff check . passa
- [ ] pytest roda (mesmo que vazio)
