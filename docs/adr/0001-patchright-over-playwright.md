# ADR-0001: Patchright no lugar de Playwright puro

**Data:** 21/07/2026
**Status:** Aceito

---

## Contexto

O projeto automatiza a UI do Adobe Firefly via browser. O Adobe usa infraestrutura enterprise de deteccao de automacao (provavelmente Cloudflare ou similar). Playwright puro e detectavel em milissegundos pelos seguintes vetores:

1. `navigator.webdriver = true` — flag W3C exposta por padrao
2. CDP `Runtime.enable` leak — Chrome DevTools Protocol deixa artefatos detectaveis em isolated worlds
3. CDP `Console.enable` leak — expoe CDP ativo
4. `--enable-automation` flag presente nos launch args padrao
5. TLS Fingerprinting (JA3/JA4) — handshake TLS do Chromium nao bate com Chrome real (nao resolvido por Patchright, mas mitigado por `channel="chrome"`)

## Decisao

Usar **Patchright** — fork do Playwright que patches os leaks de CDP no nivel do driver, em tempo de compilacao. E um drop-in replacement: mesma API, mesmo package name, mesmos tipos.

## Alternativas consideradas

| Alternativa | Por que descartada |
|---|---|
| Playwright puro + add_init_script manual | Nao resolve CDP leaks (Runtime.enable, Console.enable). Init scripts em page nao cobrem iframes |
| playwright-stealth (plugin) | Nao mantido, nao resolve leaks de CDP, viola AI Guard 4.6 |
| undetected-chromedriver (Selenium) | Troca de stack (Selenium), menos confiavel, viola RULES C2 |
| nodriver (Puppeteer undetected) | Stack diferente (Node.js), viola RULES C2 |
| Browser de automacao comercial (Browserless, etc.) | Dependencia externa, custo, viola RNF4 |

## Consequencias

**Positivas:**
- Passa em Cloudflare, Datadome, Akamai, Fingerprint.com, CreepJS e Sannysoft
- Mesma API do Playwright — sem curva de aprendizado, sem mudanca de codigo
- Mantido ativamente (ultima atualizacao: 2026)

**Negativas:**
- Patchright e um fork — pode desync da versao upstream do Playwright
- Patches de stealth expiram em 6-12 meses (Chrome ou anti-bot atualizam)
- TLS/JA3 fingerprinting nao e resolvido por Patchright — mitigado separadamente com channel="chrome"
- Monitoramento semanal de score de deteccao e obrigatorio (ver SETUP.md secao 7)

**Risco residual:** Deteccao de automacao e mitigada, nao eliminada. O PRD secao 4 ja prevê isso: "nao contorna captcha/bot-detection de forma ativa — apenas detecta e pausa".
