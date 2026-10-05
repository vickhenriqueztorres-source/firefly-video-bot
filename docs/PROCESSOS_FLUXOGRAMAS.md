# 🔄 Processos Operacionais e Fluxogramas — Firefly Video Automation

> **Versão**: 2.0.0  
> **Status**: Produção  
> **Escopo**: Manuais de Operação, Fluxos de Execução e Diagramas de Sequência  

---

## 📌 1. Fluxo de Execução de Ponta a Ponta

O diagrama a seguir descreve a sequência exata de processamento de um take (Job) desde a ingestão via JSON até a gravação final do arquivo `.mp4`.

```mermaid
sequenceDiagram
    autonumber
    actor Operador
    participant CLI as main.py CLI
    participant DB as SQLite JobStore
    participant W as Worker Loop
    participant Page as Patchright Page
    participant DOM as StateReader Engine
    participant FS as FileSystem (saida/)

    Operador->>CLI: python main.py --feed-guide guia.json
    CLI->>DB: INSERT INTO jobs (status='pending')
    Operador->>CLI: python main.py --run --concurrency 1
    CLI->>W: Inicia ParallelBatchExecutor
    
    loop Cada Job Pendente
        W->>DB: claim_job() [CAS: pending -> claimed]
        W->>Page: goto(firefly_url) [Recarregamento limpo]
        W->>Page: Configura Kling 3.0, 720p, 9:16, 5s
        W->>Page: Upload First Frame PNG
        W->>Page: Limpeza DOM JS + Digitação Bézier
        W->>Page: Clica "Gerar"
        W->>DB: transition(claimed -> generating)
        
        loop Polling de Tela (a cada 3s)
            W->>DOM: read_screen_state()
            DOM-->>W: ScreenObservation (STILL_GENERATING / RESULT_READY / CONTENT_REJECTED)
        end
        
        alt Estado = RESULT_READY
            W->>Page: Clica "Baixar"
            Page-->>FS: Download concluído (.mp4)
            W->>DB: transition(generating -> done)
            W->>FS: Copia vídeo para pasta do projeto de produção
        else Estado = CONTENT_REJECTED
            DOM-->>W: Detecta "Não foi possível processar esse prompt"
            W->>DB: transition(generating -> failed-content)
        else Timeout de Geração (10 min)
            W->>DB: transition(generating -> failed-infra)
        end
    end
```

---

## 🛠️ 2. Guia de Operação e Comandos CLI

### 2.1. Ingestão de Guia de Produção
Para registrar novos vídeos na fila de geração:
```powershell
.\.venv\Scripts\python.exe main.py --feed-guide "C:\caminho\para\guia_producao.json"
```

### 2.2. Execução da Automação (Modo Solo ou Concorrente)
Para rodar a fila sequencialmente (Recomendado para estabilidade de abas):
```powershell
.\.venv\Scripts\python.exe main.py --run --concurrency 1
```

Para rodar com múltiplas abas simultâneas no mesmo contexto do Chrome:
```powershell
.\.venv\Scripts\python.exe main.py --run --concurrency 3
```

### 2.3. Verificação de Status da Fila
Para inspecionar o status detalhado no SQLite:
```powershell
.\.venv\Scripts\python.exe main.py --status
```

### 2.4. Reset de Jobs Pendentes (Script Auxiliar)
Caso deseje resetar jobs travados para re-tentativa limpa:
```powershell
.\.venv\Scripts\python.exe C:\Users\brend\.gemini\antigravity\brain\<conv-id>\scratch\update_prompts_and_reset.py
```

---

## 🧼 3. Procedimento de Limpeza e Sanitização do DOM

Para evitar contaminação por cache visual, o robô executa o seguinte protocolo de sanitização a cada job:

```mermaid
graph TD
    A[Início do Job] --> B[Navegação para URL Base do Firefly]
    B --> C[Aguardar domcontentloaded]
    C --> D[Ajustar Controles de Modelo/Resolução]
    D --> E[Upload de Imagem Start Frame]
    E --> F[Injeção JS: page.evaluate]
    F --> G[Limpar innerHTML do Editor Tiptap]
    G --> H[Disparar Eventos DOM input/change]
    H --> I[HumanInput: .fill com Novo Prompt]
    I --> J[Clique em Gerar]
```

---

## 🛡️ 4. Política Anti-Loop e Recuperação Automatizada

1. **Watchdog de Parede (*Wall-Clock Watchdog*)**:
   Se a execução estagnar por mais de `WATCHDOG_WALL_CLOCK` (800.000 ms = 13.3 minutos), o Watchdog encerra o processo do navegador Chromium e força uma reinicialização limpa.
2. **Prevenção contra Moderação Falso-Positiva**:
   A checagem de erros busca estritamente pela string completa de erro da Adobe (`"Não foi possível processar esse prompt"`), impedindo que imagens de *placeholder* interrompam prematuramente a renderização.
