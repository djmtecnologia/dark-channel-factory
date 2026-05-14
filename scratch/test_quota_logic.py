import os
from datetime import datetime
from sqlmodel import Session, create_engine, select, func
from cloud_hub.models import VideoJob, Channel
from viral_scout import ViralScout
from dotenv import load_dotenv

load_dotenv()

def verify_quota_and_seed():
    print("=== TESTE DE COTA E DIVERSIFICAÇÃO ===")
    DAILY_TARGET = int(os.getenv("DAILY_LONG_TARGET", 3))
    engine = create_engine(os.getenv("DATABASE_URL"))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    with Session(engine) as session:
        # 1. Conta vídeos de hoje
        stmt = select(func.count(VideoJob.id)).where(
            VideoJob.video_type == "LONG",
            VideoJob.created_at >= today
        )
        long_count = session.exec(stmt).one()
        print(f"📊 Progresso: {long_count}/{DAILY_TARGET}")
        
        if long_count < DAILY_TARGET:
            print("⚡ Acionando ViralScout para diversificação...")
            channel = session.exec(select(Channel).where(Channel.active == True)).first()
            if not channel:
                print("❌ Nenhum canal ativo encontrado.")
                return
                
            scout = ViralScout()
            # O ViralScout agora escolhe um nicho aleatório se o canal for genérico
            trends = scout.get_viral_videos(niche=None, days=30)
            
            if trends:
                print(f"✅ Nicho minerado: {trends[0]['title'][:50]}...")
                needed = DAILY_TARGET - long_count
                for t in trends[:needed]:
                    print(f"➕ Injetando: {t['title']}")
                print(f"🚀 Pronto para gerar {needed} novos vídeos de elite.")
            else:
                print("❌ Falha ao minerar tendências.")
        else:
            print("✅ Cota de hoje já batida!")

if __name__ == "__main__":
    verify_quota_and_seed()
