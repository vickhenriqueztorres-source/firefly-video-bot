# AI Guard — Automação de Geração de Vídeo no Adobe Firefly

**Versão:** 1.0
**Propósito:** documento de restrições e checagens obrigatórias para qualquer IA (Cursor, Claude Code, Copilot, v0, etc.) que for gerar ou modificar código deste projeto. Não é opcional — é o filtro que precede qualquer entrega.
**Como usar:** cole no início do prompt de codificação, ou anexe como regra permanente do projeto (`.cursorrules`, `CLAUDE.md`, `AGENTS.md`, system prompt).

---

## 0. Regra-mãe

Antes de escrever qualquer linha de código, a IA precisa ser capaz de responder — pra si mesma — a três perguntas:

1. **Qual estado exato eu estou verificando aqui?** (Nunca "esperar acontecer".)
2. **Se isso falhar silenciosamente, como o sistema descobre?**
3. **Se o processo morrer nesta linha agora, o que acontece no próximo boot?**

Se qualquer resposta for "não sei" ou "vai dar certo", **para. Não implementa.** Pede esclarecimento primeiro.

---

## 1. Proibições absolutas (nunca, em hipótese nenhuma)

Essas são falhas de projeto, não erros de estilo. Cada uma quebra a arquitetura inteira.

### 1.1 Nada de `sleep` fixo para sincronizar com a UI
❌ `time.sleep(30)` esperando a geração terminar
❌ `await page.wait_for_timeout(5000)` no meio do fluxo
✅ Só se aceita `sleep` como **jitter humano** entre jobs (com valor aleatório) e como intervalo de polling explícito dentro de um loop de verificação de estado.

### 1.2 Nada de `wait_for_load_state("networkidle")`
❌ O Firefly tem polling/websocket constante. `networkidle` **nunca dispara**. Usar isso é congelar o script.
✅ Espera é sempre por seletor visível de um estado esperado.

### 1.3 Nunca inferir sucesso por ausência
❌ "O spinner sumiu, então deu certo" → **falso-sucesso**, a raiz de 90% dos bugs deste projeto.
✅ Sucesso é sempre a presença positiva de `result_ready`. Nenhum outro estado pode ser interpretado como sucesso, nem por eliminação.

### 1.4 Nunca tentar login automatizado
❌ Digitar email/senha, resolver MFA, injetar cookie de sessão vindo de outro lugar.
✅ Se detectou logout, **pausa e alerta**. Ponto final. Login é sempre ação humana explícita fora do sistema.

### 1.5 Nunca resetar/limpar o Job Store automaticamente
❌ "Como o schema mudou, vou dropar a tabela e recriar."
❌ Deletar jobs `dead`/`failed` para "limpar a fila".
✅ Migração é ação manual, explicitada em código de migração versionado. Jobs falhos ficam no banco pra auditoria.

### 1.6 Nunca retry infinito em nenhum estado
❌ `while True: try: ... except: continue`
✅ Todo retry tem teto explícito (`attempts < N`), e todo estado terminal (`failed-content`, `paused-*`) **não** é retentado automaticamente.

### 1.7 Nunca engolir exceção sem registrar
❌ `try: ... except: pass`
❌ `except Exception: return None`
✅ Toda exceção capturada vira log estruturado com contexto (job_id, estado, screenshot) antes de qualquer decisão de fluxo.

### 1.8 Nunca marcar job como `done` antes de validar o arquivo
❌ Confiar no evento de download.
✅ Só marca `done` depois de confirmar: arquivo existe, tem tamanho > mínimo plausível, extensão correta.

### 1.9 Nunca fechar o browser educadamente quando o watchdog acionar
❌ `await browser.close()` esperando resposta.
✅ Se o watchdog decidiu que travou, **mata o processo à força** (kill do PID). Fechar educado assume que o browser responde — e a premissa do watchdog é que ele **não** responde.

### 1.10 Nunca disputar o mesmo perfil entre processos
❌ Dois processos, dois contextos persistentes ou dois watchdogs usando o mesmo `user_data_dir`.
✅ Um coordenador abre um contexto e cria N abas; cada aba possui Worker, page e estado próprios. Multi-conta continua exigindo outro perfil e outro processo.

---

## 2. Regras estruturais (como o código precisa ser feito)

### 2.1 Seletores só num único módulo
Existe **um** arquivo/dicionário centralizando **todos** os seletores da UI. Nenhum seletor CSS/xpath/texto pode aparecer inline no meio do fluxo.

- Preferir `get_by_role`, `get_by_text`, `get_by_label` a CSS/xpath frágil.
- Cada seletor tem uma chave nomeada (`prompt_input`, `generate_button`, etc.).
- Erro de "elemento não encontrado" registra a **chave** que falhou, não string genérica.

### 2.2 State Reader é a única fonte de decisão pós-ação
Depois de qualquer ação relevante (clicar gerar, clicar baixar, entrar na página), a próxima decisão de fluxo passa **obrigatoriamente** pelo `read_screen_state()`. Não pode existir `if botão_x.is_visible()` solto tomando decisão de fluxo.

Ordem de prioridade fixa:
```
logged_out > quota_exhausted > content_rejected > error_toast >
result_ready > still_generating > unknown
```

`unknown` **nunca** é sucesso.

### 2.3 Estado do progresso mora no Job Store, não na memória
Variáveis de instância tipo `self.current_prompt`, `self.progress`, `self.last_result` são **anti-padrão** para este projeto. Se o processo morrer, essas variáveis somem. O que importa vive no banco, com transação.

