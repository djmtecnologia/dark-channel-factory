"""
Testes da migration versionada de video_jobs.video_director_mode (Sprint 2.5 - Tarefa 2).
Executa em SQLite isolado (sem tocar em produção).
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import migrate_db


def test_migrations_registry_contains_video_director_mode_and_downgrade():
    upgrade_names = [name for name, _ in migrate_db.MIGRATIONS]
    downgrade_names = [name for name, _ in migrate_db.DOWNGRADE_MIGRATIONS]

    assert "video_director_mode_col" in upgrade_names
    assert "video_director_mode_check" in upgrade_names
    assert "video_director_mode_idx" in upgrade_names

    assert "video_director_mode_idx" in downgrade_names
    assert "video_director_mode_check" in downgrade_names
    assert "video_director_mode_col" in downgrade_names


def test_sqlite_upgrade_preserves_existing_rows_as_null_and_enforces_check(tmp_path):
    db_path = tmp_path / "migration_test.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        # Simula tabela legada sem a coluna video_director_mode
        conn.execute(
            text(
                "CREATE TABLE video_jobs ("
                "id TEXT PRIMARY KEY, "
                "topic TEXT"
                ");"
            )
        )
        conn.execute(
            text("INSERT INTO video_jobs (id, topic) VALUES ('old-1', 'Legacy Job');")
        )

        # Aplica upgrade compatível com SQLite
        conn.execute(
            text(
                "ALTER TABLE video_jobs ADD COLUMN video_director_mode VARCHAR(32) "
                "CHECK (video_director_mode IS NULL OR video_director_mode IN ('legacy', 'director'));"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_video_jobs_video_director_mode "
                "ON video_jobs (video_director_mode);"
            )
        )

        # Registro antigo deve permanecer NULL
        row = conn.execute(
            text("SELECT video_director_mode FROM video_jobs WHERE id = 'old-1';")
        ).fetchone()
        assert row is not None
        assert row[0] is None

        # Valores permitidos: 'legacy', 'director', NULL
        conn.execute(
            text(
                "INSERT INTO video_jobs (id, topic, video_director_mode) "
                "VALUES ('new-1', 'Director Job', 'director');"
            )
        )
        conn.execute(
            text(
                "INSERT INTO video_jobs (id, topic, video_director_mode) "
                "VALUES ('new-2', 'Legacy Explicit', 'legacy');"
            )
        )
        conn.execute(
            text(
                "INSERT INTO video_jobs (id, topic, video_director_mode) "
                "VALUES ('new-3', 'Null Explicit', NULL);"
            )
        )

    # Valor inválido deve ser rejeitado pelo CHECK constraint
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO video_jobs (id, topic, video_director_mode) "
                    "VALUES ('bad-1', 'Invalid', 'invalid_mode');"
                )
            )

    # Downgrade remove índice e coluna
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_video_jobs_video_director_mode;"))
        conn.execute(text("ALTER TABLE video_jobs DROP COLUMN video_director_mode;"))
        cols = conn.execute(text("PRAGMA table_info(video_jobs);")).fetchall()
        col_names = [c[1] for c in cols]
        assert "video_director_mode" not in col_names


def test_migrate_raises_and_does_not_print_success_on_unexpected_error(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "fail_migrate.db"
    engine = create_engine(f"sqlite:///{db_path}")

    monkeypatch.setattr(
        migrate_db,
        "MIGRATIONS",
        [("broken_step", "INVALID SQL SYNTAX THAT MUST FAIL;")],
    )

    with pytest.raises(SQLAlchemyError):
        migrate_db.migrate(target_engine=engine)

    captured = capsys.readouterr()
    assert "broken_step" in captured.out
    assert "Migração concluída" not in captured.out


def test_downgrade_raises_and_does_not_print_success_on_unexpected_error(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "fail_downgrade.db"
    engine = create_engine(f"sqlite:///{db_path}")

    monkeypatch.setattr(
        migrate_db,
        "DOWNGRADE_MIGRATIONS",
        [("broken_downgrade", "INVALID SQL SYNTAX THAT MUST FAIL;")],
    )

    with pytest.raises(SQLAlchemyError):
        migrate_db.downgrade(target_engine=engine)

    captured = capsys.readouterr()
    assert "broken_downgrade" in captured.out
    assert "Downgrade concluído" not in captured.out


_KNOWN_PRODUCTION_DB_NAMES = {
    "postgres",
    "prod",
    "production",
    "main",
    "primary",
    "dark_channel",
    "dark_channel_factory",
    "darkchannel",
}


def _require_validated_test_database_url() -> str:
    """
    Valida explicitamente TEST_DATABASE_URL antes de qualquer operação no PostgreSQL.

    Variáveis de ambiente exigidas:
    - TEST_DATABASE_URL: URL PostgreSQL exclusiva para testes (nunca usa DATABASE_URL como fallback).
    - ALLOW_DESTRUCTIVE_DB_TESTS=1: confirmação explícita de que o destino é um banco de testes
      onde schemas temporários podem ser criados e removidos.
    - PRODUCTION_DB_NAMES (opcional): lista separada por vírgula com nomes adicionais de bancos
      de produção a serem rejeitados.
    """
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url or not test_db_url.startswith("postgresql"):
        pytest.skip(
            "TEST_DATABASE_URL (PostgreSQL isolado) não configurada; "
            "execução em PostgreSQL real pendente."
        )

    allow_destructive = (os.getenv("ALLOW_DESTRUCTIVE_DB_TESTS") or "").strip().lower()
    if allow_destructive not in {"1", "true"}:
        pytest.skip(
            "ALLOW_DESTRUCTIVE_DB_TESTS=1 não configurada; confirmação explícita "
            "de banco de testes obrigatória antes de criar/remover schemas."
        )

    parsed_test = make_url(test_db_url)
    db_name = (parsed_test.database or "").strip().lower()
    if not db_name:
        raise ValueError("TEST_DATABASE_URL deve especificar explicitamente o nome do banco de testes.")

    configured_prod = {
        name.strip().lower()
        for name in (os.getenv("PRODUCTION_DB_NAMES") or "").split(",")
        if name.strip()
    }
    rejected_names = _KNOWN_PRODUCTION_DB_NAMES | configured_prod
    if db_name in rejected_names:
        raise ValueError(
            f"TEST_DATABASE_URL aponta para nome de banco proibido/produção: '{db_name}'."
        )

    prod_db_url = os.getenv("DATABASE_URL")
    if prod_db_url and prod_db_url.startswith("postgresql"):
        parsed_prod = make_url(prod_db_url)
        if (
            (parsed_test.host or "").lower() == (parsed_prod.host or "").lower()
            and parsed_test.port == parsed_prod.port
            and db_name == (parsed_prod.database or "").strip().lower()
        ):
            raise ValueError(
                "TEST_DATABASE_URL não pode apontar para o mesmo banco configurado em DATABASE_URL."
            )

    return test_db_url


def _verify_connected_test_database(admin_engine, expected_db_name: str) -> None:
    """
    Confirma o nome real do banco conectado via SELECT current_database() antes de
    qualquer criação/remoção de schema.
    """
    with admin_engine.connect() as conn:
        actual_db = (conn.execute(text("SELECT current_database();")).scalar() or "").strip().lower()
    if actual_db != expected_db_name:
        raise ValueError(
            f"current_database()='{actual_db}' diverge do banco de testes esperado '{expected_db_name}'."
        )
    if actual_db in _KNOWN_PRODUCTION_DB_NAMES:
        raise ValueError(f"current_database()='{actual_db}' é um banco de produção proibido.")


def test_postgres_real_migration_upgrade_idempotency_and_downgrade():
    """
    Teste de integração real em PostgreSQL isolado por schema exclusivo (via TEST_DATABASE_URL).

    Regras de segurança:
    - TEST_DATABASE_URL e ALLOW_DESTRUCTIVE_DB_TESTS=1 são obrigatórias; nunca utiliza DATABASE_URL como fallback.
    - Nunca opera em produção nem no schema `public`.
    - Nunca executa DROP TABLE em tabelas preexistentes.
    - Cria um schema exclusivo temporário (`test_mig_<uuid>`), configura `search_path`
      para esse schema em todas as conexões do engine isolado e remove exclusivamente
      esse schema no bloco `finally`.
    - Documentação histórica: `migrate_db.migrate()` executa todas as migrations 001–008
      em sequência; por isso, o teste parte de uma estrutura legada mínima (`tenants`,
      `channels`, `video_jobs`) compatível com todas elas.
    """
    test_db_url = _require_validated_test_database_url()

    schema_name = f"test_mig_{uuid.uuid4().hex}"
    admin_engine = create_engine(test_db_url)
    isolated_engine = create_engine(test_db_url)

    @event.listens_for(isolated_engine, "connect")
    def _set_isolated_search_path(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f'SET search_path TO "{schema_name}"')
        finally:
            cursor.close()

    try:
        _verify_connected_test_database(
            admin_engine,
            (make_url(test_db_url).database or "").strip().lower(),
        )
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}";'))

        with isolated_engine.begin() as conn:
            current_schema = conn.execute(text("SELECT current_schema();")).scalar()
            assert current_schema == schema_name

            # Cria tabelas legadas mínimas compatíveis com as migrations 001-008
            conn.execute(text("CREATE TABLE tenants (id SERIAL PRIMARY KEY, name TEXT);"))
            conn.execute(text("CREATE TABLE channels (id SERIAL PRIMARY KEY, name TEXT);"))
            conn.execute(
                text(
                    "CREATE TABLE video_jobs ("
                    "id TEXT PRIMARY KEY, "
                    "topic TEXT, "
                    "error_message VARCHAR(255)"
                    ");"
                )
            )
            conn.execute(text("INSERT INTO video_jobs (id, topic) VALUES ('old-1', 'Legacy Job');"))

        # 1. Upgrade real executando migrate_db.migrate() (migrations 001-008) no schema isolado
        migrate_db.migrate(target_engine=isolated_engine)

        with isolated_engine.begin() as conn:
            # Preservação de registros existentes como NULL
            row = conn.execute(
                text("SELECT video_director_mode FROM video_jobs WHERE id = 'old-1';")
            ).fetchone()
            assert row is not None
            assert row[0] is None

            # Inserção com 'legacy', 'director' e NULL
            conn.execute(
                text(
                    "INSERT INTO video_jobs (id, topic, video_director_mode) "
                    "VALUES ('new-legacy', 'Legacy Explicit', 'legacy');"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO video_jobs (id, topic, video_director_mode) "
                    "VALUES ('new-director', 'Director Explicit', 'director');"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO video_jobs (id, topic, video_director_mode) "
                    "VALUES ('new-null', 'Null Explicit', NULL);"
                )
            )

            # Verificação de índice e constraint no catálogo do PostgreSQL restrito ao schema isolado
            idx = conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = :schema "
                    "AND tablename = 'video_jobs' "
                    "AND indexname = 'ix_video_jobs_video_director_mode';"
                ),
                {"schema": schema_name},
            ).fetchone()
            assert idx is not None

            chk = conn.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE n.nspname = :schema "
                    "AND c.conname = 'chk_video_jobs_video_director_mode';"
                ),
                {"schema": schema_name},
            ).fetchone()
            assert chk is not None

        # Rejeição de valores inválidos
        with pytest.raises(IntegrityError):
            with isolated_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO video_jobs (id, topic, video_director_mode) "
                        "VALUES ('bad-1', 'Invalid', 'invalid_mode');"
                    )
                )

        # 2. Reexecução idempotente do upgrade
        migrate_db.migrate(target_engine=isolated_engine)

        # 3. Downgrade real executando migrate_db.downgrade() no schema isolado
        migrate_db.downgrade(target_engine=isolated_engine)

        with isolated_engine.begin() as conn:
            idx_after = conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = :schema "
                    "AND tablename = 'video_jobs' "
                    "AND indexname = 'ix_video_jobs_video_director_mode';"
                ),
                {"schema": schema_name},
            ).fetchone()
            assert idx_after is None

            chk_after = conn.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE n.nspname = :schema "
                    "AND c.conname = 'chk_video_jobs_video_director_mode';"
                ),
                {"schema": schema_name},
            ).fetchone()
            assert chk_after is None

            col_after = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema "
                    "AND table_name = 'video_jobs' "
                    "AND column_name = 'video_director_mode';"
                ),
                {"schema": schema_name},
            ).fetchone()
            assert col_after is None

        # 4. Reexecução segura do downgrade
        migrate_db.downgrade(target_engine=isolated_engine)
    finally:
        isolated_engine.dispose()
        try:
            with admin_engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE;'))
        finally:
            admin_engine.dispose()


def test_postgres_test_requires_test_database_url_without_database_url_fallback(monkeypatch):
    """
    Garante que o teste PostgreSQL exige TEST_DATABASE_URL e nunca usa DATABASE_URL
    como fallback (proteção contra execução acidental em produção).
    """
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_DB_TESTS", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-user:prod-pass@prod-host/prod-db")

    with pytest.raises(pytest.skip.Exception):
        test_postgres_real_migration_upgrade_idempotency_and_downgrade()


def test_postgres_guard_rejects_production_db_names_and_requires_confirmation(monkeypatch):
    """
    Garante que a validação rejeita bancos de produção conhecidos/configurados,
    rejeita colisão com DATABASE_URL e exige ALLOW_DESTRUCTIVE_DB_TESTS=1.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/dark_channel_test")
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_DB_TESTS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _require_validated_test_database_url()

    monkeypatch.setenv("ALLOW_DESTRUCTIVE_DB_TESTS", "1")
    assert _require_validated_test_database_url().endswith("/dark_channel_test")

    for forbidden in ("postgres", "prod", "production", "dark_channel_factory"):
        monkeypatch.setenv("TEST_DATABASE_URL", f"postgresql://user:pass@localhost:5432/{forbidden}")
        with pytest.raises(ValueError, match="proibido/produção"):
            _require_validated_test_database_url()

    monkeypatch.setenv("PRODUCTION_DB_NAMES", "custom_prod_db")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/custom_prod_db")
    with pytest.raises(ValueError, match="proibido/produção"):
        _require_validated_test_database_url()

    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@db.internal:5432/app_staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.internal:5432/app_staging")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _require_validated_test_database_url()


