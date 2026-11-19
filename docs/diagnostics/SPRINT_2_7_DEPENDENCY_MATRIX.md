# SPRINT 2.7 — MATRIZ DE DEPENDÊNCIAS

| Tarefa | Depende de | Bloqueia | Arquivos Candidatos | Risco Principal |
|---|---|---|---|---|
| **T1: Perfis e Validadores** | Nenhuma | T6 (Homologação E2E) | `agents/content_validator/agent.py`, `shared/production_profiles.py`, `shared/script_validator.py`, `agents/validator/qc_agent.py` | Regressão em regras legadas sem perfil explícito |
| **T2: Cloud Hub (Estado/Lock)** | Nenhuma | T3, T6 | `cloud_hub/main.py`, `cloud_hub/models.py` | Lock incompatível entre SQLite (staging) e Postgres (prod) |
| **T3: Desktop Node (Sync/Upload)** | T2 | T6 | `desktop_node.py` | Quebra de fluxo de upload assíncrono ou perda de estado em crash |
| **T4: GlobalScheduler (Dedup/Recovery)** | Nenhuma | T6 | `agents/orchestrator/global_scheduler.py`, `factory_service.py` | Cota diária bloquear testes se não configurável |
| **T5: Política de Créditos** | T2 (modelo) | — (não bloqueia T6 se em modo shadow) | `cloud_hub/models.py`, `cloud_hub/main.py` | Bloqueio indevido de jobs por saldo zerado em staging |
| **T6: Homologação E2E Staging** | T1, T2, T3, T4 | Encerramento da Sprint 2.7 | `tests/` (staging) | Dependência de FFmpeg/Edge-TTS/Whisper no ambiente |

## Ordem Recomendada de Execução
1. **T1 (Perfis/Validadores)** e **T2 (Cloud Hub)** em paralelo.
2. **T4 (GlobalScheduler)** em paralelo ou logo após T2.
3. **T3 (Desktop Node)** após T2 estar estabilizado.
4. **T5 (Política de Créditos)** — definição formal e validação não-bloqueante.
5. **T6 (Homologação E2E)** após T1–T4 concluídos e suíte de regressão da Sprint 2.6 verde.
