import asyncio
import os
import sys
import re
from pathlib import Path

# Force utf-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from patchright.async_api import async_playwright
from firefly_bot.config import Config
from firefly_bot.chrome_profile import close_existing_profile_chrome
from firefly_bot.session import detect_signed_out, dismiss_overlays
from firefly_bot.human_input import HumanInput
from firefly_bot.selectors import locator_for
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("firefly_login")

async def main():
    config = Config()
    profile_dir = Path(os.environ.get("FIREFLY_CHROME_PROFILE_DIR", os.environ.get("HSL_FIREFLY_CHROME_PROFILE", r"D:\HSL-FIREFLY-PROFILE"))).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 65)
    print("[LOGIN] ABRINDO CHROME PARA LOGIN NO ADOBE FIREFLY")
    print(f"[LOGIN] Perfil: {profile_dir}")
    print("Alvo: https://firefly.adobe.com/generate/video")
    print("=" * 65)
    
    try:
        close_existing_profile_chrome(profile_dir)
    except Exception as e:
        logger.warning(f"Aviso ao limpar processos residuais: {e}")
        
    for lock in profile_dir.glob("*lock*"):
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass
    for singleton in profile_dir.glob("Singleton*"):
        try:
            singleton.unlink(missing_ok=True)
        except Exception:
            pass

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=config.chrome_channel,
            headless=False,
            no_viewport=True,
            args=[
                "--disable-http2",
                "--disable-blink-features=AutomationControlled",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        print("[LOGIN] Navegando para o Adobe Firefly Video...")
        try:
            await page.goto(config.firefly_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[LOGIN] Aviso de navegacao: {e}")

        # Aguarda 5 segundos para a UI hidratar completamente
        await asyncio.sleep(5)
        
        # Tenta clicar no botão 'Fazer logon' se estiver visível
        try:
            login_btn = page.get_by_role("button", name=re.compile(r"^Fazer logon$|^Iniciar sess[aã]o$|^Sign in$|^Log in$", re.IGNORECASE))
            if await login_btn.is_visible(timeout=3000):
                print("[LOGIN] Clicando no botao 'Fazer logon' para abrir tela de login...")
                await login_btn.click()
        except Exception:
            pass

        print("\n" + "=" * 65)
        print(">> A JANELA DO GOOGLE CHROME ESTA ABERTA NA SUA TELA.")
        print(">> FACA LOGIN COM SEU E-MAIL E SENHA DA ADOBE.")
        print(">> O SISTEMA DETECTARA AUTOMATICAMENTE O LOGIN QUANDO CONCLUIDO!")
        print("=" * 65 + "\n")
        
        human_input = HumanInput(page)
        
        max_wait_seconds = 900
        start_time = asyncio.get_event_loop().time()
        clicked_login = False
        saw_auth = False
        saw_signed_out = False
        logged_in = False
        consecutive_valid = 0
        
        while asyncio.get_event_loop().time() - start_time < max_wait_seconds:
            try:
                if page.is_closed() or not context.pages:
                    print("[LOGIN] Janela do navegador foi fechada pelo usuario.")
                    break
                
                # Se estamos na tela de login da Adobe (auth.services.adobe.com), aguarda o usuário terminar
                current_url = page.url.lower()
                if "auth" in current_url or "signin" in current_url or "login" in current_url:
                    saw_auth = True
                    consecutive_valid = 0
                    await asyncio.sleep(2)
                    continue

                signed_out = await detect_signed_out(page)
                if signed_out:
                    saw_signed_out = True
                    consecutive_valid = 0
                    # Tenta clicar no botão de logon se ainda não estiver em tela de auth
                    if not clicked_login:
                        try:
                            login_btn = page.get_by_role("button", name=re.compile(r"^Fazer logon$|^Iniciar sess[aã]o$|^Sign in$|^Log in$", re.IGNORECASE))
                            if await login_btn.is_visible(timeout=1500):
                                print("[LOGIN] Clicando no botao 'Fazer logon' para abrir tela de login...")
                                await login_btn.click()
                                clicked_login = True
                        except Exception:
                            pass
                else:
                    # Só considera válido se já tivermos visto a tela de auth/login,
                    # ou se tivermos detectado explicitamente que antes estava deslogado
                    # e agora a UI hidratou como logada
                    if saw_auth or (saw_signed_out and (asyncio.get_event_loop().time() - start_time > 15)):
                        consecutive_valid += 1
                        if consecutive_valid >= 3:
                            print("\n[LOGIN] LOGIN CONFIRMADO COM SUCESSO!")
                            print("[LOGIN] Aceitando termos de parceiros e ajustando preferencias...")
                            await dismiss_overlays(page, human_input, logger, -1)
                            await page.wait_for_timeout(4000)
                            logged_in = True
                            break
                    else:
                        consecutive_valid = 0
            except Exception as loop_err:
                err_str = str(loop_err)
                if "Target page, context or browser has been closed" in err_str or "Target closed" in err_str:
                    print("[LOGIN] Janela fechada pelo usuario.")
                    break
            
            await asyncio.sleep(2)
            
        print("[LOGIN] Encerrando navegador para persistir cookies e tokens no disco...")
        try:
            await context.close()
        except Exception:
            pass
            
    if logged_in:
        print("\n[SUCESSO] Sessao do Adobe Firefly autenticada e pronta para producao!")
        sys.exit(0)
    else:
        print("\n[AVISO] Janela fechada antes da confirmacao do login. Validando via probe...")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
