import sys, os, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from patchright.sync_api import sync_playwright

root_dir = Path(__file__).resolve().parent.parent
profile_dir = root_dir / "data" / "chrome_profile"
artifact_dir = Path(r"C:\Users\brend\.gemini\antigravity\brain\96ed0e32-bebf-4c0c-bffa-f274f1c7ac9c")
screenshot_path = artifact_dir / "firefly_live_interface.png"

print(f"Usando perfil: {profile_dir}")
print(f"Destino screenshot: {screenshot_path}")

with sync_playwright() as p:
    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": True,
        "args": [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox"
        ],
        "viewport": {"width": 1920, "height": 1080}
    }
    
    try:
        context = p.chromium.launch_persistent_context(**launch_kwargs, channel="chrome")
    except Exception:
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        
    page = context.pages[0] if context.pages else context.new_page()
    print("Acessando https://firefly.adobe.com/generate/video ...")
    page.goto("https://firefly.adobe.com/generate/video", wait_until="domcontentloaded", timeout=60000)
    
    print("Aguardando carregamento da interface e créditos (6s)...")
    time.sleep(6)
    
    current_url = page.url
    page_title = page.title()
    print(f"URL: {current_url}")
    print(f"Título: {page_title}")
    
    page.screenshot(path=str(screenshot_path), full_page=False)
    print(f"Screenshot salvo com sucesso ({screenshot_path.stat().st_size / 1024:.1f} KB)!")
    
    context.close()
