# SPRINT 2.7 — PLANO REVISADO DE TESTES E HOMOLOGAÇÃO E2E

**Ambiente:** Staging isolado (SQLite/DB de teste, `SKIP_UPLOAD=true` / mock Playwright, `MOCK_LLM=true` nos testes automatizados).

---

## 1. Cenários Obrigatórios de Confiabilidade

| Cenário | Descrição | Resultado Esperado |
|---|---|---|
| **A — Concorrência** | Dois workers chamam `get_next_job` simultaneamente. | Apenas 1 worker recebe o job; status vira `RENDERING` atomicamente. |
| **B — Falha de upload** | Uploader retorna erro/exceção. | Job não fica `PUBLISHED`; erro persistido; estado permite recuperação. |
| **C — Upload bem-sucedido** | Uploader retorna URL válida. | `published_url`, `published_at` e `status="PUBLISHED"` persistidos no `VideoJob`. |
| **D — Reinicialização** | Reiniciar Cloud Hub durante o ciclo. | Estado persistido permite retomada sem duplicar jobs ou publicações. |
| **E — Falha de QC** | `ValidatorAgent` reprova o vídeo. | Status `FAILED_QC` + motivo persistidos; upload não iniciado. |
| **F — Roteiro preexistente** | Job `PENDING` com `script_data` válido. | `process_pending_jobs()` avança para `READY_FOR_EDIT` sem gerar novo roteiro. |
| **G — Limite diário** | Executar `run_daily_cycle()` repetidamente. | Respeita cota diária por canal e não cria duplicidades. |
| **H — Compatibilidade de perfis** | Validar roteiros e QC para os 4 perfis. | `PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN` e `SHORTS` aprovados dentro de seus limites, sem chamadas externas. |

---

## 2. Protocolo E2E em Staging (`PILOT_5MIN`)

1. Criar job `PILOT_5MIN` (`video_type="LONG"`, `status="PENDING"`).
2. Gerar roteiro + Master AV Plan via `process_pending_jobs()` → confirmar `READY_FOR_EDIT`.
3. Entregar job ao Desktop Node (`GET /worker/next-job/{market_region}`) → confirmar `RENDERING` + `worker_id`.
4. Gerar narração (Edge-TTS) e legendas (`.srt` Whisper).
5. Adquirir mídia (`director_media_router`) e renderizar MP4 (`assemble_production` + `director_camera_engine`).
6. Executar `ValidatorAgent.execute_quality_control()` e validar MP4 via `ffprobe` (duração 270–330s, >=1280x720, áudio+vídeo).
7. Executar dry-run do uploader (mock Playwright) e confirmar `PUBLISHED` + `published_url` + `published_at` persistidos apenas após retorno da URL.
8. Publicação real somente em canal de teste mediante autorização explícita.
