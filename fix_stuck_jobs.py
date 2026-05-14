"""
fix_stuck_jobs.py — Dark Channel Factory
Reseta jobs que ficaram travados em estados intermediarios (THINKING, DRAFTED, PROCESSING).
Esses "jobs zumbis" ocorrem quando o servico e reiniciado no meio de uma operacao.

Modos:
  python fix_stuck_jobs.py          -- Interactive: mostra contagens e pede confirmacao
  python fix_stuck_jobs.py --auto   -- Automatico: reseta sem pedir confirmacao (para CI/scripts)
  python fix_stuck_jobs.py --dry    -- Dry run: mostra o que seria feito sem alterar nada
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[ERR] DATABASE_URL nao configurada no .env!")
    sys.exit(1)

DRY_RUN   = "--dry" in sys.argv
AUTO_MODE = "--auto" in sys.argv

from sqlalchemy import create_engine, text

engine = create_engine(DATABASE_URL)

# ── Configuracao de estados zumbi ────────────────────────────────────────────
# Jobs nesses estados ficaram presos no meio de uma operacao.
# THINKING  = copywriter chamado mas nao completou (crash/restart do servico)
# DRAFTED   = script gerado mas nunca movido para READY_FOR_EDIT
# PROCESSING = worker pegou o job mas crashou durante o render
# 
# NAO resetar: PUBLISHED, FAILED (esses sao estados finais intencionais)
STUCK_STATES = ["THINKING", "DRAFTED", "PROCESSING", "UPLOADING"]
RESET_TO     = "PENDING"

print("=" * 60)
print("  FIX STUCK JOBS -- Dark Channel Factory")
print("=" * 60)
if DRY_RUN:
    print("  [DRY RUN] Nenhuma alteracao sera feita no banco.\n")

with engine.connect() as conn:
    # 1. Conta jobs por estado zumbi
    print("\n[INFO] Contagem atual de jobs zumbis:")
    total_stuck = 0
    for state in STUCK_STATES:
        count = conn.execute(
            text("SELECT COUNT(*) FROM video_jobs WHERE status = :s"),
            {"s": state}
        ).scalar()
        if count > 0:
            print(f"  - {state}: {count} jobs")
            total_stuck += count
        else:
            print(f"  - {state}: 0 (nenhum travado)")

    if total_stuck == 0:
        print("\n[OK] Nenhum job zumbi encontrado. Fila saudavel!")
        sys.exit(0)

    print(f"\n[TOTAL] {total_stuck} jobs serao resetados para '{RESET_TO}'")

    # 2. Pede confirmacao (exceto em modo auto ou dry run)
    if not AUTO_MODE and not DRY_RUN:
        ans = input(f"\n[?] Resetar {total_stuck} jobs para PENDING? (sim/nao): ").strip().lower()
        if ans not in ("sim", "s", "yes", "y"):
            print("[INFO] Operacao cancelada.")
            sys.exit(0)

    if DRY_RUN:
        print("\n[DRY RUN] Nenhuma alteracao feita. Remova --dry para aplicar.")
        sys.exit(0)

    # 3. Aplica o reset atomicamente
    print("\n[RESET] Aplicando reset...")

    # SQLAlchemy 2.x usa autobegin: a conexao ja esta em transacao.
    # NAO chamar conn.begin() novamente -- isso causa InvalidRequestError.
    result = conn.execute(
        text("""
            UPDATE video_jobs
            SET status = :new_status,
                error_message = CONCAT('[RESET] Era: ', status, ' -- reiniciado em ', NOW()::text)
            WHERE status = ANY(:stuck_states)
        """),
        {
            "new_status": RESET_TO,
            "stuck_states": STUCK_STATES
        }
    )
    rows_updated = result.rowcount
    conn.commit()  # Commita a transacao do autobegin

    print(f"\n[OK] {rows_updated} jobs resetados para '{RESET_TO}'.")
    
    # 4. Mostra nova contagem
    print("\n[INFO] Contagem pos-reset:")
    rows = conn.execute(text(
        "SELECT status, COUNT(*) as cnt FROM video_jobs GROUP BY status ORDER BY cnt DESC"
    )).fetchall()
    for status, count in rows:
        print(f"  - {status}: {count}")

print("\n[DONE] Pronto! Reinicie o factory_service.py para processar os jobs resetados.")
print("       Ou use o RUN_FACTORY.bat opcao [1].")
