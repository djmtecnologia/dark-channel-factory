import os
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def check_recent_jobs():
    with Session(engine) as session:
        # Pega os 5 jobs mais recentes
        statement = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(5)
        results = session.exec(statement).all()
        print(f"--- 5 Jobs Mais Recentes ---")
        for job in results:
            print(f"ID: {job.id}")
            print(f"Tópico: {job.topic}")
            print(f"Status: {job.status}")
            print(f"Duração: {job.video_type}")
            print(f"Idioma: {job.target_language}")
            print(f"Erro: {job.error_message}")
            print(f"Criado em: {job.created_at}")
            print(f"Roteiro gerado? {'Sim' if job.script_data else 'Não'}")
            if job.script_data and isinstance(job.script_data, dict):
                scenes = job.script_data.get("scenes", [])
                print(f"Cenas no roteiro: {len(scenes)}")
            print("-" * 30)

if __name__ == "__main__":
    check_recent_jobs()
