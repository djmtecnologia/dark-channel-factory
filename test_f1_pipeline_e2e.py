import os
import sys
import asyncio
import json
from uuid import uuid4
from datetime import datetime, timezone
from sqlmodel import Session, create_engine, SQLModel
from dotenv import load_dotenv

load_dotenv()

# Forçar ambiente UTF-8 no Windows para evitar UnicodeEncodeError
sys.stdout.reconfigure(encoding='utf-8')

# Importações internas do Dark Channel Factory
from cloud_hub.core.copywriter import run_copywriter_phase
from cloud_hub.models import VideoJob, Channel, Tenant
import desktop_node

async def run_f1_e2e_test():
    print("🏎️ [TESTE F1 E2E] Iniciando simulação sistêmica...")
    
    # 1. Setup de banco de dados temporário em memória para isolamento
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Criar inquilino e canal de teste
        tenant = Tenant(
            id=uuid4(),
            name="Elite Production Test", 
            email="f1-test@factory.ia", 
            plan_type="enterprise",
            auto_approve_content=True
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        
        channel = Channel(
            id=uuid4(),
            tenant_id=tenant.id,
            name="Formula 1 Analytics US",
            identifier="en-US",
            market_region="US",
            language="en-US",
            voice="en-US-GuyNeural",
            niche="Money",
            active=True
        )
        session.add(channel)
        session.commit()
        session.refresh(channel)
        
        # Criar job do tipo SHORT sobre Formula 1 para testar em alta velocidade
        job = VideoJob(
            id=uuid4(),
            channel_id=channel.id,
            tenant_id=tenant.id,
            topic="How real-time Python telemetry pipeline decides F1 Pitstops at Monaco GP",
            status="PENDING",
            video_type="SHORT",
            target_language="en-US",
            scheduled_at=datetime.now(timezone.utc)
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        
        print(f"✅ Job criado: {job.id} | Tópico: '{job.topic}' | Idioma: {job.target_language}")

        # 2. Executar fase de Copywriter (Geração de Roteiro)
        print("📝 [Copywriter] Gerando roteiro com monetização dinâmica do Google Trends (Match Semântico do catálogo)...")
        job = await run_copywriter_phase(session, job)
        
        script_data = job.script_data
        print("\n" + "="*50)
        print("📊 ROTEIRO E METADADOS GERADOS:")
        print(f"Título: {script_data.get('title')}")
        print(f"Descrição: {script_data.get('description')}")
        print(f"Link de CTA: {job.injected_affiliate_url}")
        
        scenes = script_data.get("scenes", [])
        print(f"Cenas geradas: {len(scenes)}")
        for idx, scene in enumerate(scenes):
            print(f"  🎬 Cena {idx+1} [{scene.get('timestamp')}]:")
            print(f"    Narração: {scene.get('narration')[:120]}...")
            print(f"    Visual: {scene.get('b_roll_visual')}")
            print(f"    SFX Cue: {scene.get('sfx_cue')}")
        print("="*50 + "\n")
        
        # Validar se o link de monetização dinâmico foi injetado com sucesso
        assert job.injected_affiliate_url is not None and len(job.injected_affiliate_url) > 0, "Erro: URL de monetização dinâmica não foi injetada!"
        print("🎯 [VALIDAÇÃO] CTA e Monetização Dinâmica injetadas com sucesso!")

        # Construir o payload de dados que o Worker do Desktop consome
        job_data = {
            "job_id": str(job.id),
            "topic": job.topic,
            "language": job.target_language,
            "script_data": script_data,
            "injected_url": job.injected_affiliate_url,
            "niche": channel.niche,
            "type": job.video_type,
            "channel_identifier": channel.identifier,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None
        }

        # 3. Executar o Renderizador Local (Geração do Vídeo, Pillow, Mixagem e QC)
        print("🎬 [Renderizador] Renderizando vídeo com Pillow (Code & Data Flowcharts) + Mixagem Sonora + QC local...")
        
        # Forçar uso de pasta de teste local caso queira monitorar
        success = desktop_node.render_video(job_data)
        
        if success:
            print("\n" + "="*50)
            print("🚀 [TESTE COMPLETO: SUCESSO]")
            print("O vídeo foi gerado, editado com Ken Burns, trilhas sonoras dark ambient tech integradas,")
            print("code blocks desenhados com Pillow, CTA em áudio e Pinned Comment criados,")
            print("e aprovado com sucesso no QC Agent Local!")
            print("="*50 + "\n")
        else:
            print("\n" + "="*50)
            print("❌ [TESTE COMPLETO: FALHA]")
            print("Ocorreu um erro na renderização ou o QC Agent reprovou o vídeo.")
            print("="*50 + "\n")
            sys.exit(1)

if __name__ == "__main__":
    if not os.path.exists("output"):
        os.makedirs("output")
    asyncio.run(run_f1_e2e_test())
