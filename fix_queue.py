import os
from dotenv import load_dotenv
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and "sslmode=" not in DATABASE_URL:
    separator = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL += f"{separator}sslmode=require"

engine = create_engine(DATABASE_URL)

print("🛠️ Corrigindo a fila de vídeos da Vercel...")
with Session(engine) as session:
    # 1. Pega todos os jobs da fila (qualquer status que o worker tenta ler)
    jobs_na_fila = session.exec(select(VideoJob).where(VideoJob.status.in_(["DRAFTED", "APPROVED"]))).all()
    
    count_cancelados = 0
    job_valido = None
    
    for j in jobs_na_fila:
        # Pega o texto do roteiro
        script_text = ""
        if isinstance(j.script_data, dict):
            script_text = j.script_data.get("script_full") or j.script_data.get("script") or ""
            
        # Se não tem um texto real ou foi o erro do LLM, cancela!
        if not script_text.strip() or "Erro LLM" in script_text:
            j.status = "FAILED"
            j.error_message = "Limpando job fantasma"
            session.add(j)
            count_cancelados += 1
        else:
            # Achamos um job que realmente tem texto válido!
            job_valido = j
            
    print(f"✅ Limpos {count_cancelados} jobs defeituosos/vazios da fila.")
    
    # 2. Se não achamos nenhum job válido, vamos criar um manualmente 
    # para forçar o worker a renderizar com sucesso!
    if not job_valido:
        print("🤖 Criando um Job de Teste Garantido para passar na Vercel...")
        
        # Pega o primeiro canal do usuário
        from cloud_hub.models import Channel
        channel = session.exec(select(Channel)).first()
        
        if channel:
            novo_job = VideoJob(
                tenant_id=channel.tenant_id,
                channel_id=channel.id,
                status="DRAFTED", # Vercel antiga só lê DRAFTED
                topic="AI Tools That Make Money",
                is_master=True,
                target_language="en-US",
                script_data={
                    "script_full": "Did you know that AI tools are generating thousands of dollars daily? Watch this short video to learn how a simple automation script can change your life! First, find a trending topic. Second, use AI to write the script. Subscribe for more money tips!",
                    "title": "AI Tools That Make Money"
                }
            )
            session.add(novo_job)
            print("✅ Roteiro de Ouro criado e colocado na fila (DRAFTED)!")
            
    session.commit()
    print("\n🚀 PRONTO! PODE RODAR O DESKTOP_NODE.PY NOVAMENTE!")
