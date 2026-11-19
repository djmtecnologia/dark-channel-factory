# SPRINT 2.7 — CRITÉRIOS DE ACEITE POR TAREFA

## Tarefa 1 — Compatibilidade de Perfis e Validadores
- `PILOT_5MIN` (600–850 palavras, 12–18 cenas, 270–330s) aprovado no `content_validator`, `script_validator` e `qc_agent`.
- Os 4 perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`) possuem regras explícitas e coerentes.
- Testes automatizados rodam 100% offline com `MOCK_LLM=true` / mocks determinísticos, sem depender de saldo ou API externa.

## Tarefa 2 — Cloud Hub: Contrato de Estado e Concorrência
- `GET /worker/next-job/{market_region}` transiciona atomicamente o job para `RENDERING` e registra `worker_id`.
- Dois workers simultâneos nunca recebem o mesmo job.
- `POST /worker/report-status/{job_id}` rejeita estados inválidos e persiste `published_url` e `published_at` quando `status == "PUBLISHED"`.
- Estado persiste após reinicialização do serviço.

## Tarefa 3 — Desktop Node e Sincronização de Publicação
- Nenhuma chamada ao endpoint inexistente `/worker/job-status`.
- `start_worker()` não reporta `PUBLISHED` ao concluir `render_video()`.
- `upload_worker_loop()` só reporta `PUBLISHED` após `publish_video_to_youtube()` retornar URL válida, enviando `published_url` e `published_at`.
- Falhas de QC reportam `FAILED_QC`; falhas de upload reportam erro sem marcar `PUBLISHED`.

## Tarefa 4 — GlobalScheduler: Deduplicação e Recuperação
- `run_daily_cycle()` respeita cota diária por canal e não duplica jobs a cada ciclo de 10 minutos, mesmo após reinicialização.
- `process_pending_jobs()` avança jobs `PENDING` que já possuem `script_data` válido para `READY_FOR_EDIT` sem gerar segundo roteiro.

## Tarefa 5 — Política de Créditos e Custos
- Documento formal especificando momento de reserva, débito, estorno em falha, prevenção de débito duplicado e estimativa de custo por perfil.

## Tarefa 6 — Homologação E2E em Staging
- Fluxo completo `PILOT_5MIN` validado em banco isolado de staging, com MP4 aprovado por `ValidatorAgent` + `ffprobe`, e dry-run do uploader confirmando persistência de status e URL.
- Suíte de regressão da Sprint 2.6 permanece 100% aprovada.