def test_migration_008_check_query_scopes_by_conrelid_and_namespace():
    """
    Garante que a verificação da constraint na migration 008 filtra por
    pg_constraint.conrelid, tabela video_jobs e pg_namespace (current_schema()).
    """
    migrations_map = dict(migrate_db.MIGRATIONS)
    check_sql = migrations_map["video_director_mode_check"]

    assert "conrelid" in check_sql
    assert "pg_class" in check_sql
    assert "pg_namespace" in check_sql
    assert "video_jobs" in check_sql
    assert "current_schema()" in check_sql


def test_postgres_homonymous_constraint_in_other_schema_does_not_block_migration():
    """
    Simula uma constraint homônima `chk_video_jobs_video_director_mode` em outro schema
    e confirma que a migration 008 ainda cria a constraint na tabela `video_jobs` do schema isolado.
    """
    test_db_url = _require_validated_test_database_url()

    other_schema = f"test_other_{uuid.uuid4().hex}"
    target_schema = f"test_mig_{uuid.uuid4().hex}"
    admin_engine = create_engine(test_db_url)
    isolated_engine = create_engine(test_db_url)

    @event.listens_for(isolated_engine, "connect")
    def _set_isolated_search_path(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f'SET search_path TO "{target_schema}"')
        finally:
            cursor.close()

    try:
        _verify_connected_test_database(
            admin_engine,
            (make_url(test_db_url).database or "").strip().lower(),
        )
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{other_schema}";'))
            conn.execute(text(f'CREATE SCHEMA "{target_schema}";'))
            conn.execute(
                text(
                    f'CREATE TABLE "{other_schema}".video_jobs ('
                    "id TEXT PRIMARY KEY, "
                    "video_director_mode VARCHAR(32), "
                    "CONSTRAINT chk_video_jobs_video_director_mode "
                    "CHECK (video_director_mode IS NULL OR video_director_mode IN ('legacy', 'director'))"
                    ");"
                )
            )

        with isolated_engine.begin() as conn:
            conn.execute(text("CREATE TABLE tenants (id SERIAL PRIMARY KEY, name TEXT);"))
            conn.execute(text("CREATE TABLE channels (id SERIAL PRIMARY KEY, name TEXT);"))
            conn.execute(
                text(
                    "CREATE TABLE video_jobs ("
                    "id TEXT PRIMARY KEY, "
                    "topic TEXT, "
                    "error_message VARCHAR(255)"
                    ");"
                )
            )

        migrate_db.migrate(target_engine=isolated_engine)

        with isolated_engine.begin() as conn:
            chk = conn.execute(
                text(
                    "SELECT c.conname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = :schema "
                    "AND t.relname = 'video_jobs' "
                    "AND c.conname = 'chk_video_jobs_video_director_mode';"
                ),
                {"schema": target_schema},
            ).fetchone()
            assert chk is not None
    finally:
        isolated_engine.dispose()
        try:
            with admin_engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{target_schema}" CASCADE;'))
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{other_schema}" CASCADE;'))
        finally:
            admin_engine.dispose()
