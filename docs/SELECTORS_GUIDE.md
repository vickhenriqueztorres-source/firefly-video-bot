# Guia de Mapeamento de Seletores — Adobe Firefly

Este e o **gating factor** do projeto. Sem seletores confirmados, o State Reader nao funciona, e sem State Reader nada roda. O AI Guard 4.1 proibe chutar seletores — todo seletor comeca como `TODO:SELETOR` e so vira `confirmed=True` apos mapeamento manual.

---

## Por que isso e critico

O State Reader (`read_screen_state()`) e a unica fonte de decisao pos-acao. Ele checa estados em ordem de prioridade e retorna o primeiro que bater. Se os seletores estao errados:

- `result_ready` nunca detecta → video pronto mas sistema acha que ainda esta gerando → timeout do watchdog
- `content_rejected` nao detectado → prompt rejeitado mas sistema acha que esta gerando → loop infinito ate budget
- `error_toast` nao detectado → erro do Firefly mascarado como sucesso ou unknown → debug impossivel

---

## Pre-requisitos

1. Ambiente de setup completo (ver `SETUP.md`)
2. Sessao logada no Firefly (perfil persistente)
3. DevTools aberto (F12) no Chrome

---

## Estados a mapear

O State Reader tem 7 estados. Cada um precisa de **pelo menos um seletor** que seja mutuamente exclusivo (nao pode aparecer quando outro estado esta ativo).

| Estado | O que procurar na UI | Metodo preferido |
|---|---|---|
| LOGGED_OUT | URL muda para /login, /auth, /signin | URL pattern (regex) |
| QUOTA_EXHAUSTED | Toast/modal "sem creditos", "limite atingido" | get_by_text() |
| CONTENT_REJECTED | Modal/toast "conteudo rejeitado", "viola diretrizes" | get_by_text() |
| ERROR_TOAST | Toast generico de erro | get_by_role("alert") |
| RESULT_READY | Botao "Download" visivel, video pronto na tela | get_by_role("button") + texto |
| STILL_GENERATING | Spinner, progress bar, "gerando..." | get_by_role("status") ou CSS |
| UNKNOWN | Nenhum dos acima bateu | (fallback automatico) |

---

## Procedimento de mapeamento

### Passo 1 — Abrir o Firefly com DevTools

```
1. Rodar scripts/first_login.py (abre browser com perfil persistente)
2. Navegar para https://firefly.adobe.com/generate/video
3. Abrir DevTools (F12)
4. Ir para a aba Elements
```

### Passo 2 — Mapear cada estado

Para cada estado abaixo, reproduza a condicao na UI e inspecione o DOM.

#### LOGGED_OUT
- **Como reproduzir:** Fazer logout manualmente (menu → sair)
- **O que inspecionar:** A URL muda? Para que?
- **Seletor esperado:** URL contendo `auth.adobe.com` ou similar
- **Registrar:** O padrao de URL exato

#### STILL_GENERATING
- **Como reproduzir:** Digitar um prompt e clicar em gerar
- **O que inspecionar:** Aparece spinner? Progress bar? Texto "Gerando..."?
- **Seletor esperado:** `get_by_role("status")` ou `get_by_text("Gerando")` ou CSS `[class*="spinner"]`
- **Cuidado:** O spinner pode desaparecer e reaparecer. Precisa de um seletor estavel.

#### RESULT_READY
- **Como reproduzir:** Esperar a geracao terminar (ou usar um prompt simples rapido)
- **O que inspecionar:** Aparece um botao de download? Onde? Qual o texto?
- **Seletor esperado:** `get_by_role("button", name="Download")` ou `get_by_test_id("download-button")`
- **Cuidado:** Pode haver multiplos botoes (compartilhar, download, regenerar). Precisa do botao certo.

#### CONTENT_REJECTED
- **Como reproduzir:** Digitar um prompt que sabe que vai ser rejeitado (violencia explicita, etc.)
- **O que inspecionar:** Aparece modal? Toast? Qual o texto exato?
- **Seletor esperado:** `get_by_text("conteudo")` com filtro, ou `get_by_role("dialog")`
- **Cuidado:** O texto pode variar ("nao permitido", "viola", "rejeitado"). Mapear todas as variacoes.

