import asyncio
import os
from dotenv import load_dotenv
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob, Channel, Tenant
from shared.llm_client import get_completion

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    separator = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL += f"{separator}sslmode=require"

engine = create_engine(DATABASE_URL)

async def run():
    print("🌍 INICIANDO CLONAGEM GLOBAL DO ÚLTIMO VÍDEO...")
    with Session(engine) as session:
        # Pega o último vídeo renderizado (o en-US que acabou de rodar)
        last_job = session.exec(
            select(VideoJob)
            .where(VideoJob.status.in_(["APPROVED", "PUBLISHED", "PENDING"]))
            .order_by(VideoJob.created_at.desc())
        ).first()
        
        if not last_job:
            print("❌ Nenhum vídeo encontrado no banco de dados para clonar.")
            return
            
        print(f"🎬 Vídeo base encontrado: {last_job.topic}")
            
        admin = session.exec(select(Tenant).where(Tenant.email == "derikmoretti@gmail.com")).first()
        
        # Idiomas alvo
        langs = {"fr-FR": "Francês", "de-DE": "Alemão", "es-ES": "Espanhol"}
        
        for lang_code, lang_name in langs.items():
            # Cria ou busca o canal para esse idioma
            channel = session.exec(select(Channel).where(Channel.language == lang_code)).first()
            if not channel:
                channel = Channel(
                    tenant_id=admin.id,
                    name=f"Master AI Stories {lang_code}",
                    niche="AI + Money + Stories",
                    language=lang_code,
                    market_region="Europe"
                )
                session.add(channel)
                session.commit()
                session.refresh(channel)
                
            print(f"\n🔄 Traduzindo para {lang_name} ({lang_code})...")
            
            # 1. Traduzir o Tópico (para busca de Thumbnails e Assets)
            prompt_topic = f"Translate this short YouTube topic to {lang_name} ({lang_code}). Keep it catchy and short. Return ONLY the translated text: {last_job.topic}"
            new_topic = get_completion(prompt_topic)
            print(f"  -> Novo Tópico: {new_topic}")
            
            # 2. Traduzir o Roteiro
            original_script = last_job.script_data.get("script_full", "")
            if not original_script:
                original_script = last_job.script_data.get("script", "")
                
            prompt_script = f"Translate the following YouTube script to {lang_name} ({lang_code}). Make it sound natural for a native speaker. IMPORTANT: Preserve the exact length and tone. Return ONLY the translated script text:\n\n{original_script}"
            new_script = get_completion(prompt_script)
            print(f"  -> Roteiro traduzido! ({len(new_script)} caracteres)")
            
            # 3. Criar Novo Job na fila (APPROVED) para que o Desktop Node pegue
            new_job = VideoJob(
                channel_id=channel.id,
                topic=new_topic,
                script_data={
                    "script_full": new_script,
                    "topic": new_topic,
                    "primary_keyword": last_job.script_data.get("primary_keyword"),
                    "search_keywords": last_job.script_data.get("search_keywords", [])
                },
                status="DRAFTED", # Pronto para renderizar e visível para a Vercel antiga
                target_language=lang_code,
                is_master=False,
                parent_job_id=last_job.id
            )
            session.add(new_job)
            session.commit()
            print(f"✅ Job de {lang_name} enviado para a fábrica!")
            
    print("\n🚀 CLONAGEM CONCLUÍDA! Vá para o terminal do desktop_node e rode-o para renderizar os 3 vídeos internacionais.")

if __name__ == "__main__":
    asyncio.run(run())