### 2.4 Toda operação no Job Store é atômica
Transição de estado é `UPDATE ... WHERE status = 'X'` com verificação de linhas afetadas. Nada de "leio, mudo em Python, gravo" (race condition entre worker e reconciliação).

### 2.5 Timeouts têm nomes e significados diferentes
Não existe `TIMEOUT = 60`. Existe:
- `SELECTOR_TIMEOUT` (espera de elemento aparecer)
- `GENERATION_BUDGET` (tempo máximo esperando `still_generating` sair)
- `WATCHDOG_WALL_CLOCK` (tempo total de um job antes de considerar travado)
- `NAV_TIMEOUT` (navegação de página)

Cada um com valor próprio, documentado por quê.

### 2.6 Watchdog é processo separado, não thread
Se o watchdog roda dentro do mesmo processo que travou, ele trava junto. A vigilância de wall-clock precisa ser externa (processo pai, systemd, ou similar).

### 2.7 Log é estruturado, não `print`
Cada linha de log tem: timestamp, job_id (se aplicável), estado, ação, resultado. Formato consistente pra permitir busca (`grep job_id=42`).

---

## 3. Checagens obrigatórias antes de dar merge / dar como pronto

A IA precisa **listar explicitamente**, ao final da entrega, que checou cada item:

- [ ] Nenhum `sleep` fixo no fluxo de sincronização com a UI.
- [ ] Nenhum `networkidle`.
- [ ] Nenhum ponto do código conclui sucesso por ausência de estado negativo.
- [ ] Todos os seletores estão no módulo central.
- [ ] Toda transição de estado do Job Store é atômica e logada.
- [ ] Reconciliação de boot está implementada e testada com um cenário de "matar no meio".
- [ ] Download tem validação de tamanho, não só evento.
- [ ] Todo retry tem teto explícito.
- [ ] Nenhuma exceção é engolida sem log estruturado.
- [ ] Watchdog roda fora do processo do worker.
- [ ] Nenhuma tentativa de login automático em nenhum lugar.
- [ ] `unknown` do State Reader vira falha com screenshot, nunca sucesso.

Se qualquer item não puder ser marcado, a entrega **não está pronta** — explicita o que falta antes de dar como concluída.

---

## 4. Comportamentos proibidos da IA durante a geração de código

### 4.1 Não inventar seletor da Adobe Firefly
A IA **não conhece** os seletores reais do Firefly, e eles mudam. Todo seletor sai como `TODO:SELETOR` com comentário do que ele representa. Chutar CSS class name é pior que deixar TODO — dá ilusão de pronto.

### 4.2 Não assumir estrutura da UI que não foi confirmada
"Provavelmente tem um botão de download no canto direito" — não. Se não foi confirmado, é hipótese, e vai no comentário como hipótese, não no código como fato.

### 4.3 Não expandir escopo sem pedir
Se o PRD diz "worker único serial", a IA **não** pode entregar uma versão multi-conta "que é melhor". Escopo é o do PRD. Sugestões de expansão vão em comentário no final, separadas.

### 4.4 Não trocar a stack sem justificar
Patchright + SQLite foi decisão de arquitetura registrada na versão 1.1. Se a IA "prefere" Selenium, Playwright puro ou Postgres, isso é uma proposta explícita — nunca uma troca silenciosa.

### 4.5 Não gerar código de "exemplo" que não roda
Se o código depende de um seletor que ainda não foi preenchido, o comentário deixa claro que **precisa preencher pra rodar**. Não entrega como se estivesse pronto.

### 4.6 Hardening aprovado e limites invioláveis
Patchright, Chrome real em perfil persistente e `humancursor-playwright` são decisões explícitas desta versão. Não adicionar outros wrappers de stealth, injeção de credenciais, solução de captcha ou tentativa ativa de contornar desafios. Ao detectar bloqueio, a fila pausa.

### 4.7 Não misturar responsabilidade de camadas
Job Store não sabe nada de Playwright. Worker não escreve SQL cru. State Reader não decide o que fazer, só reporta. Se um módulo começou a fazer duas coisas, a IA quebra em dois antes de continuar.

---

## 5. Regras de resposta a erro em runtime

Quando o código encontrar um estado inesperado, a resposta padrão é, em ordem:

1. **Registrar** — log estruturado com estado, job_id, contexto.
2. **Capturar** — screenshot da página em `screenshots/{job_id}_{timestamp}.png`.
3. **Classificar** — se cai em algum estado conhecido, aplica a regra do estado. Se não, é `unknown`.
4. **Decidir** — retry (dentro do teto), pausa (se `paused-*`), ou marca como falha terminal.
5. **Nunca** — continuar como se nada tivesse acontecido.

---

## 6. O que a IA deve perguntar antes de codificar, se não estiver claro

- Onde exatamente ficam os arquivos de saída? (Pasta, nomenclatura.)
- O formato de entrada dos prompts é lista simples ou objeto com parâmetros (aspect ratio, duração)?
- Qual o teto de gerações por hora esperado?
- Existe integração com Supabase nesta versão, ou é só SQLite local?
- O watchdog roda como script separado, systemd, PM2, ou loop-pai simples?

Perguntar é sempre preferível a assumir e entregar errado.

---

## 7. Regra final

Se a IA se pegar pensando "isso aqui é uma exceção pequena, dessa vez pode" para qualquer regra desse documento — **não pode.** As regras existem porque cada uma corresponde a um bug real que este projeto vai encontrar. Ignorar uma é garantir que o bug aconteça.
