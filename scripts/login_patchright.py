import sys
from pathlib import Path
from patchright.sync_api import sync_playwright

project_root = Path(__file__).resolve().parents[1]
profile_dir = project_root / "data" / "chrome_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

# Remove arquivos de lock antigos
for f in profile_dir.glob("*lock*"):
    try:
        f.unlink(missing_ok=True)
    except Exception:
        pass
for f in profile_dir.glob("Singleton*"):
    try:
        f.unlink(missing_ok=True)
    except Exception:
        pass

print("=" * 60)
print("🚀 ABRINDO ADOBE FIREFLY COM PATCHRIGHT (ANTI-DETECCAO)...")
print("=" * 60)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        args=["--start-maximized", "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        viewport=None
    )
    page = context.pages[0] if context.pages else context.new_page()
    print("🌐 Acessando https://firefly.adobe.com/ ...")
    page.goto("https://firefly.adobe.com/", wait_until="domcontentloaded")
    
    print("\n" + "=" * 60)
    print("👉 A janela do Adobe Firefly está aberta na sua tela.")
    print("👉 Faça login na sua NOVA CONTA da Adobe.")
    print("👉 Quando a tela inicial carregar com seus créditos:")
    print("   Volte neste terminal e pressione ENTER para salvar.")
    print("=" * 60 + "\n")
    
    input("Pressione [ENTER] aqui depois que concluir o login no Firefly: ")
    context.close()

print("\n✅ SUCESSO: Sessão da nova conta do Firefly persistida em disco!")
