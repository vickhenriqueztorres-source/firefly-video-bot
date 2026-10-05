# 🌐 Guia de Conexão Remota MCP via SSH (PC 2 -> PC 1)

Este guia contém as instruções e dados exatos para fazer outro computador (**PC 2**) consultar em tempo real os servidores MCP hospedados neste computador principal (**PC 1**).

---

## 📌 Dados Exatos do PC 1 (Servidor MCP)

- **Usuário:** `brend`
- **Hostname:** `Brenda`
- **IP Local (LAN/Wi-Fi):** `192.168.0.4`
- **Python do Sistema:** `C:\Users\brend\AppData\Local\Programs\Python\Python313\python.exe` (ou simplesmente `python` no PATH)
- **Caminho dos Scripts MCP:**
  - `chief-youtube`: `"D:/CHIEF YOUTUBE/src/mcp/youtube_analytics_mcp.py"`
  - `hermes-codex-bridge`: `"D:/CHIEF YOUTUBE/src/mcp/hermes_codex_bridge_mcp.py"`
  - `el-profe-de-velas`: `"D:/CHIEF YOUTUBE/src/mcp/hermes_codex_bridge_mcp.py"`

---

## ⚡ Passo 1: Garantir que o SSH Server está ativo no PC 1

No **PC 1**, abra o **PowerShell como Administrador** e execute:

```powershell
# 1. Instalar o OpenSSH Server (se ainda não instalado)
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 2. Iniciar o serviço SSH e definir para iniciar automaticamente com o Windows
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# 3. Liberar no Firewall do Windows
if (!(Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
}
```

---

## 🔑 Passo 2: Configurar Chave SSH (Sem pedir senha)

Para que o agente AI no **PC 2** execute comandos sem travar pedindo senha interativa:

1. No terminal do **PC 2**, gere uma chave se ainda não tiver:
   ```bash
   ssh-keygen -t ed25519 -N ""
   ```

2. Copie a chave pública do **PC 2** para o **PC 1**:
   ```bash
   # Opção A (Linux/Mac/Git Bash):
   ssh-copy-id brend@192.168.0.4

   # Opção B (PowerShell no PC 2):
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh brend@192.168.0.4 "powershell -Command \"New-Item -ItemType Directory -Force -Path `\"$env:USERPROFILE\.ssh`\"; Add-Content -Force -Path `\"$env:USERPROFILE\.ssh\authorized_keys`\"\""
   ```

3. Teste a conexão a partir do **PC 2**:
   ```bash
   ssh brend@192.168.0.4 "python -c \"print('Conectado ao PC 1!')\""
   ```
   *Se imprimir `Conectado ao PC 1!` sem pedir senha, o túnel está 100% pronto.*

---

## 🚀 Passo 3: Configurar o `mcp_config.json` no PC 2

No **PC 2**, abra o arquivo:
- **Antigravity:** `~/.gemini/config/mcp_config.json` (ou `%USERPROFILE%\.gemini\config\mcp_config.json`)
- **Claude Desktop:** `%APPDATA%\Claude\claude_desktop_config.json`

E cole a configuração abaixo:

```json
{
  "mcpServers": {
    "chief-youtube": {
      "command": "ssh",
      "args": [
        "brend@192.168.0.4",
        "python",
        "\"D:/CHIEF YOUTUBE/src/mcp/youtube_analytics_mcp.py\""
      ],
      "type": "stdio"
    },
    "hermes-codex-bridge": {
      "command": "ssh",
      "args": [
        "brend@192.168.0.4",
        "python",
        "\"D:/CHIEF YOUTUBE/src/mcp/hermes_codex_bridge_mcp.py\""
      ],
      "type": "stdio"
    },
    "el-profe-de-velas": {
      "command": "ssh",
      "args": [
        "brend@192.168.0.4",
        "python",
        "\"D:/CHIEF YOUTUBE/src/mcp/hermes_codex_bridge_mcp.py\""
      ],
      "type": "stdio"
    }
  }
}
```

> **Dica para redes diferentes (Internet):** Se os dois computadores não estiverem no mesmo Wi-Fi, instale o [Tailscale](https://tailscale.com/) em ambos e substitua `192.168.0.4` pelo IP do Tailscale do PC 1 (ex: `100.x.y.z`).
