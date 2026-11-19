# SPRINT 2.7 — ETAPA A: INVENTÁRIO DO REPOSITÓRIO

**Data:** 2026-11-17  
**Escopo:** Auditoria técnica somente leitura (Arquitetura Operacional: Pipeline B — Cloud Hub + Desktop Node Windows)

## 1. Arquitetura Operacional vs. Referência
- **Pipeline B (Operacional):** `RUN_FACTORY.bat` → `factory_service.py` (`GlobalScheduler`) + `cloud_hub/main.py` (FastAPI + SQLModel) + `desktop_node.py` (Renderização FFmpeg, TTS Edge-TTS/XTTS/Chatterbox, Whisper, QC local, Upload Playwright).
- **Arquitetura A (Referência modular, não operacional no fluxo principal):** `docker-compose.yml`, `Dockerfile.worker`, `orchestrator/`, `desktop_worker/main.py`, `desktop_worker/uploader/`.

## 2. Inventário de Componentes

| Componente | Arquivo | Função | Chamado por | Estado |
|---|---|---|---|---|
| Painel de Controle | `RUN_FACTORY.bat` | Entrypoint CLI Windows (inicia serviço, worker, migrações, resets) | Operador | Implementado e integrado |
| Serviço Central 24/7 | `factory_service.py` | Loop principal: `run_daily_cycle()` (10min), `check_viral_and_replicate()` (10min), `process_pending_jobs()` (30s) | `RUN_FACTORY.bat` | Implementado e integrado |
| Agendador Global | `agents/orchestrator/global_scheduler.py` | Descoberta de temas (`ViralScout`), criação de `VideoJob` (3 LONG + 9 SHORT), disparo do Copywriter | `factory_service.py` | Implementado e integrado (com falhas de deduplicação) |
| Cloud Hub API | `cloud_hub/main.py` | API FastAPI, dashboard, `/jobs/create`, `/worker/next-job/{market_region}`, `/worker/report-status/{job_id}` | `desktop_node.py`, Web/Admin | Implementado e integrado (com lacunas de contrato) |
| Modelos de Dados | `cloud_hub/models.py` | `Tenant` (`credits_balance`), `Channel`, `VideoJob`, `PerformanceLog`, `AffiliateProduct`, `ViralTheme` | `cloud_hub/main.py`, `global_scheduler.py` | Implementado e integrado |
| Copywriter | `cloud_hub/core/copywriter.py` | `run_copywriter_phase()`: gera roteiro, cenas, metadados, chama `integrate_video_director()` | `global_scheduler.py` | Implementado e integrado |
| AI Video Director | `agents/video_director/director_engine.py`, `pipeline_adapter.py`, `cloud_hub/core/video_director_integration.py` | Gera Master AV Plan, VisualBible, direção por cena, fallback Legacy | `copywriter.py` | Implementado e integrado |
| Desktop Node Worker | `desktop_node.py` | Polling `/worker/next-job`, `render_video(job_data)`, TTS, Whisper, aquisição de mídia, FFmpeg, QC, fila de upload | `RUN_FACTORY.bat` | Implementado e integrado (com bugs de status/upload) |
| Director Execution Interpreter | `desktop_worker/director_execution_interpreter.py` | Interpreta Master AV Plan / cenas V2 para execução no Desktop Node (Sprint 2.6 T1) | `desktop_node.py`, `director_media_router.py` | Implementado e integrado (Homologado) |
| Director Media Router | `desktop_worker/director_media_router.py` | Roteia fontes de mídia conforme direção (Sprint 2.6 T2) | `desktop_node.py` | Implementado e integrado (Homologado) |
| Director Camera Engine | `desktop_worker/director_camera_engine.py` | Gera filtros FFmpeg de câmera/movimento (Sprint 2.6 T3) | `desktop_node.py` | Implementado e integrado (Homologado) |
| Shared Contracts & Profiles | `shared/production_profiles.py`, `shared/models_v2.py`, `shared/scene_normalizer.py`, `shared/script_validator.py` | Perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`), normalização e validação | `copywriter.py`, `qc_agent.py`, `desktop_node.py` | Implementado e integrado |
| Quality Control | `agents/validator/qc_agent.py` | `ValidatorAgent.execute_quality_control()`: valida metadados, FFprobe, duração, resolução, legendas, thumbnail | `desktop_node.py` | Implementado e integrado |
| Publisher (Playwright) | `agents/publisher/playwright_uploader.py` | `publish_video_to_youtube()`: upload via Playwright + `chrome_profiles` | `desktop_node.py` (`upload_worker_loop`) | Implementado, mas sem integração comprovada de retorno de status/URL |
| Analytics | `agents/analyst/performance_analyst.py` | `PerformanceAnalyst`: calcula score e cria `ViralTheme` a partir de `performance_metrics` | `global_scheduler.py` | Implementado parcialmente (sem coleta real de métricas do YouTube) |
| Uploader (Arq. A) | `desktop_worker/uploader/` | Uploader modular da Arquitetura A | N/A (Arq. A) | Implementado, mas fora do Pipeline B |
