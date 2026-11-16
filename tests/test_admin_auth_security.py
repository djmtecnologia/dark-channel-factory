"""
Testes de segurança para autenticação administrativa em cloud_hub/main.py.
Garante que não existam credenciais padrão hardcoded e que o sistema falhe de forma segura.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from sqlmodel import Session, SQLModel, create_engine, select

from cloud_hub import main as cloud_hub_main
from cloud_hub.models import Channel, Tenant


def test_authenticate_admin_fails_closed_when_env_missing(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)

    creds = HTTPBasicCredentials(username="derik_admin", password="senha_temporaria_123")
    with pytest.raises(HTTPException) as exc_info:
        cloud_hub_main.authenticate_admin(credentials=creds)

    assert exc_info.value.status_code == 503


def test_authenticate_admin_rejects_old_default_credentials(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "env_admin")
    monkeypatch.setenv("ADMIN_PASS", "env_strong_pass")

    old_creds = HTTPBasicCredentials(username="derik_admin", password="senha_temporaria_123")
    with pytest.raises(HTTPException) as exc_info:
        cloud_hub_main.authenticate_admin(credentials=old_creds)

    assert exc_info.value.status_code == 401

    valid_creds = HTTPBasicCredentials(username="env_admin", password="env_strong_pass")
    assert cloud_hub_main.authenticate_admin(credentials=valid_creds) == "env_admin"


def test_login_action_rejects_null_password_hash_and_old_default_password():
    class _FakeExecResult:
        def __init__(self, tenant):
            self._tenant = tenant

        def first(self):
            return self._tenant

    class _FakeSession:
        def __init__(self, tenant):
            self._tenant = tenant

        def exec(self, _stmt):
            return _FakeExecResult(self._tenant)

    tenant_without_hash = SimpleNamespace(id=1, email="admin@example.com", password_hash=None)
    resp = cloud_hub_main.login_action(
        response=None,
        username="admin@example.com",
        password="senha_temporaria_123",
        session=_FakeSession(tenant_without_hash),
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=1"


def test_argon2id_password_hashing_and_login_verification():
    hashed = cloud_hub_main.hash_password("S3cur3!Pass#2026")
    assert hashed.startswith("$argon2id$")
    assert cloud_hub_main.verify_password(hashed, "S3cur3!Pass#2026") is True
    assert cloud_hub_main.verify_password(hashed, "wrong-password") is False
    assert cloud_hub_main.verify_password(None, "S3cur3!Pass#2026") is False
    assert cloud_hub_main.verify_password("plaintext_not_a_hash", "plaintext_not_a_hash") is False

    class _FakeExecResult:
        def __init__(self, tenant):
            self._tenant = tenant

        def first(self):
            return self._tenant

    class _FakeSession:
        def __init__(self, tenant):
            self._tenant = tenant

        def exec(self, _stmt):
            return _FakeExecResult(self._tenant)

    valid_tenant = SimpleNamespace(id=42, email="user@example.com", password_hash=hashed)

    ok_resp = cloud_hub_main.login_action(
        response=None,
        username="user@example.com",
        password="S3cur3!Pass#2026",
        session=_FakeSession(valid_tenant),
    )
    assert ok_resp.status_code == 303
    assert ok_resp.headers["location"] == "/"

    bad_resp = cloud_hub_main.login_action(
        response=None,
        username="user@example.com",
        password="wrong-password",
        session=_FakeSession(valid_tenant),
    )
    assert bad_resp.status_code == 303
    assert bad_resp.headers["location"] == "/login?error=1"

    invalid_hash_tenant = SimpleNamespace(id=43, email="user2@example.com", password_hash="not-an-argon2-hash")
    invalid_resp = cloud_hub_main.login_action(
        response=None,
        username="user2@example.com",
        password="not-an-argon2-hash",
        session=_FakeSession(invalid_hash_tenant),
    )
    assert invalid_resp.status_code == 303
    assert invalid_resp.headers["location"] == "/login?error=1"


def test_admin_bootstrap_disabled_by_default_and_validates_config(tmp_path, monkeypatch):
    db_path = tmp_path / "bootstrap_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    monkeypatch.delenv("ENABLE_ADMIN_BOOTSTRAP", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)

    with Session(test_engine) as session:
        assert cloud_hub_main._run_admin_bootstrap(session) is False
        assert session.exec(select(Tenant)).all() == []

    monkeypatch.setenv("ENABLE_ADMIN_BOOTSTRAP", "1")
    with Session(test_engine) as session:
        with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_EMAIL"):
            cloud_hub_main._run_admin_bootstrap(session)

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "ops-admin@example.com")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    with Session(test_engine) as session:
        with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN_PASSWORD"):
            cloud_hub_main._run_admin_bootstrap(session)

    secret_pw = "BootStr@p!2026#Safe"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", secret_pw)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PLAN", "invalid_plan")
    with Session(test_engine) as session:
        with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PLAN"):
            cloud_hub_main._run_admin_bootstrap(session)

    monkeypatch.setenv("BOOTSTRAP_ADMIN_PLAN", "pro")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_CREDITS", "-10")
    with Session(test_engine) as session:
        with pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_CREDITS"):
            cloud_hub_main._run_admin_bootstrap(session)

    monkeypatch.setenv("BOOTSTRAP_ADMIN_CREDITS", "150")
    monkeypatch.setenv("BOOTSTRAP_CREATE_MASTER_CHANNEL", "0")
    with Session(test_engine) as session:
        assert cloud_hub_main._run_admin_bootstrap(session) is True
        tenants = session.exec(select(Tenant)).all()
        assert len(tenants) == 1
        assert tenants[0].email == "ops-admin@example.com"
        assert tenants[0].plan_type == "pro"
        assert tenants[0].credits_balance == 150
        assert tenants[0].password_hash is not None
        assert tenants[0].password_hash.startswith("$argon2id$")
        assert secret_pw not in tenants[0].password_hash
        assert session.exec(select(Channel)).all() == []


def test_bootstrap_password_login_idempotency_and_no_log_exposure(tmp_path, monkeypatch, capsys, caplog):
    db_path = tmp_path / "bootstrap_auth.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(test_engine)

    initial_pw = "Initial!Admin#Pass99"
    monkeypatch.setenv("ENABLE_ADMIN_BOOTSTRAP", "1")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin-sec@example.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", initial_pw)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PLAN", "enterprise")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_CREDITS", "10")

    with Session(test_engine) as session:
        assert cloud_hub_main._run_admin_bootstrap(session) is True
        admin = session.exec(select(Tenant).where(Tenant.email == "admin-sec@example.com")).first()
        assert admin is not None
        original_hash = admin.password_hash

        # Primeiro login após bootstrap com senha correta
        ok_resp = cloud_hub_main.login_action(
            response=None,
            username="admin-sec@example.com",
            password=initial_pw,
            session=session,
        )
        assert ok_resp.status_code == 303
        assert ok_resp.headers["location"] == "/"

        # Senha incorreta rejeitada
        bad_resp = cloud_hub_main.login_action(
            response=None,
            username="admin-sec@example.com",
            password="wrong-password",
            session=session,
        )
        assert bad_resp.status_code == 303
        assert bad_resp.headers["location"] == "/login?error=1"

    # Bootstrap repetido não sobrescreve senha existente
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "Different!Password#2")
    with Session(test_engine) as session:
        assert cloud_hub_main._run_admin_bootstrap(session) is True
        admin_after = session.exec(select(Tenant).where(Tenant.email == "admin-sec@example.com")).first()
        assert admin_after.password_hash == original_hash

    # Administrador preexistente sem hash não é sobrescrito silenciosamente
    with Session(test_engine) as session:
        no_hash_admin = Tenant(
            name="Legacy Admin",
            email="legacy-no-hash@example.com",
            plan_type="pro",
            credits_balance=0,
            password_hash=None,
        )
        session.add(no_hash_admin)
        session.commit()

    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "legacy-no-hash@example.com")
    with Session(test_engine) as session:
        with pytest.raises(RuntimeError, match="sem password_hash"):
            cloud_hub_main._run_admin_bootstrap(session)

    captured = capsys.readouterr()
    assert initial_pw not in captured.out
    assert initial_pw not in captured.err
    assert initial_pw not in caplog.text
