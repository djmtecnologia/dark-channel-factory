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


def _get_default_engine():
    if not DATABASE_URL:
        print("❌ DATABASE_URL não configurada no .env. Abortando.")
        raise SystemExit(1)
    return create_engine(DATABASE_URL)

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
    
    # Migração 007 — production_profile na tabela video_jobs (Fase 1: PILOT_5MIN, STANDARD_10MIN, etc.)
    ("production_profile", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS production_profile VARCHAR(50);"),

    # Migração 008 — video_director_mode na tabela video_jobs (Sprint 2.5: 'legacy', 'director' ou NULL)
    ("video_director_mode_col", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS video_director_mode VARCHAR(32);"),
    (
        "video_director_mode_check",
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "SELECT 1 FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE c.conname = 'chk_video_jobs_video_director_mode' "
        "AND t.relname = 'video_jobs' "
        "AND n.nspname = current_schema()"
        ") THEN "
        "ALTER TABLE video_jobs ADD CONSTRAINT chk_video_jobs_video_director_mode "
        "CHECK (video_director_mode IS NULL OR video_director_mode IN ('legacy', 'director')); "
        "END IF; "
        "END $$;",
    ),
    (
        "video_director_mode_idx",
        "CREATE INDEX IF NOT EXISTS ix_video_jobs_video_director_mode ON video_jobs (video_director_mode);",
    ),
]

DOWNGRADE_MIGRATIONS = [
    ("video_director_mode_idx", "DROP INDEX IF EXISTS ix_video_jobs_video_director_mode;"),
    ("video_director_mode_check", "ALTER TABLE video_jobs DROP CONSTRAINT IF EXISTS chk_video_jobs_video_director_mode;"),
    ("video_director_mode_col", "ALTER TABLE video_jobs DROP COLUMN IF EXISTS video_director_mode;"),
]

def migrate(target_engine=None):
    active_engine = target_engine or _get_default_engine()
    print("🛠️  Iniciando Migração de Banco de Dados...")
    if target_engine is None:
        print(f"📡 Destino: {DATABASE_URL[:40]}...")
    with active_engine.connect() as conn:
        for name, sql in MIGRATIONS:
            try:
                print(f"  ▶ Aplicando: {name}...")
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✅ {name}: OK")
            except Exception as e:
                conn.rollback()
                print(f"  ❌ Falha na migration '{name}': {e}")
                raise
    print("\n✅ Migração concluída. Banco de dados atualizado.")


def downgrade(target_engine=None):
    active_engine = target_engine or _get_default_engine()
    print("⏪ Iniciando Downgrade de Migração...")
    with active_engine.connect() as conn:
        for name, sql in DOWNGRADE_MIGRATIONS:
            try:
                print(f"  ▶ Revertendo: {name}...")
                conn.execute(text(sql))
                conn.commit()
                print(f"  ✅ {name}: OK")
            except Exception as e:
                conn.rollback()
                print(f"  ❌ Falha no downgrade '{name}': {e}")
                raise
    print("\n✅ Downgrade concluído.")

if __name__ == "__main__":
    migrate()
