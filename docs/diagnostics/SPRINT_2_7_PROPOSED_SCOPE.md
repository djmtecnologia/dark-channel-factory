# SPRINT 2.7 — ETAPA G: ESCOPO PROPOSTO DA SPRINT 2.7

**Diretriz:** Preservar Pipeline B, preservar componentes homologados da Sprint 2.6 e compatibilidade Legacy. Foco exclusivo em viabilizar produção e publicação reais de ponta a ponta.

## Ordem de Dependência das Tarefas

### Tarefa 1: Contrato de Estado e Lock de Worker no Cloud Hub
- **Objetivo:** Corrigir `get_next_job` e `report_status` para suportar lock (`RENDERING`), `worker_id`, `published_url` e `published_at`.
- **Resolve:** P0-03, P0-04.
- **Arquivos previstos:** `cloud_hub/main.py`.
- **Critérios de aceite:** `get_next_job` transiciona job para `RENDERING`; `report_status` persiste `published_url` e `published_at` quando `status == "PUBLISHED"`.
- **Testes necessários:** Teste unitário/integrado de `get_next_job` (concorrência) e `report_status`.

### Tarefa 2: Sincronização Real de Upload e Reporte de Status no Desktop Node
- **Objetivo:** Corrigir `desktop_node.py` para usar `/worker/report-status/{job_id}`, reportar `RENDERED`/`UPLOADING` após render, e só reportar `PUBLISHED` (com `published_url`) após sucesso real de `publish_video_to_youtube()`.
- **Resolve:** P0-01, P0-02, P0-03.
- **Arquivos previstos:** `desktop_node.py`.
- **Dependentes:** Tarefa 1.
- **Critérios de aceite:** Falhas de QC reportam `FAILED_QC` no Hub; falhas de upload reportam `FAILED`/`PENDING_UPLOAD`; sucesso de upload envia `published_url`.
- **Testes necessários:** Testes mockados de `upload_worker_loop` e fluxo `start_worker` sem chamadas reais ao YouTube.

### Tarefa 3: Deduplicação e Fluxo de Jobs no GlobalScheduler
- **Objetivo:** Evitar criação infinita de jobs a cada 10 minutos e destravar jobs `PENDING` que já possuem `script_data`.
- **Resolve:** P1-01, P1-02.
- **Arquivos previstos:** `agents/orchestrator/global_scheduler.py`, `factory_service.py`.
- **Critérios de aceite:** `run_daily_cycle()` respeita janela diária/cota por canal; `process_pending_jobs()` promove jobs com `script_data` para `READY_FOR_EDIT`.
- **Testes necessários:** Testes unitários do `GlobalScheduler`.

### Tarefa 4: Homologação E2E em Staging (Dry-Run + Publicação Controlada)
- **Objetivo:** Executar o protocolo da Etapa H com vídeo real (`PILOT_5MIN`) e validar todas as transições no banco.
- **Dependentes:** Tarefas 1, 2 e 3.
- **Critérios de aceite:** MP4 completo aprovado pelo `ValidatorAgent`, status e `published_url` persistidos no `VideoJob`.
