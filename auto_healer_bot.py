import time
import os

try:
    import pyautogui
except ImportError:
    print("Instale o PyAutoGUI primeiro: pip install pyautogui")
    exit(1)

LOG_FILE = "factory_console.log"
# Palavras que disparam o alerta (mas ignoramos cotas cheias para não floodar)
ERROR_KEYWORDS = ["Exception", "Traceback", "❌ [ERRO", "CRITICAL"]
IGNORE_KEYWORDS = ["COTA DE API ESGOTADA", "Todas as chaves", "PENDING_UPLOAD"]
COOLDOWN_SECONDS = 300  # Só manda mensagem a cada 5 minutos

def monitor_log():
    print("==================================================")
    print("🤖 AUTO-HEALER BOT (PyAutoGUI) ATIVADO")
    print("Monitorando logs. Deixe o cursor piscando no chat do Gemini!")
    print("==================================================")

    if not os.path.exists(LOG_FILE):
        open(LOG_FILE, 'a').close()

    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        f.seek(0, os.SEEK_END)
        
        while True:
            line = f.readline()
            if not line:
                time.sleep(2)
                continue
                
            # Verifica se é um erro válido
            line_lower = line.lower()
            is_error = any(kw.lower() in line_lower for kw in ERROR_KEYWORDS)
            is_ignored = any(kw.lower() in line_lower for kw in IGNORE_KEYWORDS)
            
            if is_error and not is_ignored:
                print(f"\n🚨 ERRO CRÍTICO DETECTADO: {line.strip()}")
                print("⚠️ NÃO MEXA NO TECLADO! Acionando o Gemini em 3 segundos...")
                time.sleep(3)
                
                # Digita magicamente "logs" e aperta Enter
                pyautogui.typewrite("logs")
                pyautogui.press('enter')
                
                print(f"✅ Chamado enviado. Dormindo por {COOLDOWN_SECONDS//60} minutos para o Gemini trabalhar.")
                time.sleep(COOLDOWN_SECONDS)
                
                # Pula tudo que foi escrito no log enquanto o bot dormia
                f.seek(0, os.SEEK_END)

if __name__ == "__main__":
    monitor_log()
