#!/usr/bin/env python3
"""
Health Check Script - Dark Channel Factory
Verifica se todos os componentes estão funcionando corretamente.
"""
import os
import sys
from typing import Dict, List, Tuple

# Cores para output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def check_mark(passed: bool) -> str:
    return f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"

def print_header(text: str):
    print(f"\n{BLUE}{'=' * 60}{RESET}")
    print(f"{BLUE}{text:^60}{RESET}")
    print(f"{BLUE}{'=' * 60}{RESET}\n")

def check_python_version() -> Tuple[bool, str]:
    """Verifica se Python >= 3.11"""
    version = sys.version_info
    is_ok = version >= (3, 11)
    msg = f"Python {version.major}.{version.minor}.{version.micro}"
    return is_ok, msg

def check_dependencies() -> List[Tuple[str, bool, str]]:
    """Verifica dependências Python críticas"""
    deps = [
        "sqlmodel",
        "pydantic",
        "dotenv",
        "edge_tts",
        "requests",
        "tenacity",
        "redis",
    ]
    
    results = []
    for dep in deps:
        try:
            __import__(dep)
            results.append((dep, True, "Instalado"))
        except ImportError:
            results.append((dep, False, "NÃO encontrado"))
    
    return results

def check_external_tools() -> List[Tuple[str, bool, str]]:
    """Verifica ferramentas externas (FFmpeg, etc)"""
    import subprocess
    
    tools = []
    
    # FFmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        version = result.stdout.decode().split('\n')[0]
        tools.append(("FFmpeg", result.returncode == 0, version[:50]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        tools.append(("FFmpeg", False, "NÃO instalado"))
    
    return tools

def check_env_variables() -> List[Tuple[str, bool, str]]:
    """Verifica variáveis de ambiente críticas"""
    from dotenv import load_dotenv
    load_dotenv()
    
    required_vars = [
        ("DATABASE_URL", True),
        ("REDIS_URL", False),
        ("GEMINI_API_KEY", True),
        ("PEXELS_API_KEY", True),
        ("OPENAI_API_KEY", False),
        ("YOUTUBE_API_KEY", False),
    ]
    
    results = []
    for var, is_required in required_vars:
        value = os.getenv(var)
        if value and value != f"your_{var.lower()}_here":
            status = "Configurado"
            is_ok = True
        elif is_required:
            status = "❌ OBRIGATÓRIO"
            is_ok = False
        else:
            status = "⚠️ Opcional (não configurado)"
            is_ok = True
        
        results.append((var, is_ok, status))
    
    return results

def check_database_connection() -> Tuple[bool, str]:
    """Testa conexão com banco de dados"""
    try:
        from dotenv import load_dotenv
        from sqlmodel import create_engine
        
        load_dotenv()
        DATABASE_URL = os.getenv("DATABASE_URL")
        
        if not DATABASE_URL:
            return False, "DATABASE_URL não configurada"
        
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            # Testa query simples
            result = conn.execute("SELECT 1")
            return True, f"Conectado: {DATABASE_URL[:30]}..."
    except Exception as e:
        return False, f"Erro: {str(e)[:50]}"

def check_redis_connection() -> Tuple[bool, str]:
    """Testa conexão com Redis"""
    try:
        import redis
        from dotenv import load_dotenv
        
        load_dotenv()
        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        
        r = redis.from_url(REDIS_URL)
        r.ping()
        return True, f"Redis OK: {REDIS_URL}"
    except Exception as e:
        return False, f"Redis não acessível: {str(e)[:50]}"

def check_model_imports() -> List[Tuple[str, bool, str]]:
    """Verifica se modelos carregam sem erro"""
    models = [
        "models",
    ]
    
    results = []
    for module in models:
        try:
            __import__(module)
            results.append((module, True, "Importado com sucesso"))
        except Exception as e:
            results.append((module, False, f"Erro: {str(e)[:40]}"))
    
    return results

def run_health_check():
    """Executa todos os checks"""
    print_header("DARK CHANNEL FACTORY - HEALTH CHECK")
    
    # 1. Python Version
    print(f"\n{YELLOW}1. Python Version{RESET}")
    passed, msg = check_python_version()
    print(f"  {check_mark(passed)} {msg}")
    if not passed:
        print(f"    {RED}ERRO: Requer Python 3.11+{RESET}")
    
    # 2. Dependências Python
    print(f"\n{YELLOW}2. Dependências Python{RESET}")
    deps = check_dependencies()
    all_deps_ok = all(ok for _, ok, _ in deps)
    for name, ok, status in deps:
        print(f"  {check_mark(ok)} {name:20s} - {status}")
    
    # 3. Ferramentas Externas
    print(f"\n{YELLOW}3. Ferramentas Externas{RESET}")
    tools = check_external_tools()
    for name, ok, status in tools:
        print(f"  {check_mark(ok)} {name:20s} - {status}")
    
    # 4. Variáveis de Ambiente
    print(f"\n{YELLOW}4. Variáveis de Ambiente{RESET}")
    env_vars = check_env_variables()
    all_env_ok = all(ok for _, ok, _ in env_vars)
    for name, ok, status in env_vars:
        print(f"  {check_mark(ok)} {name:25s} - {status}")
    
    # 5. Banco de Dados
    print(f"\n{YELLOW}5. Banco de Dados{RESET}")
    db_ok, db_msg = check_database_connection()
    print(f"  {check_mark(db_ok)} {db_msg}")
    
    # 6. Redis
    print(f"\n{YELLOW}6. Redis{RESET}")
    redis_ok, redis_msg = check_redis_connection()
    print(f"  {check_mark(redis_ok)} {redis_msg}")
    
    # 7. Modelos
    print(f"\n{YELLOW}7. Modelos e Imports{RESET}")
    models = check_model_imports()
    all_models_ok = all(ok for _, ok, _ in models)
    for name, ok, status in models:
        print(f"  {check_mark(ok)} {name:20s} - {status}")
    
    # Resumo Final
    print_header("RESUMO")
    
    all_critical_ok = (
        passed and 
        all_deps_ok and 
        all_env_ok and 
        db_ok and 
        all_models_ok
    )
    
    if all_critical_ok:
        print(f"{GREEN}✓ Sistema pronto para produção!{RESET}\n")
        return 0
    else:
        print(f"{RED}✗ Existem problemas que precisam ser corrigidos.{RESET}")
        print(f"{YELLOW}  Veja os erros marcados com ✗ acima.{RESET}\n")
        return 1

if __name__ == "__main__":
    exit_code = run_health_check()
    sys.exit(exit_code)
