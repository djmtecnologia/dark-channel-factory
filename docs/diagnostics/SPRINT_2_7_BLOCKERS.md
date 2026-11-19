# SPRINT 2.7 — ETAPA F: IDENTIFICAÇÃO DOS BLOQUEADORES

## P0 — Críticos (Impedem produção ou publicação completa confiável)

### P0-01: Reporte prematuro de `PUBLISHED` antes do upload real
- **Arquivos:** `desktop_node.py` (linhas 2720-2721)
- **Evidência:** `start_worker()` chama `requests.post(f"{CLOUD_HUB_URL}/worker/report-status/{job['job_id']}", params={"status": "PUBLISHED"})` logo após `render_video()` retornar `True`, enquanto o upload apenas foi enfileirado na thread `upload_worker_loop`.
- **Impacto:** Jobs são marcados como `PUBLISHED` mesmo que o upload falhe ou nem tenha começado.
- **Critério de aceite:** Status `PUBLISHED` só pode ser reportado após `publish_video_to_youtube()` retornar URL válida.

### P0-02: Endpoint `/worker/job-status` inexistente chamado pelo Desktop Node
- **Arquivos:** `desktop_node.py` (linhas 142, 2591) vs `cloud_hub/main.py` (linha 713)
- **Evidência:** `desktop_node.py` envia `POST /worker/job-status` (JSON) em falhas de QC e erro de upload, mas `cloud_hub/main.py` expõe apenas `POST /worker/report-status/{job_id}`.
- **Impacto:** Falhas de QC (`FAILED_QC`) e erros de upload (`FAILED` / `PENDING_UPLOAD`) nunca chegam ao banco.
- **Critério de aceite:** Todas as atualizações de status do worker usam contrato único e persistente no `cloud_hub`.

### P0-03: `published_url` e `published_at` nunca são persistidos após upload
- **Arquivos:** `desktop_node.py` (linha 131), `cloud_hub/main.py` (linha 714)
- **Evidência:** `upload_worker_loop()` obtém `video_url`, imprime no console e descarta; `report_status()` não aceita `published_url` nem `published_at`.
- **Impacto:** Impossibilita rastrear vídeos publicados e alimentar analytics.
- **Critério de aceite:** `published_url` e `published_at` salvos no `VideoJob` ao concluir upload.

### P0-04: `get_next_job` sem lock/transição de estado (`RENDERING`)
- **Arquivos:** `cloud_hub/main.py` (linhas 677-711)
- **Evidência:** `get_next_job` retorna jobs em `["DRAFTED", "APPROVED", "READY_FOR_EDIT"]` sem atualizar `status = "RENDERING"` ou `worker_id`.
- **Impacto:** Múltiplos workers ou retries processam o mesmo job simultaneamente.
- **Critério de aceite:** `get_next_job` marca atomicamente o job como `RENDERING` antes de retorná-lo.

---

## P1 — Altos (Comprometem qualidade, confiabilidade ou operação contínua)

### P1-01: Loop de criação de jobs a cada 10 minutos sem deduplicação
- **Arquivos:** `factory_service.py` (linha 90), `agents/orchestrator/global_scheduler.py` (linha 31)
- **Evidência:** `run_daily_cycle()` roda a cada 600s criando 12 jobs por canal ativo sem checar cota diária ou duplicidade.
- **Critério de aceite:** Limite diário por canal e verificação de duplicidade implementados.

### P1-02: Jobs com `script_data` pré-populado ficam presos em `PENDING`
- **Arquivos:** `agents/orchestrator/global_scheduler.py` (linha 192)
- **Evidência:** `if not job.script_data:` ignora jobs que já possuem roteiro.
- **Critério de aceite:** Jobs `PENDING` com `script_data` válido avançam para `READY_FOR_EDIT`.

### P1-03: `credits_balance` sem validação nem débito
- **Arquivos:** `cloud_hub/models.py` (linha 16), `cloud_hub/main.py` (linhas 76, 340)
- **Evidência:** Nenhuma rota ou worker consulta ou debita `credits_balance`.
- **Critério de aceite:** Verificação e débito explícito por job/perfil (ou política documentada).

---

## P2 / P3 — Médios e Baixos
- **P2-01:** `PerformanceAnalyst` sem coleta real de métricas do YouTube (`agents/analyst/performance_analyst.py`).
- **P3-01:** `desktop_node.py` carrega `model_whisper` no nível de módulo (import time), tornando imports e testes lentos.
