import asyncio
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob, Channel, Tenant
from cloud_hub.core.copywriter import run_copywriter_phase
from viral_scout import ViralScout
from dotenv import load_dotenv
import json

load_dotenv()

async def analyze_first_elite_script():
    engine = create_engine(os.getenv("DATABASE_URL"))
    
    with Session(engine) as session:
        # 1. Busca Canal
        channel = session.exec(select(Channel).where(Channel.active == True)).first()
        if not channel:
            print("❌ Canal não encontrado.")
            return

        # 2. Minera um tema fresco
        scout = ViralScout()
        trends = scout.get_viral_videos(niche=None, days=30)
        topic = trends[0]["title"] if trends else "The AI Agent Revolution: How 1 Person Companies are Scaling to $10M"
        
        print(f"🎬 [TEST] Gerando Roteiro Elite para: {topic}")
        
        # 3. Cria Job Temporário
        job = VideoJob(
            channel_id=channel.id,
            tenant_id=channel.tenant_id,
            topic=topic,
            status="PENDING",
            video_type="LONG",
            target_language="en-US"
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        
        # 4. Roda o Copywriter
        await run_copywriter_phase(session, job)
        
        # 5. Output da Análise
        print("\n" + "="*50)
        print("📊 ANÁLISE DO ROTEIRO ELITE")
        print("="*50)
        
        strat = job.script_data.get("marketing_strategy", {})
        print(f"🔥 TITULOS VIRAIS: {strat.get('viral_titles', [])}")
        print(f"🧲 GANCHO (0-5s): {job.script_data['scenes'][0]['narration'][:200]}...")
        print(f"📈 PACING MAP: {strat.get('pacing_map')}")
        print(f"🎨 THUMB CONCEPTS: {strat.get('thumb_concepts')}")
        print("="*50)

if __name__ == "__main__":
    asyncio.run(analyze_first_elite_script())
