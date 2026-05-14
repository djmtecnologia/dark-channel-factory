import time
import os
import sys
import logging
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
import warnings
try:
    from requests.packages.urllib3.exceptions import DependencyWarning
    warnings.filterwarnings("ignore", category=DependencyWarning)
except: pass

class Tee(object):
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

log_file = open("factory_console.log", "a", encoding="utf-8")
sys.stdout = Tee(sys.stdout, log_file)
sys.stderr = Tee(sys.stderr, log_file)
from sqlmodel import Session, create_engine, SQLModel
from agents.orchestrator.global_scheduler import GlobalScheduler
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURAÇÃO DE LOGS COM ROTAÇÃO (Evita disco cheio) ---
LOG_FILE = os.path.join(os.path.abspath("."), "factory_errors.log")
rotating_handler = RotatingFileHandler(
    LOG_FILE, 
    maxBytes=5 * 1024 * 1024,  # 5MB por arquivo
    backupCount=3,              # Mantém 3 arquivos de backup
    encoding='utf-8'
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        rotating_handler,
        logging.StreamHandler(sys.stdout)
    ]
)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.error("❌ CRÍTICO: DATABASE_URL não configurada no .env! O serviço não pode iniciar.")
    sys.exit(1)
engine = create_engine(DATABASE_URL)

def run_service_loop():
    # Sincroniza tabelas novas e aplica migrações de colunas faltantes
    print("🗄️ Sincronizando banco de dados...")
    try:
        from migrate_db import migrate
        migrate()
    except Exception as e:
        print(f"⚠️ Erro ao tentar migrar automaticamente: {e}")
        SQLModel.metadata.create_all(engine)
    
    scheduler = GlobalScheduler(engine)
    
    # Inicializa o Agente de Áudio (Garante BGM/SFX antes de começar)
    try:
        from agents.media.audio_scout import AudioScout
        print("🎵 [Serviço] Preparando biblioteca de áudio sem direitos autorais...")
        scout = AudioScout()
        scout.download_theme_music()
    except Exception as e:
        print(f"⚠️ Alerta Audio Scout: {e}")

    print("=============================================")
    print("🛡️ DARK CHANNEL FACTORY - MODO SERVIÇO 24/7")
    print(f"📄 Logs de erro: {LOG_FILE}")
    print("=============================================")

    last_creation_check = 0
    
    while True:
        try:
            now_ts = time.time()
            
            # 1. Ciclo de Criação & Replicação (A cada 10 minutos - Pesado)
            if now_ts - last_creation_check > 600:
                logging.info("🕒 [Cron] Ciclo de criação e analytics (10min interval)...")
                scheduler.run_daily_cycle()
                scheduler.check_viral_and_replicate()
                last_creation_check = now_ts
            
            # 2. Processamento de IA (A cada 30 segundos - Leve)
            #logging.info("🧠 [IA] Processando jobs pendentes...")
            scheduler.process_pending_jobs()
            
            # Intervalo curto para o loop de IA ser ágil
            time.sleep(30) 

        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"🚨 ERRO CRÍTICO NO SERVIÇO: {str(e)}")
            logging.error(f"Detalhes: \n{error_details}")
            
            # Espera um pouco antes de tentar reiniciar o loop
            time.sleep(60)

if __name__ == "__main__":
    try:
        run_service_loop()
    except KeyboardInterrupt:
        logging.info("🛑 Serviço encerrado manualmente.")
        sys.exit(0)
