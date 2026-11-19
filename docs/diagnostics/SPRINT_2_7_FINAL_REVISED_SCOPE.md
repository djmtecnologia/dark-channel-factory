# SPRINT 2.7 — ESCOPO FINAL REVISADO

**Data:** 2026-11-17  
**Diretriz:** Preservar Pipeline B, preservar componentes homologados da Sprint 2.6 (`director_execution_interpreter.py`, `director_media_router.py`, `director_camera_engine.py`), manter compatibilidade Legacy.

---

## Tarefas e Dependências

1. **T1 — Compatibilidade de Perfis e Validadores (P0)**
   - Parametrizar `agents/content_validator/agent.py` por perfil + suportar `MOCK_LLM=true`.
   - Validar `max_words` em `shared/production_profiles.py` e `scene_count` em `shared/script_validator.py`.
   - *Dependências:* Nenhuma.

2. **T2 — Cloud Hub: Estado, Lock e Concorrência (P0)**
   - Tornar `get_next_job()` atômico (SQLite + PostgreSQL) transicionando para `RENDERING` e gravando `worker_id`.
   - Validar transições em `report_status()` e persistir `published_url` + `published_at`.
   - *Dependências:* Nenhuma.

3. **T3 — Desktop Node: Sincronização e Idempotência do Upload (P0)**
   - Corrigir `/worker/job-status` → `/worker/report-status/{job_id}`.
   - Remover `PUBLISHED` imediato pós-`render_video()`.
   - Implementar checkpoint local para idempotência de upload.
   - *Dependências:* T2 (`report_status` atualizado).

4. **T4 — GlobalScheduler: Deduplicação e Recuperação (P1)**
   - Implementar deduplicação persistente no DB em `run_daily_cycle()` com chave `(channel_id, video_type, scheduled_date, topic_hash)` e cota diária configurável (permitindo override em staging).
   - *Dependências:* T2 (estados consolidados).

5. **T5 — Política de Créditos e Custos (P1)**
   - Documentar política de reserva, débito e estorno por perfil usando `Tenant.credits_balance` sem bloqueio em produção nesta sprint.
   - *Dependências:* Nenhuma.

6. **T6 — Homologação E2E em Staging (P0)**
   - Executar fluxo completo `PILOT_5MIN` em banco isolado de staging com dry-run do uploader.
   - *Dependências:* T1, T2, T3, T4.
