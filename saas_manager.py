"""
╔══════════════════════════════════════════════════════════════╗
║   Dark Channel Factory — SaaS Manager v1.0                  ║
║   Módulo: saas_manager.py                                    ║
║                                                              ║
║   Responsabilidade:                                          ║
║     • Validar licença e Client ID                            ║
║     • Consultar status de pagamento no Asaas                 ║
║     • Bloquear acesso após 30 dias de inadimplência          ║
║     • Reportar telemetria básica ao Cloud Hub                ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Configurações do SaaS
CLIENT_ID = os.getenv("CLIENT_ID", "anonymous_tenant")
LICENSE_KEY = os.getenv("LICENSE_KEY", "")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_ENV = os.getenv("ASAAS_ENV", "sandbox")
CLOUD_HUB_URL = os.getenv("CLOUD_HUB_URL", "https://dark-channel-factory.vercel.app")
BLOCK_DAYS = int(os.getenv("BLOCK_AFTER_DAYS_DELINQUENT", "30"))

# URLs do Asaas
ASAAS_URLS = {
    "sandbox": "https://sandbox.asaas.com/api/v3",
    "production": "https://www.asaas.com/api/v3"
}
ASAAS_BASE_URL = ASAAS_URLS.get(ASAAS_ENV, ASAAS_URLS["sandbox"])

def check_access():
    """
    Verifica se o cliente tem acesso ao sistema.
    Retorna (True, message) se OK, ou (False, error) se bloqueado.
    """
    print(f"\n[SaaS] 🛡️ Verificando acesso para Cliente: {CLIENT_ID}...")

    # 1. Validação Básica de Licença
    if not LICENSE_KEY:
        return False, "Chave de licença não encontrada no .env. Adquira uma em nosso site."

    # 2. Verificação de Inadimplência no Asaas (opcional se houver API KEY)
    if ASAAS_API_KEY and ASAAS_API_KEY != "sua_chave_aqui":
        try:
            status, msg = _verify_asaas_payment()
            if not status:
                return False, msg
        except Exception as e:
            print(f"[SaaS] ⚠️ Falha ao consultar Asaas: {e}. Prosseguindo com cache local...")

    # 3. Verificação no Cloud Hub (Multi-Tenant)
    try:
        res = requests.get(
            f"{CLOUD_HUB_URL}/api/tenant/status",
            params={"client_id": CLIENT_ID, "license": LICENSE_KEY},
            timeout=15
        )
        if res.status_code == 200:
            data = res.json()
            if not data.get("active", False):
                return False, f"Acesso suspenso pelo administrador: {data.get('reason', 'Motivo não informado')}"
            
            # Checa se há bloqueio por dias de atraso via Cloud Hub
            days_overdue = data.get("days_overdue", 0)
            if days_overdue > BLOCK_DAYS:
                return False, f"Acesso bloqueado por inadimplência ({days_overdue} dias). Regularize no painel Asaas."
        elif res.status_code == 404:
            print("[SaaS] ⚠️ Cliente não registrado no Cloud Hub. Operando em modo Standalone.")
    except Exception as e:
        print(f"[SaaS] 📡 Cloud Hub offline ou inacessível. Usando políticas de fallback.")

    print(f"[SaaS] ✅ Acesso autorizado. Bem-vindo, {CLIENT_ID}!")
    return True, "Acesso concedido."

def _verify_asaas_payment():
    """
    Consulta o Asaas para verificar se há faturas vencidas há mais de 30 dias.
    """
    headers = {"access_token": ASAAS_API_KEY}
    
    # Busca assinaturas do cliente (precisaríamos do customer id ou email)
    # Para simplificar nesta versão, buscamos pagamentos vencidos (OVERDUE)
    try:
        url = f"{ASAAS_BASE_URL}/payments?status=OVERDUE"
        res = requests.get(url, headers=headers, timeout=15)
        
        if res.status_code == 200:
            payments = res.json().get("data", [])
            for pay in payments:
                due_date = datetime.strptime(pay["dueDate"], "%Y-%m-%d")
                overdue_days = (datetime.now() - due_date).days
                
                if overdue_days > BLOCK_DAYS:
                    return False, f"Pagamento vencido há {overdue_days} dias (Vencimento: {pay['dueDate']}). Bloqueio automático ativado."
        
        return True, "Sem pendências críticas."
    except Exception as e:
        return True, f"Aviso: Erro Asaas ({e})"

def block_screen(message):
    """Exibe uma mensagem de bloqueio estilizada e encerra o programa."""
    print("\n" + "█" * 60)
    print("█" + " " * 58 + "█")
    print(f"█   🚫 ACESSO BLOQUEADO: {CLIENT_ID[:30]:<25} █")
    print("█" + " " * 58 + "█")
    print(f"█   MOTIVO: {message[:48]:<48} █")
    print("█" + " " * 58 + "█")
    print("█   Para regularizar, acesse: https://sua-area-membro.com  █")
    print("█" + " " * 58 + "█")
    print("█" * 60 + "\n")
    sys.exit(1)

if __name__ == "__main__":
    # Teste de execução
    status, msg = check_access()
    if not status:
        block_screen(msg)
    else:
        print("Módulo SaaS operacional.")
