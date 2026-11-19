# SPRINT 2.7 — DECISÕES TÉCNICAS FINAIS (ETAPA 0.6)

**Status:** Especificação Técnica Consolidada (Pronta para Decisão do Proprietário — Corrigida na Etapa 0.6)  
**Autorização de Implementação:** **NÃO AUTORIZADA** (aguardando aprovação explícita do proprietário do projeto).

---

## 1. DECISÕES FINAIS CONSOLIDADAS

1. **Tema 1 (Matemática dos Perfis):** A duração real do áudio TTS e do vídeo renderizado (`ffprobe`) é a autoridade máxima; palavras são apenas orientação. O overhead de 12% é tratado como **hipótese** até calibração futura. Para `SHORTS` (`20–59s`), `120 palavras` estoura `59s` no cenário lento (`67,3s`), sendo a meta conservadora do Copywriter ajustada para **55–100 palavras**.
2. **Tema 2 (Upload e Idempotência):** Não existe garantia de `exactly-once` via Playwright. Todo upload exige `UploadAttempt` (estados padronizados: `INTENT`, `IN_PROGRESS`, `FAILED_PRE_UPLOAD`, `UNKNOWN`, `CONFIRMED`, `STALE_PUBLISHED_EVIDENCE`, `TERMINATED`), checkpoint local (`PUBLISHED_LOCAL`) e estado `UPLOAD_UNKNOWN` com bloqueio absoluto de retry automático. `CONFIRMED_ABSENT` **sozinho não libera retry**: exige também comprovação confiável de que a tentativa anterior encerrou/foi isolada. A reconciliação considera **todas as tentativas do job** e preserva evidências tardias.
3. **Tema 3 (Deduplicação):** Chave canônica `(channel_id, video_type, production_profile, scheduled_date_local, topic_hash)` com **Índice Único Parcial** (`WHERE (status IS NULL OR status NOT IN ('CANCELLED', 'ARCHIVED')) AND channel_id IS NOT NULL AND video_type IS NOT NULL AND production_profile IS NOT NULL AND scheduled_date_local IS NOT NULL AND topic_hash IS NOT NULL`), precedido por saneamento dos 5 campos históricos (sem inventar valores). Registros históricos incompletos nunca são considerados protegidos pela deduplicação. Jobs `PENDING` com `script_data` preenchido devem ser retomados/validados.
4. **Tema 4 (Máquina de Estados e Ownership):** **O sistema garante exclusividade de claim e de atualização de estado no Cloud Hub. A prevenção de publicações externas duplicadas depende também do bloqueio de novas tentativas, do controle de processos antigos e da reconciliação de resultados desconhecidos.** `claim` atômico transacional (SQLite e PostgreSQL), `heartbeat` validando conjuntamente `job_id`, `worker_id`, `attempt_id`, `fencing_token` (BIGINT monotônico) e estado (`409 Conflict` para obsoletos). **Render Lease** e **Upload Lease** possuem políticas independentes.

---

## 2. REGRAS OBRIGATÓRIAS

- Preservar integralmente a **Sprint 2.6** (`DirectorExecutionInterpreter`, `DirectorMediaRouter`, `DirectorCameraEngine`, `MasterPlanAdapter`, Universal Scene Contract V2, Integração Legacy, Hierarquia de mídia, Pipeline B).
- Proibido `worker_id = "unknown-worker"`.
- Proibido `RENDERING → PUBLISHED` sem `video_url` confirmada.
- Proibido `UPLOAD_UNKNOWN → UPLOADING` sem reconciliação prévia.
- Proibido liberar novo worker para upload quando uma tentativa anterior pode ter iniciado operação externa sem resultado confirmado (`UPLOAD_UNKNOWN`).
- Nunca interpretar falha de consulta (`CHECK_FAILED`) como ausência de publicação.
- Nunca presumir que `git revert` desfaz migrations ou publicações externas.

---

## 3. PENDÊNCIAS

