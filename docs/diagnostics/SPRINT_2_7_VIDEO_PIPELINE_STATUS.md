# SPRINT 2.7 — ETAPAS B, C e E: FLUXO DO VÍDEO E MATRIZ DE FUNCIONAMENTO

## 1. Fluxo Real Mapeado (Pipeline B)

1. **Descoberta de Temas (B1):** `factory_service.py` executa `scheduler.run_daily_cycle()` a cada 10 minutos (`> 600s`). `GlobalScheduler.run_daily_cycle()` cria 12 jobs (`3 LONG + 9 SHORT`) por canal ativo a cada execução, sem verificar se já foram criados jobs no dia ou se o tópico já existe.
2. **Persistência do Job (B2):** `VideoJob` nasce `PENDING`. `process_pending_jobs()` só processa `if not job.script_data:`, deixando jobs com `script_data` pré-populado presos em `PENDING`.
3. **Copywriter & AI Video Director (B3-B4):** `run_copywriter_phase()` gera roteiro e chama `integrate_video_director()`. Atualiza status para `READY_FOR_EDIT`.
4. **Desktop Node (B5):** `desktop_node.py` faz polling em `GET /worker/next-job/{market_region}`. O endpoint **não** marca o job como `RENDERING` nem grava `worker_id`, permitindo que múltiplos workers (ou retries) peguem o mesmo job.
5. **Narração e Legendas (B6):** `generate_voice_master()` usa `edge-tts` como padrão (com fallback de ElevenLabs, XTTSv2 e Chatterbox). `generate_subtitles_locally()` usa `whisper` carregado no import de `desktop_node.py`.
6. **Aquisição de Mídia & Montagem (B7-B8):** `director_media_router.py` + `fetch_premium_broll()` (Pexels, Pixabay, Wikimedia, Pollinations, cache local). `assemble_production()` usa `director_camera_engine.py` e FFmpeg (QSV/NVENC/libx264).
7. **Quality Control (B9):** `desktop_node.py` chama `ValidatorAgent.execute_quality_control()`. Se reprovado, tenta POST para `/worker/job-status` (**endpoint inexistente** em `cloud_hub/main.py`).
8. **Publicação no YouTube (B10):** `render_video()` coloca o vídeo na `upload_queue` (thread daemon) e retorna `True`. `start_worker()` imediatamente reporta `PUBLISHED` para `/worker/report-status/{job_id}` **antes do upload acontecer**. Quando `upload_worker_loop()` termina `publish_video_to_youtube()`, ele não envia `published_url` nem `published_at` para o `cloud_hub`.
9. **Pós-publicação e Analytics (B11):** `PerformanceAnalyst` lê `job.performance_metrics`, mas não há integração automática buscando métricas reais da YouTube Data API / Studio.

## 2. Auditoria de Confiabilidade e Custos (Etapa C)
- **credits_balance:** Definido em `Tenant` (`cloud_hub/models.py:16`) e inicializado no bootstrap (`cloud_hub/main.py:76,340`), mas **nunca é consultado, validado ou debitado** na criação ou execução de jobs. Risco alto de produção sem controle de custos.
- **Idempotência / Concorrência:** Ausente no `get_next_job` e no `run_daily_cycle`.
- **Startup pesado:** `desktop_node.py` carrega `model_whisper` no nível de módulo (import time).

## 3. Matriz de Funcionamento Completo (Etapa E)

| Etapa | Implementação | Integração | Teste automatizado | Teste real | Evidência | Resultado |
|---|---|---|---|---|---|---|
| B1. Descoberta de temas | Sim | Parcial | Parcial | Não | `factory_service.py` chama `run_daily_cycle()` a cada 10min sem dedup | AMARELO |
| B2. Criação/persistência job | Sim | Parcial | Sim | Não | `get_next_job` não faz lock (`RENDERING`) | AMARELO |
| B3. Copywriter | Sim | Sim | Sim | Não | `run_copywriter_phase()` integrado ao `GlobalScheduler` | AMARELO |
| B4. AI Video Director | Sim | Sim | Sim | Não | Sprint 2.6 homologado e integrado | VERDE |
| B5. Desktop Node (orquestração) | Sim | Falha | Parcial | Não | Reporta `PUBLISHED` antes do upload; chama `/worker/job-status` inexistente | VERMELHO |
| B6. Narração e legendas | Sim | Sim | Parcial | Não | Edge-TTS + Whisper integrados em `desktop_node.py` | AMARELO |
| B7. Aquisição de mídia | Sim | Sim | Sim | Não | `director_media_router.py` + Pexels/Pixabay/Pollinations | AMARELO |
| B8. Montagem audiovisual | Sim | Sim | Sim | Não | `director_camera_engine.py` + `assemble_production()` | AMARELO |
| B9. Quality Control | Sim | Parcial | Sim | Não | `ValidatorAgent` integrado, mas falha de QC chama endpoint inexistente | AMARELO |
| B10. Publicação YouTube | Sim | Falha | Não | Não | `upload_worker_loop` não retorna `published_url` nem status real ao Hub | VERMELHO |
| B11. Pós-publicação / Analytics | Parcial | Não | Não | Não | Sem coleta real de métricas do YouTube | VERMELHO |
| C. Controle de créditos | Não | Não | Não | Não | `credits_balance` nunca é debitado | VERMELHO |
