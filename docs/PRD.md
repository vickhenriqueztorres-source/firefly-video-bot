# 📄 PRD — Documento de Requisitos de Produto (Firefly Video Automation)

> **Versão**: 2.0.0  
> **Status**: Em Produção  
> **Projeto**: Agente Firefly Video Automation (Kling 3.0 Integration)  
> **Última Atualização**: 2026-08-06  

---

## 🎯 1. Visão Geral do Produto

O **Firefly Video Automation Bot** é uma plataforma autônoma de engenharia de automação robótica (RPA / Browser Automation) projetada para orquestrar a geração em escala de vídeos utilizando o modelo **Kling 3.0** diretamente na interface web do **Adobe Firefly**.

O sistema opera no modo **Image-to-Video**, pegando quadros iniciais (*start frames* PNG/JPG), aplicando descrições cinemáticas detalhadas (*prompts*), monitorando a renderização na nuvem da Adobe, baixando os vídeos MP4 resultantes e publicando-os de forma estruturada no diretório de saída do canal de produção.

---

## 🚀 2. Objetivos Principais

1. **Automação 100% Hands-Free**: Eliminar a necessidade de interação manual para upload de imagem, digitação de prompt, ajuste de controles (Kling 3.0, 720p, 9:16, 5s) e download.
2. **Resiliência a Falhas do Servidor**: Tolerar variações de tempo de fila da Adobe, instabilidades no navegador Chromium e tentativas de timeouts de rede através de retentativas inteligentes e Watchdog.
3. **Imunidade a Rejeição Falso-Positiva**: Identificar com 100% de precisão erros reais de moderação de conteúdo da Adobe vs. estados normais de renderização/placeholders DOM.
4. **Isolamento de Sessão & Perfil Persistente**: Manter a sessão de login do usuário salva em perfil Chrome dedicado (`data/chrome_profile`), evitando logins repetidos ou desafios de autenticação.
5. **Garantia de Entrega Sequencial**: Executar lotes de takes em lote (concorrência configurável `--concurrency 1` por padrão para evitar *background tab throttling* do Chromium).

---

## 🛠️ 3. Requisitos Funcionais (User Stories & Funcionalidades)

### RF-01: Ingestão de Guias de Produção (JSON)
- **Descrição**: O sistema deve importar guias de produção em formato JSON descrevendo os takes, imagens de partida, prompts, modelo, proporção, resolução e duração.
- **Comando**: `python main.py --feed-guide <caminho_do_guia.json>`
- **Comportamento**: Popula o banco de dados SQLite (`data/firefly_jobs.db`) criando os registros de jobs com status inicial `pending`.

### RF-02: Gestão Transacional de Fila (CAS JobStore)
- **Descrição**: O banco local SQLite deve operar em modo WAL (*Write-Ahead Logging*) com transações CAS (*Compare-And-Swap*) para evitar condições de corrida (*race conditions*).
- **Ciclo de Vida do Job**:
  - `pending` ➔ `claimed` ➔ `generating` ➔ `done`
  - Estados de Falha: `failed-content` (Prompt rejeitado) ou `failed-infra` (Timeout de infraestrutura/navegador).

### RF-03: Configuração Automática de Parâmetros de Vídeo
Ao abrir a ferramenta no Adobe Firefly (`https://firefly.adobe.com/generate/video`), o bot deve selecionar automaticamente:
- **Modelo**: Kling 3.0 (`kling:firefly:colligo:v3direct`)
- **Resolução**: 720p
- **Proporção**: Vertical (9:16)
- **Duração**: 5 segundos (ou slider configurável)
- **Áudio**: Desativado (`aria-checked="false"`)
- **Primeiro Quadro (Image-to-Video)**: Upload do arquivo PNG local.

### RF-04: Limpeza Rígida de Estado DOM do Editor
- Para prevenir a contaminação de prompts anteriores no editor rico ProseMirror/Tiptap do Firefly:
  - Injeção de script DOM nativo `page.evaluate()` limpando `innerHTML = '<p></p>'` e disparando eventos de `input`.
  - Execução de `.fill("")` antes da digitação Bézier do novo prompt.
  - Recarregamento explícito da página (`page.goto(FIREFLY_VIDEO_URL)`) a cada novo job para limpar a tela de resíduos de erro.

### RF-05: Monitoramento Inteligente de Tela (StateReader)
O leitor de tela lê o DOM Chromium através de seletores confirmados por prioridade:
1. `LOGGED_OUT`: Sessão expirada.
2. `QUOTA_EXHAUSTED`: Limite de créditos esgotado.
3. `CONTENT_REJECTED`: Detecta explicitamente a mensagem *"Não foi possível processar esse prompt"*.
4. `RESULT_READY`: Botão *"Baixar"* ativo e habilitado (`[data-testid="generate-video-download-button"]:not([aria-disabled="true"])`).
5. `STILL_GENERATING`: Indicadores de progresso ou renderização ativa.

### RF-06: Download & Publicação Automática de Arquivos
- Ao detectar `RESULT_READY`, o bot clica no botão *Baixar*, captura o arquivo `.mp4` transmitido via resposta HTTP/WebSocket e o move para:
  - `saida/<nome_do_take>.mp4`
  - Sincronização automática com a pasta do projeto final de produção.

---

## 📐 4. Requisitos Não-Funcionais & Restrições Técnicas

| Categoria | Requisito / Limite | Detalhes |
|---|---|---|
| **Linguagem & Runtime** | Python 3.10+ com `asyncio` | Programação totalmente assíncrona não-bloqueante. |
| **Navegador & Automação** | `patchright` / Playwright | Utiliza biblioteca anti-detecção baseada em Chromium. |
| **Perfil de Usuário** | Chrome Persistent Context | `data/chrome_profile` mantém sessão e cookies gravados. |
| **Banco de Dados Local** | SQLite WAL | `data/firefly_jobs.db` com controle de concorrência transacional. |
| **Timeout de Geração** | `GENERATION_BUDGET = 600.000 ms` | 10 minutos por renderização (cobre filas de pico do Kling 3.0). |
| **Watchdog Wall Clock** | `WATCHDOG_WALL_CLOCK = 800.000 ms` | 13.3 minutos de tolerância máxima antes de reiniciar o worker. |
| **Tentativas Máximas** | `MAX_ATTEMPTS = 3` | Tentativas de retentativa por infraestrutura. |
| **Limite Horário** | `MAX_GENERATIONS_PER_HOUR = 20` | Controle preventivo contra bloqueio por taxa de uso. |

---

## 🎨 5. Especificações da Produção Rafa Lobo (Exemplo Ativo)

- **Projeto Alvo**: `RL-20260805-CINCO-MINUTOS-TESTE-001`
- **Diretório Final de Vídeos**:
  `C:\Users\brend\OneDrive\Desktop\B2 ENTERPRISE\Canais_\02 canais Lifestyle_\Rafa Lobo\Producoes\RL-20260805-CINCO-MINUTOS-TESTE-001\07-start-frames\video\`
- **Lote de Takes**:
  - `P01_ORANGE_ESCAPE` (Takes T01, T02, T03) — *Câmera baixa, laranja rolando do saco*
  - `P02_RAFAS_CHOICE` (Takes T01, T02, T03) — *Rafa aperta botão e trava porta com o pé*
  - `P03_LEAVES_ELEVATOR` (Takes T01, T02, T03) — *Rafa sai do elevador e recolhe a laranja*
  - `P04_ELEVATOR_LEAVES` (Takes T01, T02, T03) — *Rafa devolve a laranja e o elevador fecha*
