"""
Migration Script — Dark Channel Factory
Aplica as migrações necessárias para o banco de dados de produção (Neon).
Execute quando um novo campo for adicionado ao modelo.
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ DATABASE_URL não configurada no .env. Abortando.")
    exit(1)

engine = create_engine(DATABASE_URL)

MIGRATIONS = [
    # Migração 001 — scheduled_at (anterior)
    ("scheduled_at", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP WITH TIME ZONE;"),
    
    # Migração 002 — video_type (novo campo adicionado em 05/05/2026)
    ("video_type", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS video_type VARCHAR DEFAULT 'LONG';"),
    
    # Migração 003 — published_url (novo campo para armazenar URL do YouTube)
    ("published_url", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS published_url TEXT;"),
    
    # Migração 004 — error_message pode precisar de tamanho maior (TEXT)
    ("error_message_text", "ALTER TABLE video_jobs ALTER COLUMN error_message TYPE TEXT;"),

    # Migração 005 — Novas colunas para Micro SaaS na tabela tenants
    ("tenant_password", "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS password_hash TEXT;"),
    ("tenant_api_keys", "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS personal_api_keys JSONB DEFAULT '{}';"),
    ("tenant_created_at", "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();"),
    ("tenant_region", "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS region TEXT DEFAULT 'North America';"),
    
    # Migração 006 — identifier na tabela channels
    ("channel_identifier", "ALTER TABLE channels ADD COLUMN IF NOT EXISTS identifier VARCHAR(100);"),
]

def migrate():
    print("🛠️  Iniciando Migração de Banco de Dados...")
    print(f"📡 Destino: {DATABASE_URL[:40]}...")
    with engine.connect() as conn:
        for name, sql in MIGRATIONS:
            try:
                print(f"  ▶ Aplicando: {name}...")
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✅ {name}: OK")
            except Exception as e:
                err_str = str(e)
                # Ignorar erros de 'coluna já existe' — migração idempotente
                if "already exists" in err_str or "does not exist" in err_str:
                    print(f"  ⏭  {name}: Coluna já existe ou não aplicável — ignorando.")
                    conn.rollback()
                else:
                    print(f"  ❌ Erro em {name}: {e}")
                    conn.rollback()
    print("\n✅ Migração concluída. Banco de dados atualizado.")

if __name__ == "__main__":
    migrate()
