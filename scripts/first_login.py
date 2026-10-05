"""Abre o Google Chrome real com aceleração GPU para a autenticação manual no Firefly."""

from __future__ import annotations

import subprocess
from pathlib import Path

CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
)


def find_chrome() -> Path:
    """Localiza o Chrome estável instalado no sistema."""
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Google Chrome não encontrado. Instale o Chrome estável primeiro.")


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    profile_dir = project_root / "data" / "chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Limpa locks residuais que causam travamento
    for lock_file in profile_dir.glob("*lock*"):
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass
    for singleton in profile_dir.glob("Singleton*"):
        try:
            singleton.unlink(missing_ok=True)
        except Exception:
            pass

    chrome = find_chrome()
    command = [
        str(chrome),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-gpu-rasterization",
        "--ignore-gpu-blocklist",
        "--disable-background-timer-throttling",
        "--start-maximized",
        "--new-window",
        "https://firefly.adobe.com/",
    ]

    print("=" * 65)
    print("🚀 ABRINDO GOOGLE CHROME NATIVO COM O PERFIL DO PROJETO...")
    print(f"📁 Perfil: {profile_dir}")
    print("🌐 Alvo: https://firefly.adobe.com/")
    print("=" * 65)

    process = subprocess.Popen(command)
    
    print("\n👉 A janela oficial do Google Chrome está aberta.")
    print("👉 Faça login na sua NOVA CONTA da Adobe.")
    print("👉 Quando a tela do Firefly carregar com seus créditos:")
    print("   Feche a janela do Chrome e pressione ENTER aqui no terminal.\n")
    
    input("Pressione [ENTER] após fechar o Chrome para concluir: ")

    if process.poll() is None:
        print("Aguardando o Chrome fechar para salvar todos os cookies e tokens...")
        process.wait()

    print(f"\n✅ SUCESSO: Sessão persistida com segurança em: {profile_dir}")


if __name__ == "__main__":
    main()
