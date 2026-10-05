# 📚 Central de Documentação do Projeto — Agente Firefly Video Automation

> **Projeto**: Agente Firefly Video Automation (Kling 3.0 Integration)  
> **Versão**: 2.0.0  
> **Status**: Em Produção  

Bem-vindo à documentação oficial do **Agente Firefly Video Automation**. Esta pasta contém todos os manuais técnicos, arquitetura de sistemas, especificação de requisitos (PRD), mapas de agentes, guias de processos e registros de decisões de arquitetura (ADRs).

---

## 🗺️ Mapa da Documentação (Sitemap)

| Documento | Descrição e Conteúdo |
|---|---|
| 📄 **[PRD.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/PRD.md)** | Documento de Requisitos de Produto. Define a visão do produto, objetivos, requisitos funcionais (RF-01 a RF-06), requisitos não-funcionais, restrições e escopo do lote de produção Rafa Lobo. |
| 🏗️ **[ARQUITETURA.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/ARQUITETURA.md)** | Arquitetura técnica completa do sistema. Inclui diagramas de componentes, máquinas de estado de jobs SQLite WAL, tabela de prioridades do StateReader e esquema físico de banco de dados. |
| 🤖 **[MAPA_AGENTES_SUBAGENTES.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/MAPA_AGENTES_SUBAGENTES.md)** | Mapeamento da hierarquia e papéis de agentes humanos, IAs supervisoras (Antigravity), Workers de automação, StateReader e Watchdogs. |
| 🔄 **[PROCESSOS_FLUXOGRAMAS.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/PROCESSOS_FLUXOGRAMAS.md)** | Processos operacionais detalhados com diagramas Mermaid de sequência, manuais de comandos CLI, rotinas de sanitização do DOM Tiptap e política anti-loop. |
| 🛡️ **[AIGUARD.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/AIGUARD.md)** | Regras de ouro invioláveis de código, anti-loop policy e diretivas de integridade estatística e comportamental. |
| 🎯 **[SELECTORS_GUIDE.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/SELECTORS_GUIDE.md)** | Guia completo de seletores da interface do Adobe Firefly com estratégia de resolução e fallback DOM. |
| 🚀 **[SETUP.md](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/SETUP.md)** | Guia de instalação de ambiente, dependências e inicialização de sessão Chrome. |
| 📜 **[adr/](file:///C:/Users/brend/OneDrive/Desktop/B2%20ENTERPRISE/Canais_/Mateo%20-%20Copia/agente%20firefly/docs/adr)** | Registros de Decisões de Arquitetura (ADRs 0001 a 0004). |

---

## 💻 Estrutura do Código Fonte

```
agente firefly/
├── docs/                        # Documentação Técnica Oficial
│   ├── README.md                # Central de Documentação (Este arquivo)
│   ├── PRD.md                   # Documento de Requisitos de Produto
│   ├── ARQUITETURA.md           # Arquitetura Técnica & Diagramas
│   ├── MAPA_AGENTES_SUBAGENTES.md # Mapeamento da Hierarquia de Agentes
│   ├── PROCESSOS_FLUXOGRAMAS.md # Fluxogramas de Sequência e Manuais CLI
│   ├── AIGUARD.md               # Regras Invioláveis de Proteção
│   ├── SELECTORS_GUIDE.md       # Guia de Seletores do Adobe Firefly
│   ├── SETUP.md                 # Guia de Instalação e Ambiente
│   └── adr/                     # Architectural Decision Records
├── firefly_bot/                 # Pacote Python de Automação
│   ├── main.py                  # Ponto de Entrada da CLI
│   ├── worker.py                # Loop Principal de Automação Worker
│   ├── job_store.py             # Banco de Dados SQLite WAL (CAS State Machine)
│   ├── state_reader.py          # Inspeção e Leitura de Estado DOM
│   ├── selectors.py             # Registro de Seletores da Interface
│   ├── human_input.py           # Movimentos Bézier e Digitação
│   ├── watchdog.py              # Guardião de Processos e Restarts
│   ├── chrome_profile.py        # Gestor de Perfil Persistente Chrome
│   ├── config.py                # Imutável Dataclass de Configuração
│   └── tests/                   # Suíte de Testes Unitários Pytest
├── data/                        # Dados Persistentes (DB SQLite & Chrome Profile)
├── saida/                       # Diretório de Saída de Vídeos MP4 Gerados
└── main.py                      # Atalho de Execução na Raiz
```

---

## ⚡ Comandos Rápidos

```powershell
# 1. Testes Unitários
.\.venv\Scripts\python.exe -m pytest -q

# 2. Ingestão de Guia de Produção
.\.venv\Scripts\python.exe main.py --feed-guide "caminho/para/guia.json"

# 3. Executar Fila de Vídeo (Modo Serial Estável)
.\.venv\Scripts\python.exe main.py --run --concurrency 1

# 4. Checar Status no Banco SQLite
.\.venv\Scripts\python.exe main.py --status
```
