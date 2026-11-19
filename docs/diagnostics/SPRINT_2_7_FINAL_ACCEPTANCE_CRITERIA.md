# SPRINT 2.7 — CRITÉRIOS DE ACEITE FINAIS

**Data:** 2026-11-17  

---

1. **T1 — Perfis e Validadores:**
   - `pytest tests/test_pilot_profile_and_contracts.py` passa 100% offline com `MOCK_LLM=true` para os 4 perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`).
   - `SHORTS` respeita teto efetivo de 59 s no QC.

2. **T2 — Cloud Hub:**
   - Teste de concorrência com 2+ workers simultâneos em `get_next_job()` entrega cada job a no máximo 1 worker, transicionando para `RENDERING` com `worker_id` preenchido.
   - `report_status()` rejeita transição direta `RENDERING` → `PUBLISHED` sem `published_url`, e é idempotente ao receber `PUBLISHED` duplicado.

3. **T3 — Desktop Node & Uploader:**
   - Nenhuma chamada ao endpoint inexistente `/worker/job-status`.
   - `render_video()` bem-sucedido reporta `PENDING_UPLOAD` (não `PUBLISHED`).
   - Simulação de queda pós-URL e reinicialização sincroniza `PUBLISHED` + `published_url` via checkpoint local sem re-upload.

4. **T4 — GlobalScheduler:**
   - Executar `run_daily_cycle()` duas vezes seguidas no mesmo dia não cria jobs duplicados no banco.
   - Cota diária por canal respeitada e configurável para testes de staging.

5. **T5 — Créditos:**
   - Política documentada e validada sem impacto bloqueante no fluxo de staging/produção.

6. **T6 — Regressão & Staging:**
   - Suíte Sprint 2.6 (`tests/test_sprint_2_6_integrated_regression.py` e `tests/test_director_camera_engine.py`) passa 100% sem alterações nos arquivos protegidos.
   - Fluxo E2E `PILOT_5MIN` concluído em staging isolado.
