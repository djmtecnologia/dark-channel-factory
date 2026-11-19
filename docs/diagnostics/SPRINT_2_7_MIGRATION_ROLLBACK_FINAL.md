# SPRINT 2.7 — SEQUÊNCIA DEFINITIVA DE MIGRATION, DEPLOY E ROLLBACK (ETAPA 0.6)

**Status:** Especificação Técnica Final (Somente Leitura / Documental — Corrigida na Etapa 0.6)  
**Escopo:** Tema 5 — Consolidação de sequência de implantação, compatibilidade e rollback.

---

## 1. SEQUÊNCIA DEFINITIVA DE IMPLANTAÇÃO (ETAPAS A–G)

As etapas abaixo **não precisam** ser executadas em um único deploy; a separação reduz risco e garante compatibilidade reversa entre Cloud Hub, Banco e Desktop Node.

### Etapa A — Preparação (Somente Leitura / Offline)
- Backup completo do banco (PostgreSQL/Neon ou SQLite) e **teste de restauração** comprovado.
- Auditoria somente leitura de duplicidades em `video_jobs`.
- Identificação de registros históricos incompletos (qualquer um dos 5 campos da chave canônica ausente: `channel_id`, `video_type`, `production_profile`, `scheduled_date_local` ou `topic_hash`).
- Aprovação explícita do plano de saneamento pelo proprietário.

### Etapa B — Migration Aditiva
- Adicionar colunas novas **anuláveis** em `video_jobs` (`worker_id`, `attempt_id`, `fencing_token`, `lease_expires_at`, `heartbeat_at`, `topic_hash`, `scheduled_date_local`, `video_hash`, `duration_s`).
- Criar tabela `upload_attempts` e índices auxiliares **não exclusivos** (ex: por `job_id`, `status`).
- **Justificativa:** Versões antigas da aplicação continuam operando normalmente ignorando as colunas extras.

### Etapa C — Deploy do Cloud Hub
- Deploy do Cloud Hub com novos contratos (`claim`, `heartbeat`, `status`, `confirm-upload`, `reconcile`, evidência tardia).
- Manter compatibilidade temporária com clientes antigos (se necessário) durante a janela de transição, rejeitando `worker_id = "unknown-worker"`.
- Ativar validação estrita de `tenant` (`channel_id`) e `ownership`.

### Etapa D — Deploy do Desktop Node
- Deploy do novo worker com `worker_id` único e persistente.
- Ativar loop de `heartbeat`, `fencing_token`, registro de `UploadAttempt`, checkpoint local (`PUBLISHED_LOCAL`) e bloqueio de retry ambíguo (`UPLOAD_UNKNOWN`).
- **Justificativa:** O Desktop Node só pode ser atualizado **após** o Cloud Hub expor os novos endpoints.

### Etapa E — Saneamento de Registros Históricos
- Executar script aprovado seguindo a **Matriz de Saneamento Histórico** abaixo, preenchendo **apenas** os campos com fonte confiável (nunca inventar valores, inferir perfil/canal sem evidência, converter UTC sem timezone do canal ou calcular `topic_hash` sem tópico confiável):

| Campo | Fonte Confiável | Estratégia de Preenchimento | Se Não Houver Fonte |
|---|---|---|---|
| `channel_id` | FK existente em `video_jobs.channel_id` (ou relação com `Channel`) | Manter/validar FK existente | Permanecer incompleto (fora do índice) |
| `video_type` | `video_jobs.video_type` ou `production_profile` mapeado | Copiar valor válido (`LONG`/`SHORT`) | Permanecer incompleto (fora do índice) |
| `production_profile` | `video_jobs.production_profile` ou metadado explícito do roteiro/job | Copiar perfil explícito válido | Permanecer incompleto (fora do índice) |
| `scheduled_date_local` | `scheduled_at` (ou `created_at`) + `timezone` configurado do canal | Converter para `YYYY-MM-DD` no timezone do canal | Permanecer incompleto (fora do índice) |
| `topic_hash` | `video_jobs.topic` (ou título/tópico explícito em `script_data`) | Normalizar texto + SHA-256 | Permanecer incompleto (fora do índice) |

