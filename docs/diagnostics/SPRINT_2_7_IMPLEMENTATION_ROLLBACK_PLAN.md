# SPRINT 2.7 — PLANO DE IMPLANTAÇÃO E ROLLBACK

**Data:** 2026-11-17  
**Modo:** Somente leitura (planejamento)  

---

## 1. Ordem de Implantação Recomendada

1. **Fase 1 (Sem impacto em runtime):** T1 (Perfis/Validadores) + T5 (Política de Créditos).
2. **Fase 2 (Backend):** T2 (Cloud Hub `get_next_job` + `report_status`).
   - *Compatibilidade temporária:* `report_status` continua aceitando chamadas sem `published_url` apenas para estados não-`PUBLISHED`.
3. **Fase 3 (Worker & Scheduler):** T3 (`desktop_node.py`) + T4 (`global_scheduler.py`).
4. **Fase 4 (Validação):** T6 (Homologação E2E em Staging).

---

## 2. Monitoramento Pós-Implantação

- Taxa de jobs presos em `RENDERING` > 45 min ou `UPLOADING` > 20 min.
- Erros `409 Conflict` / `422` em `/worker/report-status/{job_id}`.
- Contagem de jobs criados por `run_daily_cycle()` vs cota diária esperada.

---

## 3. Critérios de Interrupção e Rollback

- **Interromper implantação se:**
  - Qualquer teste da suíte Sprint 2.6 falhar.
  - Dois workers receberem o mesmo `job_id`.
  - Ocorrer publicação duplicada no YouTube em staging.
- **Procedimento de Rollback:**
  - Reverter deploy da aplicação (`git revert` dos commits da Sprint 2.7).
  - Como eventuais novas colunas no DB são aditivas (`nullable=True` / `default`), não é necessário `DROP COLUMN` destrutivo no rollback imediato, evitando perda de dados.
