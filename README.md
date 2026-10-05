# 🎬 Firefly Video Bot (Kling 3.0 Automation)

Automação autônoma de geração de vídeos em lote (Image-to-Video) no Adobe Firefly utilizando o modelo **Kling 3.0**, Patchright Chromium com perfil persistente (`data/chrome_profile`) e orquestração assíncrona SQLite WAL (CAS State Machine).

## Multi-provider HSL

O worker tambem aceita `Veo 3.1 Fast` para os Premium Motion Start Frames do Hidden Systems Lab. Cada item do guia pode sobrescrever `model`, `resolution`, `aspect_ratio`, `duration_seconds` e `generate_audio`.

```json
{
  "items": [
    {
      "name": "HSL_018_V02_TAKE_01",
      "image": "HSL_018_V02_TAKE_01_start.png",
      "prompt": "Use the provided first frame...",
      "model": "Veo 3.1 Fast",
      "resolution": "720p",
      "aspect_ratio": "16:9",
      "duration_seconds": 8,
      "generate_audio": true
    }
  ]
}
```

Kling continua sendo o default. Veo aceita somente 4, 6 ou 8 segundos neste fluxo. O toggle de audio e confirmado antes da geracao; um seletor nao confirmado pausa o job em vez de presumir sucesso.

---

## 📚 Documentação Técnica Completa (`docs/`)

Toda a documentação técnica oficial e detalhada está centralizada na pasta `docs/`:

- 📄 **[PRD.md](docs/PRD.md)**: Requisitos de Produto, Objetivos, Estória de Usuário e Limites do Lote.
- 🏗️ **[ARQUITETURA.md](docs/ARQUITETURA.md)**: Arquitetura Técnica, Diagrama de Componentes, SQLite WAL CAS e StateReader Priority.
- 🤖 **[MAPA_AGENTES_SUBAGENTES.md](docs/MAPA_AGENTES_SUBAGENTES.md)**: Mapeamento de Papéis (Tech Lead, Worker Agent, StateReader, Watchdog, Research Subagent).
- 🔄 **[PROCESSOS_FLUXOGRAMAS.md](docs/PROCESSOS_FLUXOGRAMAS.md)**: Diagramas Mermaid de Sequência, Manuais CLI e Sanitização DOM.
- 📚 **[Central de Docs](docs/README.md)**: Sitemap completo da documentação do projeto.

---

## ⚡ Comandos Rápidos de Uso

```powershell
# 1. Executar Testes Unitários de Verificação (26 testes)
.\.venv\Scripts\python.exe -m pytest -q

# 2. Ingestão de Guia de Produção em Lote (JSON)
.\.venv\Scripts\python.exe main.py --feed-guide "C:\caminho\para\guia_producao.json"

# 3. Executar Automação Fila de Vídeo (Modo Serial Estável)
.\.venv\Scripts\python.exe main.py --run --concurrency 1

# 4. Verificar Status do Banco SQLite
.\.venv\Scripts\python.exe main.py --status
```

---

## 🔒 Segurança e Perfil Persistente

- O perfil persistente do Chrome reside em `data/chrome_profile`.
- A sessão de login da Adobe permanece salva e reutilizável entre execuções.
- As renderizações são salvas automaticamente em `saida/` e publicadas na pasta de destino de produção.
