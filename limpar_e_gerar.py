import requests
import json
import os
from dotenv import load_dotenv
from sqlmodel import Session, create_engine, select
from cloud_hub.models import Channel, VideoJob

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    separator = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL += f"{separator}sslmode=require"

engine = create_engine(DATABASE_URL)
CLOUD_HUB_URL = os.getenv("CLOUD_HUB_URL", "https://dark-channel-factory.vercel.app")

print("🧹 1. Limpando os jobs vazios antigos...")
with Session(engine) as session:
    jobs_vazios = session.exec(select(VideoJob).where(VideoJob.status == "DRAFTED")).all()
    count = 0
    for j in jobs_vazios:
        # Se não tem script, falha ele
        if not j.script_data or ("script_full" not in j.script_data and "script" not in j.script_data):
            j.status = "FAILED"
            j.error_message = "Cancelado pelo script de limpeza"
            session.add(j)
            count += 1
    session.commit()
    print(f"✅ {count} jobs vazios marcados como FAILED.")

    print("\n🤖 2. Criando um JOB NOVO COMPLETO com Inteligência Artificial (Localmente)...")
    channel = session.exec(select(Channel).where(Channel.language == "en-US")).first()
    
    if channel:
        print(f"-> Canal encontrado: {channel.name} (ID: {channel.id})")
        print("-> Gerando roteiro com Gemini...")
        
        from cloud_hub.core.intelligence import run_scout_phase, run_viral_score_phase
        from cloud_hub.core.copywriter import run_copywriter_phase
        import asyncio

        async def run_pipeline():
            try:
                job = await run_scout_phase(session, channel)
                score = await run_viral_score_phase(session, job)
                if score < 70:
                    print(f"❌ Score viral baixo: {score}")
                    return
                await run_copywriter_phase(session, job)
                print("✅ SUCESSO! A IA acabou de escrever o roteiro e injetar o link de afiliado.")
                print(f"Tópico escolhido pela IA: {job.topic}")
                print("\n🚀 AGORA VÁ PARA O TERMINAL DO DESKTOP_NODE E VEJA ELE EDITAR!")
            except Exception as e:
                import traceback
                print("❌ Erro ao rodar a IA localmente:")
                traceback.print_exc()

        asyncio.run(run_pipeline())
    else:
        print("❌ Canal não encontrado no banco de dados.")
