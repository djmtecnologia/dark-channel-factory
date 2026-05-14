import os
import sys
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob, Channel, ViralTheme
from viral_scout import ViralScout
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def seed():
    print("🚀 [IGNIÇÃO] Iniciando ciclo de produção Elite 2026...")
    
    with Session(engine) as session:
        # 1. Busca o Canal Principal
        channel = session.exec(select(Channel).where(Channel.niche == "Money")).first()
        if not channel:
            # Tenta buscar qualquer canal se o de "Money" não existir
            channel = session.exec(select(Channel)).first()
            
        if not channel:
            print("❌ Erro: Nenhum canal encontrado no banco de dados.")
            return

        print(f"📺 Canal Alvo: {channel.name} ({channel.language})")

        # 2. Mineração de Tópicos Reais (ViralScout)
        print("🔎 Minerando tendências dos últimos 30 dias...")
        scout = ViralScout()
        trends = scout.get_viral_videos(niche=channel.niche, days=30)
        
        if not trends:
            print("⚠️ Sem tendências encontradas. Usando tópicos da Blueprint.")
            topics = [
                "Construí um negócio usando apenas IA (7 dias de lucro real)",
                "Esta ferramenta de IA substituiu minha equipe de R$ 15k/mês",
                "O fim do SaaS: Por que os Agentes de IA são o futuro em 2026"
            ]
        else:
            topics = [t["title"] for t in trends[:3]]

        # 3. Criação dos Jobs
        job_ids = []
        for i, topic in enumerate(topics):
            # Agendamento progressivo (1h de diferença)
            scheduled = datetime.now(timezone.utc) + timedelta(hours=i+1)
            
            job = VideoJob(
                tenant_id=channel.tenant_id,
                channel_id=channel.id,
                topic=topic,
                video_type="LONG",
                status="PENDING",
                language=channel.language,
                scheduled_at=scheduled
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_ids.append(job.id)
            print(f"✅ Job Criado: {job.id} | Tópico: {topic}")

        print(f"\n🔥 Sucesso! {len(job_ids)} tarefas adicionadas à fila.")
        print("Agora você pode rodar 'python desktop_node.py' para iniciar a renderização.")

if __name__ == "__main__":
    seed()
