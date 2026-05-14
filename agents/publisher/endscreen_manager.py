import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

def setup_endscreens(video_id: str):
    """
    Usa Selenium para configurar telas finais automaticamente.
    Nota: Exige que o Chrome do usuário tenha uma sessão ativa no YouTube Studio.
    """
    print(f"🎬 [EndScreens] Iniciando configuração para o vídeo: {video_id}")
    
    chrome_options = Options()
    # Usar um perfil exclusivo para o bot para evitar conflitos com o Chrome aberto do usuário
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bot_profile_dir = os.path.join(project_root, 'bot_profile')
    
    if not os.path.exists(bot_profile_dir):
        os.makedirs(bot_profile_dir)
        
    chrome_options.add_argument(f"user-data-dir={bot_profile_dir}")
    # chrome_options.add_argument("--headless=new") # Desative se quiser ver o robô agindo
    
    # Evita detecção de robô básico
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        url = f"https://studio.youtube.com/video/{video_id}/edit"
        driver.get(url)
        wait = WebDriverWait(driver, 30)
        
        print("⏳ Verificando autenticação no YouTube Studio...")
        # Verifica se caiu na página de login
        if "accounts.google.com" in driver.current_url or "signin" in driver.current_url:
            print("\n" + "!"*60)
            print("🛑 AÇÃO NECESSÁRIA: O robô abriu uma nova janela do Chrome.")
            print("Por favor, faça login na conta do canal NESTA JANELA.")
            print("O robô aguardará você completar o login...")
            print("!"*60 + "\n")
            
            while "studio.youtube.com" not in driver.current_url:
                time.sleep(2)
            
            print("✅ Login detectado! Retomando configuração...")
            driver.get(url) # Recarrega para garantir que está na página de edição
        
        print("⏳ Aguardando carregamento da página de edição...")
        # Clica no botão de 'Tela Final'
        # O seletor pode variar, vamos buscar pelo ID comum
        endscreen_btn = wait.until(EC.element_to_be_clickable((By.ID, "endscreens-button")))
        endscreen_btn.click()
        
        time.sleep(3)
        
        print("➕ Adicionando Elementos...")
        
        # 1. Botão de Inscrição
        add_btn = wait.until(EC.element_to_be_clickable((By.ID, "add-element-button")))
        add_btn.click()
        time.sleep(1)
        subscribe_opt = driver.find_element(By.XPATH, "//*[contains(text(), 'Inscrição')]")
        subscribe_opt.click()
        
        # 2. Vídeo: Envio mais recente
        add_btn.click()
        time.sleep(1)
        video_opt = driver.find_element(By.XPATH, "//*[contains(text(), 'Vídeo')]")
        video_opt.click()
        time.sleep(1)
        recent_opt = driver.find_element(By.XPATH, "//*[contains(text(), 'Envio mais recente')]")
        recent_opt.click()
        
        # 3. Vídeo: Melhor para o espectador
        add_btn.click()
        time.sleep(1)
        video_opt = driver.find_element(By.XPATH, "//*[contains(text(), 'Vídeo')]")
        video_opt.click()
        time.sleep(1)
        best_opt = driver.find_element(By.XPATH, "//*[contains(text(), 'Melhor para o espectador')]")
        best_opt.click()
        
        print("💾 Salvando alterações...")
        save_btn = driver.find_element(By.ID, "save-button")
        save_btn.click()
        time.sleep(5)
        print("✅ Tela Final configurada!")

        # --- LÓGICA DE FIXAR COMENTÁRIO (PIN) ---
        print("📌 Tentando FIXAR o comentário estrategicamente...")
        driver.get(f"https://www.youtube.com/watch?v={video_id}")
        time.sleep(5)
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(3)
        
        try:
            # Localiza o menu de opções do comentário (três pontos)
            wait_pin = WebDriverWait(driver, 15)
            # Tenta encontrar o botão de menu do primeiro comentário
            menu_btn = wait_pin.until(EC.element_to_be_clickable((By.XPATH, "(//ytd-comment-thread-renderer//yt-icon-button[@id='button'])[1]")))
            menu_btn.click()
            time.sleep(2)
            
            # Clica em 'Fixar' ou 'Pin'
            pin_opt = wait_pin.until(EC.element_to_be_clickable((By.XPATH, "//tp-yt-paper-item[.//yt-formatted-string[contains(text(), 'Fixar') or contains(text(), 'Pin')]]")))
            pin_opt.click()
            time.sleep(2)
            
            # Confirma no pop-up
            confirm_btn = wait_pin.until(EC.element_to_be_clickable((By.ID, "confirm-button")))
            confirm_btn.click()
            print("✅ [PIN] Comentário fixado com sucesso!")
        except Exception as pin_err:
            print(f"⚠️ Aviso: Não foi possível fixar automaticamente (talvez o comentário ainda não apareceu): {pin_err}")
        
    except Exception as e:
        print(f"❌ Erro ao configurar Tela Final: {e}")
        # Se falhar, pelo menos não quebra o fluxo principal
    finally:
        driver.quit()

if __name__ == "__main__":
    # Teste rápido se rodado diretamente
    # setup_endscreens("VIDEO_ID_TESTE")
    pass
