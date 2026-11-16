"""
Pytest configuration for dark-channel-factory test suite.
Garante isolamento absoluto de banco de dados nos testes, impedindo conexão ao Neon
inclusive durante a fase de coleta (test collection) e importação dos módulos.
"""
from __future__ import annotations

import os
import pytest

# 1. Isolamento Imediato no carregamento do conftest (pré-coleta dos testes)
os.environ["DATABASE_URL"] = ""


def pytest_configure(config: pytest.Config) -> None:
    """Hook executado na inicialização do pytest antes da coleta de testes."""
    os.environ["DATABASE_URL"] = ""


@pytest.fixture(autouse=True, scope="session")
def enforce_test_db_isolation():
    """Força DATABASE_URL="" em todos os testes para garantir isolamento contra o Neon."""
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = ""
    yield
    if original_url is not None:
        os.environ["DATABASE_URL"] = original_url
