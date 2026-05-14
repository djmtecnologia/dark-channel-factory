import os
import time
from playwright.sync_api import sync_playwright

PROFILES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "chrome_profiles"))

CHANNELS = [
    {"id": "en-US", "email": "iabusinessus@gmail.com", "pais": "EUA"},
    {"id": "fr-FR", "email": "docelarmercadotudo@gmail.com", "pais": "França"},
    {"id": "es-ES", "email": "lightpublicidadedkm@gmail.com", "pais": "Espanha"},
    {"id": "de-DE", "email": "mentedivinahumana@gmail.com", "pais": "Alemanha"}
]

print("=========================================================")
print("🤖 CONFIGURAÇÃO INICIAL DE PERFIS DO YOUTUBE (ZERO-API)")
print("=========================================================")
print("Este script abrirá o navegador para cada canal.")
print("Por favor, faça login com o email indicado e aguarde a tela")
print("do YouTube Studio carregar por completo.")
print("=========================================================\n")

def setup_profiles():
    os.makedirs(PROFILES_DIR, exist_ok=True)
    
    with sync_playwright() as p:
        for channel in CHANNELS:
            channel_id = channel['id']
            email = channel['email']
            pais = channel['pais']
            
            profile_path = os.path.join(PROFILES_DIR, channel_id)
            os.makedirs(profile_path, exist_ok=True)
            
            print(f"\n🌍 Preparando Canal: {pais} ({channel_id})")
            print(f"📧 EMAIL REQUERIDO: {email}")
            print("⏳ Abrindo navegador...")
            
            browser = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                channel="chrome", # <--- Força o uso do Google Chrome oficial instalado na máquina
                args=[
                    "--no-sandbox", 
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled" # <--- Esconde do Google que é um robô
                ],
                ignore_default_args=["--enable-automation"] # <--- Remove a barra "O Chrome está sendo controlado por software de automação"
            )
            
            page = browser.pages[0] if browser.pages else browser.new_page()
            
            print("👉 Redirecionando para o YouTube Studio...")
            page.goto("https://studio.youtube.com")
            
            print("⏳ Aguardando você fazer login...")
            print(">>> NÃO FECHE A JANELA. O script fechará sozinho quando detectar o login.")
            
            # Espera até a URL mudar para algo que indique que o Studio carregou com sucesso
            # O youtube studio redireciona para studio.youtube.com/channel/UC...
            try:
                page.wait_for_url("**/channel/**", timeout=0) # Timeout 0 = Espera infinitamente
                print(f"✅ SUCESSO! Login do canal {pais} detectado e sessão salva.")
                time.sleep(3) # Aguarda só para garantir que os cookies persistiram no disco
            except Exception as e:
                print(f"⚠️ Erro ao aguardar login: {e}")
            finally:
                browser.close()
                
    print("\n🎉 TODOS OS PERFIS FORAM CONFIGURADOS COM SUCESSO!")
    print("Agora o Dark Channel Factory pode fazer uploads invisíveis sem limites de API.")

if __name__ == "__main__":
    setup_profiles()
