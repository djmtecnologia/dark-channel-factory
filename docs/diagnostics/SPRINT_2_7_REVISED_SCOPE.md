# SPRINT 2.7 — ESCOPO REVISADO

**Data:** 2026-11-17  
**Diretriz:** Preservar Pipeline B, preservar componentes homologados da Sprint 2.6 (`director_execution_interpreter.py`, `director_media_router.py`, `director_camera_engine.py`), sem migração para Arquitetura A.

---

## Tarefas Consolidadas

### Tarefa 1 — Compatibilidade de Perfis e Validadores (P0)
- **Objetivo:** Garantir que `content_validator`, `script_validator`, `qc_agent` e `production_profiles` respeitem os contratos dos 4 perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`) sem chamadas externas obrigatórias em testes.
- **Arquivos candidatos:** `agents/content_validator/agent.py`, `shared/production_profiles.py`, `shared/script_validator.py`, `agents/validator/qc_agent.py`, `tests/test_pilot_profile_and_contracts.py`.

### Tarefa 2 — Cloud Hub: Contrato de Estado e Concorrência (P0)
- **Objetivo:** Tornar `get_next_job` atômico (transição para `RENDERING` + registro de `worker_id`), corrigir `report_status` para validar transições, garantir idempotência e persistir `published_url` e `published_at`.
- **Resolve:** P0-03, P0-04.
- **Arquivos candidatos:** `cloud_hub/main.py`, `cloud_hub/models.py`.

### Tarefa 3 — Desktop Node e Sincronização de Publicação (P0)
- **Objetivo:** Substituir chamadas ao endpoint inexistente `/worker/job-status` por `/worker/report-status/{job_id}`, não reportar `PUBLISHED` após `render_video()`, só reportar `PUBLISHED` após URL válida de `publish_video_to_youtube()`, e reportar falhas de QC (`FAILED_QC`) e upload.
- **Resolve:** P0-01, P0-02, P0-03.
- **Arquivos candidatos:** `desktop_node.py`. (Sem refatoração além do necessário para contratos e sincronização.)

### Tarefa 4 — GlobalScheduler: Deduplicação e Recuperação de Jobs (P1)
- **Objetivo:** Impedir criação indiscriminada de jobs em `run_daily_cycle()` (cota diária + deduplicação persistente) e destravar jobs `PENDING` com `script_data` preexistente em `process_pending_jobs()`.
- **Resolve:** P1-01, P1-02.
- **Arquivos candidatos:** `agents/orchestrator/global_scheduler.py`, `factory_service.py`.

### Tarefa 5 — Política de Créditos e Custos (P1 — Proposta de Política)
- **Objetivo:** Definir política de reserva, débito, estorno em falha e estimativa de custo por perfil usando `credits_balance` (`cloud_hub/models.py:16`).
- **Decisão recomendada:** Documentar a política e preparar hooks/validações sem ativar cobrança bloqueante em produção nesta sprint (ou implementar apenas em modo observabilidade/staging), mitigando risco financeiro sem travar a homologação do Pipeline B.

### Tarefa 6 — Homologação E2E em Staging (P0)
- **Objetivo:** Executar o protocolo E2E completo com `PILOT_5MIN` em banco isolado de staging, com dry-run do uploader (e publicação real apenas em canal de teste mediante autorização explícita).