#### QUOTA_EXHAUSTED
- **Como reproduzir:** Esperar acabar os creditos (ou usar uma conta sem creditos)
- **O que inspecionar:** Toast? Banner? Modal? Qual o texto?
- **Seletor esperado:** `get_by_text("creditos")` ou similar

#### ERROR_TOAST
- **Como reproduzir:** Dificil de reproduzir deliberadamente. Aparece em erros de rede, timeout do servidor, etc.
- **O que inspecionar:** Toast de erro vermelho/amarelo
- **Seletor esperado:** `get_by_role("alert")` — ARIA role generico para alertas

### Passo 3 — Preencher selectors.py

Para cada seletor confirmado, atualizar `firefly_bot/selectors.py`:

```python
# Antes (nao confirmado)
STATE_SELECTORS[ScreenState.RESULT_READY] = SelectorDef(
    method="test_id",
    value="TODO:SELETOR_DOWNLOAD_BUTTON",
    description="Botao de download visivel = video pronto",
    confirmed=False,
)

# Depois (confirmado)
STATE_SELECTORS[ScreenState.RESULT_READY] = SelectorDef(
    method="role",
    value="button:Download",  # role=button, name contem "Download"
    description="Botao de download visivel = video pronto",
    confirmed=True,
)
```

### Passo 4 — Validar mutuamente exclusivo

Para cada par de estados, confirmar que **nao** aparecem ao mesmo tempo:

| Par | Cenario de teste |
|---|---|
| STILL_GENERATING vs RESULT_READY | Durante geracao, botao de download NAO deve estar visivel |
| ERROR_TOAST vs STILL_GENERATING | Erro aparece mas spinner some? Confirmar |
| CONTENT_REJECTED vs RESULT_READY | Rejeicao aparece sem botao de download? |

Se dois estados aparecem simultaneamente, a ordem de prioridade do enum resolve qual prevalece — mas precisa estar ciente do comportamento.

---

## Seletores de acao (nao-estado)

Além dos seletores de estado, mapear tambem os elementos de acao:

| Chave | Descricao | Metodo |
|---|---|---|
| prompt_input | Campo de texto onde digita o prompt | get_by_role("textbox") ou get_by_label() |
| generate_button | Botao que dispara a geracao | get_by_role("button") + texto |
| download_button | Botao/menu de download do resultado | get_by_role("button") + texto |
| export_quality_option | Opcao de qualidade no modal de export (se houver) | get_by_role("menuitem") ou get_by_text() |
| logged_in_marker | Elemento que confirma sessao ativa (avatar, menu) | get_by_role("img") ou similar |
| overlay_close_buttons | Botoes de fechar modais aleatorios (cookies, tips) | get_by_role("button") com texto "Fechar" |

---

## Manutencao

Seletores do Firefly **vao apodrecer com o tempo** (AI Guard 4.1, Arquitetura 5). A centralizacao reduz o custo de manutencao mas nao elimina.

- **Rotina:** Sempre que o sistema entrar em UNKNOWN com screenshot, verificar se a UI do Firefly mudou
- **Sintoma:** Aumento de UNKNOWN no log = seletor quebrou
- **Acao:** Re-mapear o estado afetado e atualizar confirmed=True

---

## Anti-padroes

- NAO chutar `class="btn-primary"` — classes CSS mudam a cada deploy
- NAO usar XPath fragil como `//div[3]/button[2]` — quebra com qualquer reordenacao
- NAO confiar em `id` dinamico (gerado por framework) — muda a cada render
- USAR `get_by_role()` com `name=` — baseado em ARIA, mais estavel
- USAR `get_by_text()` com `exact=False` — tolerante a pequenas variacoes
- USAR `get_by_test_id()` se o Firefly expor data-testid — mais estavel que CSS
