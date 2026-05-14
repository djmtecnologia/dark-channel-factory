import os
from typing import Dict
from sqlmodel import Session, select
from cloud_hub.models import VideoJob, ViralTheme
from datetime import datetime, timezone

class PerformanceAnalyst:
    """O Cérebro Financeiro que decide o que escalar e o que matar."""
    
    def __init__(self, session: Session):
        self.session = session

    def calculate_video_score(self, job: VideoJob) -> float:
        """Aplica a fórmula Mestra de Score do Antigravity."""
        metrics = job.performance_metrics or {}
        
        ctr = metrics.get("ctr", 0.0)
        ret_5s = metrics.get("retention_5s", 0.0)
        ret_30s = metrics.get("retention_30s", 0.0)
        watch_time = metrics.get("watch_time_pct", 0.0)
        engagement = metrics.get("engagement_rate", 0.0)

        # SCORE = (CTR × 0.25) + (Retenção 0-5s × 0.30) + (Retenção 30s × 0.25) + (Watch Time × 0.10) + (Engajamento × 0.10)
        score = (ctr * 0.25) + (ret_5s * 0.30) + (ret_30s * 0.25) + (watch_time * 0.10) + (engagement * 0.10)
        
        job.viral_score = score
        self.session.add(job)  # Persist the score update
        return score

    def decide_action(self, job: VideoJob):
        """Classifica o vídeo e define a estratégia de escala com carência de dados."""
        # Carência: Não julga vídeos com menos de 2 horas de vida ou sem impressões
        time_since_publish = 0
        if job.published_at:
            time_since_publish = (datetime.now(timezone.utc) - job.published_at).total_seconds() / 3600
        
        metrics = job.performance_metrics or {}
        impressions = metrics.get("impressions", 0)
        
        if time_since_publish < 6.0 and impressions < 100:
            print(f"⏳ [Analyst] Video {str(job.id)[:8]} ainda é muito novo ({time_since_publish:.1f}h). Aguardando dados...")
            job.viral_score = 0.1 # Score neutro positivo para permitir replicação inicial
            return

        score = self.calculate_video_score(job)
        ctr = metrics.get("ctr", 0.0)
        ret_5s = metrics.get("retention_5s", 0.0)

        print(f"📊 [Analyst] Video ID: {str(job.id)[:8]} | Score: {score:.2f} | CTR: {ctr}% | Ret 5s: {ret_5s}%")

        # BUG FIX: Use AND logic — kill only when BOTH metrics are below threshold
        # The original 'or' logic killed videos with good CTR but slightly low retention
        # BUG FIX: Covered the 7.0 < ctr < 8.0 gap where no branch was entered
        if ctr < 4.0 and ret_5s < 50.0:
            print("🔴 [KILL] Desempenho insuficiente confirmado em ambas métricas.")
            job.optimization_notes = "FAILED: Low CTR and low retention after threshold."
            
        # 🟡 TESTAR MAIS (neutro) - any mediocre signal
        elif ctr < 7.0 or ret_5s < 65.0:
            print("🟡 [TEST] Desempenho mediano.")
            job.optimization_notes = "NEUTRAL: Modeling average performance."

        # 🟢 ESCALAR (Winner)
        elif ctr >= 7.0 and ret_5s >= 65.0:
            print("🟢 [SCALE] Winner detectado!")
            job.optimization_notes = "WINNER: High engagement structure."
            
        # 🔥 SUPER WINNER
        if ctr > 10.0 and metrics.get("average_view_duration_pct", 0.0) > 45.0:
            print("🔥 [SUPER WINNER] Criando CLUSTER de 20 vídeos derivados!")
            self._create_viral_theme(job)

    def _create_viral_theme(self, job: VideoJob):
        """Transforma um Super Winner em um Tema Viral permanente."""
        from cloud_hub.models import Channel
        existing = self.session.exec(select(ViralTheme).where(ViralTheme.topic == job.topic)).first()
        if not existing:
            # BUG FIX: job.channel is not a lazy-loaded relationship in SQLModel by default
            # Must explicitly query the Channel to avoid AttributeError
            channel = self.session.get(Channel, job.channel_id)
            theme = ViralTheme(
                tenant_id=channel.tenant_id if channel else None,
                topic=job.topic,
                niche=channel.niche if channel else "general",
                views_triggered=(job.performance_metrics or {}).get("views_24h", 0),
                is_active=True
            )
            self.session.add(theme)
            print(f"🚀 [Memory] Tema '{job.topic}' adicionado ao Cluster Viral.")