- **Regra Obrigatória:** **Registros históricos incompletos não podem ser apresentados como protegidos pela deduplicação.** Eles devem constar em relatório separado (quantidade, motivo da incompletude e risco residual). A criação do índice não depende de preencher artificialmente todos os registros históricos.
- Arquivar (`ARCHIVED`) duplicatas excedentes entre registros elegíveis **somente mediante aprovação explícita do proprietário**.
- Validar ausência total de colisões entre registros elegíveis (`GROUP BY` com `HAVING COUNT(*) > 1` retornando 0 linhas) antes da Etapa F.

### Etapa F — Ativação da Deduplicação Estrita
- Criar índice único parcial aprovado (`uq_video_job_dedup` com `WHERE (status IS NULL OR status NOT IN ('CANCELLED', 'ARCHIVED')) AND channel_id IS NOT NULL AND video_type IS NOT NULL AND production_profile IS NOT NULL AND scheduled_date_local IS NOT NULL AND topic_hash IS NOT NULL`).
- Ativar inserção atômica (`ON CONFLICT DO NOTHING` / tratamento de `IntegrityError`) no `GlobalScheduler`.
- Monitorar logs de `IntegrityError` e colisões.
- **Justificativa:** Criar o índice único *antes* do saneamento (Etapa E) causaria falha imediata da migration.

### Etapa G — Validação Final
- Executar testes de concorrência, recuperação de lease, `UPLOAD_UNKNOWN`, E2E em staging e **uploader dry-run** (sem publicação real).

---

## 2. MATRIZ DE ROLLBACK POR ETAPA

> **Regra Absoluta:** `git revert` **não** desfaz migrations já aplicadas no banco e **não** desfaz vídeos já publicados no YouTube.

| Etapa | Sinais de Falha / Critério de Interrupção | Ação Imediata | Versão Compatível | Impacto no Banco | Jobs em Andamento & `UploadAttempt` | Vídeos Publicados & Condição de Retomada |
|---|---|---|---|---|---|---|
| **A (Preparação)** | Falha no backup ou divergência na auditoria | Abortar antes de qualquer alteração | Atual | Nenhum | Sem impacto | N/A; retomar após backup íntegro |
| **B (Migration Aditiva)** | Erro de DDL ou lock prolongado | Reverter transaction / restaurar backup se necessário | Atual | Colunas aditivas (sem `DROP COLUMN` em emergência) | Sem impacto | N/A; retomar após corrigir DDL em staging |
| **C (Cloud Hub)** | Erro 5xx, falha de auth/tenant ou `409` em workers saudáveis | `git revert` / rollback do deploy do Cloud Hub | Anterior (tolera colunas aditivas) | Mantém schema aditivo | Jobs em `RENDERING`/`UPLOADING` aguardam timeout ou retomada | Vídeos já publicados permanecem `PUBLISHED`; retomar após corrigir contrato |
| **D (Desktop Node)** | Crash no heartbeat, falha ao gravar `UploadAttempt` ou retry indevido | Pausar `upload_worker_loop` e reverter Desktop Node | Anterior (se Hub mantiver compatibilidade) | Mantém schema | Tentativas em `UPLOADING` vão para `UPLOAD_UNKNOWN` | **Nunca** republicar; reconciliar `UPLOAD_UNKNOWN` antes de retomar |
| **E (Saneamento)** | Backfill incorreto ou marcação indevida | Interromper script; restaurar snapshot pré-Etapa E | Atual | Reverter apenas registros afetados via backup | Sem impacto em uploads | N/A; validar script em staging antes de retomar |
| **F (Dedup Estrita)** | `IntegrityError` não tratado derrubando `GlobalScheduler` | `DROP INDEX uq_video_job_dedup` + desativar flag estrita | Atual (com flag desligada) | Remove apenas o índice único parcial | Jobs ativos continuam normalmente | N/A; corrigir tratamento de exceção antes de recriar índice |
| **G (Validação)** | Qualquer publicação duplicada ou falha E2E em staging | Bloquear promoção para produção | Staging isolado | Nenhum em produção | Limpar ambiente de staging | N/A; corrigir defeito e reexecutar Etapa G |
