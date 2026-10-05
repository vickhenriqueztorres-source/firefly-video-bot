# ADR-0003: Contexto persistente e execução headed

**Data:** 21/07/2026  
**Status:** Aceito

## Contexto

O Firefly depende de uma sessão autenticada por uma pessoa e a geração de vídeo
é sensível a diferenças entre um navegador normal e um navegador headless.
Guardar credenciais ou automatizar MFA viola o escopo do projeto.

## Decisão

Usar `launch_persistent_context` com um perfil local em
`data/chrome_profile`, `channel="chrome"` e `headless=False`. A primeira
autenticação é feita manualmente; os cookies e demais dados de sessão ficam no
perfil do Chrome, sem serem lidos ou injetados pelo código.

## Consequências

- A execução requer ambiente gráfico e Chrome instalado.
- O perfil é dado local sensível e não deve ser versionado.
- Logout, MFA ou quota esgotada pausam a fila para ação humana.
- O perfil continua pertencendo a um único processo. O ADR-0004 permite várias páginas isoladas dentro desse mesmo contexto.
