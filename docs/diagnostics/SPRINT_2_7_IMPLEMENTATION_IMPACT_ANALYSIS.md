# SPRINT 2.7 — ANÁLISE DE IMPACTO DA IMPLEMENTAÇÃO

**Data:** 2026-11-17  
**Modo:** Somente leitura (análise pré-implementação)  

---

## 1. Mapeamento de Dependências e Impacto por Tarefa

| Tarefa | Arquivo / Função | Componente Afetado | Impacto Esperado | Risco | Mitigação | Teste Necessário |
|---|---|---|---|---|---|---|
| **T1** | `agents/content_validator/agent.py` (`SYSTEM_PROMPT` L44, `validate_content` L125) | Content Validator | Prompt parametrizado por perfil; `MOCK_LLM=true` usa heurística offline. | **BAIXO** | Fallback para 1800 palavras apenas quando `profile is None`. | Unitário offline (`test_pilot_profile_and_contracts.py`). |
| **T1** | `shared/production_profiles.py` (`validate_script_word_count` L217-222) | Production Profiles | Passa a validar `max_words` além de `min_words`. | **BAIXO** | Manter compatibilidade com jobs Legacy sem perfil. | Unitário para os 4 perfis. |
| **T1** | `shared/script_validator.py` (`validate_script` L354) | Script Validator | Valida `scene_count` contra perfil quando informado. | **BAIXO** | Ignorar se `profile` não estiver definido. | Unitário (`test_scene_contract_v2.py`). |
| **T2** | `cloud_hub/main.py` (`get_next_job` L678, `report_status` L714) | Cloud Hub API | Lock atômico com separação entre Render Lease e Upload Lease; validação de transições (`UPLOAD_UNKNOWN` bloqueia novo upload); persistência de `published_url` e `published_at`. | **CRÍTICO** | Dual-DB (PostgreSQL `FOR UPDATE SKIP LOCKED` + SQLite `BEGIN IMMEDIATE` + `UPDATE ... WHERE`). | Concorrência multi-thread/processo em SQLite e PostgreSQL. |
| **T2** | `cloud_hub/models.py` (`VideoJob` L57) | Banco de Dados | Possível adição de `retry_count` / `dedup_key` (opcional, com default). | **MÉDIO** | Colunas nullable ou com default para zero quebra de registros existentes. | Teste de schema e migration dry-run. |
| **T3** | `desktop_node.py` (`upload_worker_loop` L74, `start_worker` L2663) | Desktop Node / Uploader | Troca `/worker/job-status` por `/worker/report-status/{job_id}`; remove `PUBLISHED` pós-render; adiciona checkpoint local; exige `CONFIRMED_ABSENT` + comprovação de encerramento da tentativa anterior para retry após `UPLOAD_UNKNOWN`. | **ALTO** | Não alterar `render_video()` nem `director_*`; isolar apenas sincronização de status. | Integração com Cloud Hub mockado + teste de queda/retomada. |
| **T4** | `agents/orchestrator/global_scheduler.py` (`run_daily_cycle` L31) | GlobalScheduler | Deduplicação persistente no DB e respeito à cota diária por canal. | **MÉDIO** | Chave canônica `(channel_id, video_type, production_profile, scheduled_date_local, topic_hash)` com os 5 campos não nulos + flag de staging. | Integração (executar `run_daily_cycle` 2x e verificar 0 duplicatas). |
| **T5** | `cloud_hub/models.py` (`Tenant.credits_balance` L16) | Política de Créditos | Documentação de reserva/débito/estorno sem bloqueio em produção nesta sprint. | **BAIXO** | Modo observabilidade apenas. | Unitário de cálculo de custo por perfil. |
| **T6** | Staging E2E | Pipeline B completo | Validação ponta a ponta `PILOT_5MIN` em banco isolado. | **MÉDIO** | Dry-run do uploader; sem publicação real sem autorização. | E2E em staging. |

---

## 2. Análise de Impacto Cruzado

1. **Funcionalidades existentes que podem parar:** Workers antigos que não enviam `worker_id` (mitigação: rejeitar `worker_id = "unknown-worker"` após janela de transição da Etapa C).
2. **Formato de dados:** `report_status` passará a aceitar `published_url` no body/query mantendo compatibilidade com chamadas antigas.
3. **Jobs Legacy:** Preservados; validações de perfil só aplicam quando `production_profile` está preenchido; `video_director_mode` permanece opcional.
4. **Pipeline B:** Preservado integralmente.
5. **Arquitetura A:** Não tocada.
6. **Componentes Sprint 2.6 (`director_execution_interpreter.py`, `director_media_router.py`, `director_camera_engine.py`):** **Zero alteração.**
7. **Duplicação de jobs/publicações:** Mitigada por lock em `get_next_job`, checkpoint em `desktop_node.py` e deduplicação em `global_scheduler.py`.
8. **Perda de dados:** Nenhuma; alterações de schema são apenas aditivas (`nullable=True` ou `default`).
9. **Banco de dados:** Compatível com SQLite (testes) e PostgreSQL (produção).

---

## 3. Parecer de Impacto por Tarefa

- **T1:** **BAIXO IMPACTO** — implementação direta, com testes offline.
- **T2:** **ALTO IMPACTO** — exige validação rigorosa de concorrência em SQLite e PostgreSQL.
- **T3:** **IMPACTO CONTROLADO** — implementação em etapas, preservando `render_video()` e `director_*`.
- **T4:** **IMPACTO CONTROLADO** — deduplicação persistente com suporte a override em staging.
- **T5:** **BAIXO IMPACTO** — apenas política e observabilidade.
- **T6:** **IMPACTO CONTROLADO** — execução em staging isolado.
