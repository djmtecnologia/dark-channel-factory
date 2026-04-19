import os
import asyncio
from dotenv import load_dotenv
from sqlmodel import SQLModel, create_engine, Session, select

# Importar modelos para que o SQLModel os reconheça
from cloud_hub.models import Tenant, Channel, VideoJob

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ Erro: DATABASE_URL não encontrada no .env")
    exit(1)

engine = create_engine(DATABASE_URL)

def init_db():
    print("🛢️  Inicializando banco de dados no Neon...")
    SQLModel.metadata.create_all(engine)
    print("✅ Tabelas criadas com sucesso!")

def create_super_admin():
    with Session(engine) as session:
        # Verifica se já existe
        admin_email = "derikmoretti@gmail.com"
        statement = select(Tenant).where(Tenant.email == admin_email)
        existing = session.exec(statement).first()
        
        if existing:
            print(f"ℹ️  Super Admin '{admin_email}' já existe.")
            return
            
        print(f"👑 Criando conta Super Admin: {admin_email}")
        admin = Tenant(
            name="Derik Moretti (Super Admin)",
            email=admin_email,
            plan_type="enterprise",
            credits_balance=999999,
            auto_approve_content=True
        )
        session.add(admin)
        session.commit()
        print("✅ Super Admin criado com sucesso!")

if __name__ == "__main__":
    init_db()
    create_super_admin()
    print("\n🚀 Setup concluído! Você já pode subir o 'cloud_hub' para o Vercel.")