- **Pendente (Calibração):** Medição empírica de amostras reais do `tts_engine.py` por perfil para substituir a hipótese de 12% de overhead.
- **Pendente (Decisão Humana):** Aprovação do plano de saneamento/arquivamento de duplicatas históricas em produção antes da criação do índice único parcial.
- **Pendente (Autorização):** Aprovação explícita do proprietário para iniciar a implementação (T1–T6).

---

## 4. RISCOS RESIDUAIS

- **Crash pré-checkpoint:** Queda do Desktop Node entre o clique de "Publicar" no YouTube Studio e a gravação do checkpoint local continuará exigindo reconciliação via `UPLOAD_UNKNOWN`.
- **Equivalência semântica:** O `topic_hash` (SHA-256) não detecta paráfrases semânticas sem embeddings/LLM.

---

## 5. DEPENDÊNCIAS E ORDEM DE IMPLEMENTAÇÃO (T1–T6)

1. **T1 — Perfis e validadores:** Sem dependências de banco.
2. **T5 — Política de créditos em shadow mode:** Passiva; sem bloqueio.
3. **T2 — Claim atômico, heartbeat, fencing e máquina de estados (Cloud Hub):** Base para T3.
4. **T3 — Upload durável, checkpoint e reconciliação:** Depende de T2.
5. **T4 — Deduplicação e recuperação do Scheduler:** Depende de T2 e saneamento histórico.
6. **T6 — Teste E2E em staging:** Depende de T1–T5 (obrigatoriamente com `uploader_dry_run=True`).

---

## 6. CRITÉRIOS DE ACEITE E INTERRUPÇÃO

- **Aceite:** Testes de concorrência (`409 Conflict`), heartbeat obsoleto, evidência tardia de worker antigo, `UPLOAD_UNKNOWN` bloqueando retry, isolamento por tenant e E2E staging (dry-run) passando 100%.
- **Interrupção:** Qualquer publicação duplicada em staging, falha de migration, `IntegrityError` não tratado no Scheduler ou regressão na Sprint 2.6.

---

## 7. PLANOS DE STAGING, PRODUÇÃO E ROLLBACK

- **Staging:** Executar ciclo completo `PENDING → PUBLISHED` com `uploader_dry_run=True`, simular crash de upload (`UPLOAD_UNKNOWN`) e corrida de dois workers (`409 Conflict`).
- **Produção (Etapas A–G):** A (Backup + auditoria) → B (Migration aditiva) → C (Cloud Hub) → D (Desktop Node) → E (Saneamento histórico) → F (Índice único parcial + dedup estrita) → G (Validação).
- **Rollback:** Reverter código mantendo colunas aditivas; pausar `upload_worker_loop` em anomalia de upload; remover apenas `uq_video_job_dedup` se bloquear o Scheduler.

---

## 8. ITENS QUE EXIGEM APROVAÇÃO EXPLÍCITA

1. Autorização formal para iniciar a implementação da Sprint 2.7 (T1 a T6).
2. Aprovação da estratégia de saneamento de duplicatas históricas (Etapa E).
3. Aprovação da execução do plano de staging com `uploader_dry_run=True`.

---

## 9. MATRIZ DE PRONTIDÃO (T1–T6)

| Tarefa | Status Documental | Prontidão Técnica | Dependência / Bloqueio | Autorização |
|---|---|---|---|---|
| **T1** — Perfis e validadores | Aprovada documentalmente | Pronta tecnicamente | Nenhuma | **Aguardando autorização** |
| **T2** — Claim, heartbeat e estados | Aprovada documentalmente | Pronta tecnicamente | Nenhuma | **Aguardando autorização** |
| **T3** — Upload durável e reconciliação | Aprovada documentalmente | Dependente | Depende de T2 | **Aguardando autorização** |
| **T4** — Deduplicação e Scheduler | Aprovada documentalmente | Dependente | Depende de T2 e saneamento histórico | **Aguardando autorização** |
| **T5** — Política de créditos (shadow) | Aprovada documentalmente | Pronta tecnicamente | Nenhuma | **Aguardando autorização** |
| **T6** — Teste E2E em staging (dry-run) | Aprovada documentalmente | Dependente | Depende de T1–T5 | **Aguardando autorização** |
