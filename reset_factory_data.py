import os
from sqlmodel import Session, create_engine, select, delete
from cloud_hub.models import VideoJob, PerformanceLog, ViralTheme
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def reset_database():
    print("🧹 Iniciando limpeza profunda do banco de dados...")
    with Session(engine) as session:
        # 1. Remove logs de performance
        session.exec(delete(PerformanceLog))
        print("✅ Logs de performance removidos.")
        
        # 2. Remove temas virais antigos
        session.exec(delete(ViralTheme))
        print("✅ Temas virais (memória) removidos.")
        
        # 3. Remove todos os jobs de vídeo (os 'fantasmas')
        session.exec(delete(VideoJob))
        print("✅ Todos os jobs de vídeo foram apagados.")
        
        session.commit()
    print("\n✨ Banco de dados limpo! O robô agora começará do zero absoluto.")

if __name__ == "__main__":
    confirm = input("⚠️ Isso apagará TODOS os jobs do banco de dados. Tem certeza? (s/n): ")
    if confirm.lower() == 's':
        reset_database()
    else:
        print("Operação cancelada.")
