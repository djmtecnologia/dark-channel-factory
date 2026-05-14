import os
from datetime import datetime, time, timedelta, timezone
from typing import List
from sqlmodel import Session, create_engine, select
from cloud_hub.models import Channel, VideoJob, ViralTheme
from dotenv import load_dotenv

load_dotenv()

# Janelas de Postagem (3 Longos + 9 Shorts por Canal)
POST_WINDOWS = {
    "US": {"long": [time(11, 0), time(16, 0), time(20, 0)], "shorts": [time(9,0), time(12,0), time(14,0), time(15,0), time(17,0), time(18,0), time(21,0), time(22,0), time(23,0)]},
    "France": {"long": [time(10, 0), time(15, 0), time(19, 0)], "shorts": [time(8,0), time(11,0), time(13,0), time(14,0), time(16,0), time(17,0), time(20,0), time(21,0), time(22,0)]},
    "Spain": {"long": [time(10, 0), time(15, 0), time(19, 0)], "shorts": [time(8,0), time(11,0), time(13,0), time(14,0), time(16,0), time(17,0), time(20,0), time(21,0), time(22,0)]},
    "Germany": {"long": [time(9, 0), time(14, 0), time(18, 0)], "shorts": [time(7,0), time(10,0), time(12,0), time(13,0), time(15,0), time(16,0), time(19,0), time(20,0), time(21,0)]},
}

class GlobalScheduler:
    def __init__(self, engine):
        self.engine = engine

    def get_adaptive_time(self, region, video_type, index):
        """Calcula o horario para amanha baseado no slot."""
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        slots = POST_WINDOWS.get(region, POST_WINDOWS["US"])[video_type]
        slot = slots[index % len(slots)]
        return datetime.combine(tomorrow, slot).replace(tzinfo=timezone.utc)

    def run_daily_cycle(self):
        """Dispara a criacao de 12 videos por canal (3 Long + 9 Shorts)."""
        with Session(self.engine) as session:
            channels = session.exec(select(Channel).where(Channel.active == True)).all()
            for channel in channels:
                # Detecta Virais no Canal
                viral_themes = session.exec(select(ViralTheme).where(
                    ViralTheme.is_active == True, 
                    ViralTheme.tenant_id == channel.tenant_id
                )).all()
                
                # Se viralizar, dobra de 3 para 6 videos longos
                long_limit = 6 if viral_themes else 3
                shorts_per_long = 3
                
                # Geracao de Long-form
                for i in range(long_limit):
                    # BUG FIX: Rotates through themes instead of always using themes[0]
                    if viral_themes:
                        topic = viral_themes[i % len(viral_themes)].topic
                    else:
                        topic = "Financial AI Strategy"
                    scheduled_at = self.get_adaptive_time(channel.market_region, "long", i)
                    
                    job = VideoJob(
                        channel_id=channel.id,
                        topic=topic,
                        status="PENDING",
                        is_master=True,
                        scheduled_at=scheduled_at,
                        video_type="LONG"
                    )
                    session.add(job)
                    
                    # Geracao de 3 Shorts para cada Long
                    for j in range(shorts_per_long):
                        s_idx = (i * shorts_per_long) + j
                        s_time = self.get_adaptive_time(channel.market_region, "shorts", s_idx)
                        short_job = VideoJob(
                            channel_id=channel.id,
                            topic=f"SHORT: {topic}",
                            status="PENDING",
                            is_master=False,
                            scheduled_at=s_time,
                            video_type="SHORT",  # BUG FIX: was missing in original
                            parent_job_id=None
                        )
                        session.add(short_job)
            session.commit()

    def check_viral_and_replicate(self):
        """Julgamento de Performance e Replicação Global."""
        from agents.analyst.performance_analyst import PerformanceAnalyst
        
        with Session(self.engine) as session:
            analyst = PerformanceAnalyst(session)
            
            # Pega vídeos publicados no Master
            all_masters = session.exec(select(VideoJob).where(
                VideoJob.is_master == True,
                VideoJob.status == "PUBLISHED"
            )).all()

            for job in all_masters:
                # 1. O Analista julga o vídeo e decide se escala ou mata
                analyst.decide_action(job)
                
                # 2. Se for um vencedor (ou se estivermos na fase de aquecimento), replica
                # Verifica se já existem clones para este ID
                existing_clones = session.exec(select(VideoJob).where(VideoJob.parent_job_id == job.id)).first()
                
                if not existing_clones:
                    # Regra de Escala: Replicar se o score for bom ou se não tivermos dados (início)
                    if job.viral_score is None or job.viral_score >= 0: # No início replicamos tudo
                        print(f"🌍 [Global Sync] Escalonando '{job.topic}' para canais internacionais...")
                        self._replicate_to_global(session, job)
            
            session.commit()
    def _replicate_to_global(self, session, master_job):
        # ... (mantém o código de replicação)
        pass

    def process_pending_jobs(self):
        """Monitora e executa os jobs PENDING um por um com isolamento de sessão."""
        from cloud_hub.core.copywriter import run_copywriter_phase
        import asyncio

        # 1. Busca o próximo job sem manter a sessão aberta por muito tempo
        with Session(self.engine) as lookup_session:
            job_id = lookup_session.exec(select(VideoJob.id).where(VideoJob.status == "PENDING")).first()
        
        if job_id:
            # 2. Abre uma nova sessão dedicada para este processamento específico
            with Session(self.engine) as session:
                job = session.get(VideoJob, job_id)
                if not job: return

                # Se for um master novo (sem script), roda o Copywriter
                if not job.script_data:
                    print(f"\n🎬 [PROCESSOR] Iniciando Copywriting: {job.topic}")
                    try:
                        job.status = "THINKING"
                        session.add(job)
                        session.commit()
                        session.refresh(job) # Garante que o objeto está atualizado após commit
                        
                        # Roda o roteirista (passando a sessão dedicada)
                        asyncio.run(run_copywriter_phase(session, job))
                        
                        # Se chegou aqui sem erro, finaliza
                        job.status = "READY_FOR_EDIT"
                        session.add(job)
                        session.commit()
                        print(f"✅ Roteiro e Monetização concluídos para: {job.topic}")
                    except Exception as e:
                        print(f"❌ Erro no Copywriter: {e}")
                        session.rollback()
                        
                        # Tenta marcar como falha de forma isolada
                        try:
                            # Re-busca o job pois a sessão foi resetada
                            job = session.get(VideoJob, job_id)
                            job.status = "FAILED"
                            session.add(job)
                            session.commit()
                        except: pass
