# SPRINT 2.7 — DEDUPLICAÇÃO, MÁQUINA DE ESTADOS E OWNERSHIP (ETAPA 0.6)

**Status:** Especificação Técnica Final (Somente Leitura / Documental — Corrigida na Etapa 0.6)  
**Escopo:** Temas 3 e 4 — `GlobalScheduler`, `VideoJob`, Cloud Hub Endpoints, Desktop Node.

---

## PARTE A — DEDUPLICAÇÃO DO GLOBALSCHEDULER (TEMA 3)

### 1. Chave Canônica de Deduplicação (PROPOSTA)

**Chave Canônica Definitiva:**
`(channel_id, video_type, production_profile, scheduled_date_local, topic_hash)`

- `channel_id`: Isola a produção por canal (tenant).
- `video_type` (`LONG` / `SHORT`): Distingue formatos estruturais.
- `production_profile` (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`): Permite versões de durações distintas sem colisão.
- `scheduled_date_local` (`YYYY-MM-DD` no timezone configurado do canal): Evita colisões perto da meia-noite UTC.
- `topic_hash` (SHA-256 do tópico normalizado): Identifica o assunto central.

### 2. Regras de Negócio e Hash do Tópico

1. **Perfis/formatos distintos** (`5 min` vs `10 min`, `SHORTS` vs `DOCUMENTARY`) sobre o mesmo tópico **não são duplicados**.
2. **Nova tentativa do mesmo job** opera sobre o mesmo `job_id` (incrementando `attempt_count`), sem inserir novo registro.
3. **Outro dia local** (`scheduled_date_local`) ou **outro canal** (`channel_id`) **é permitido**.
4. **Normalização textual vs. Equivalência semântica:**
   - A normalização (minúsculas, remoção de acentos NFKD, remoção de pontuação, colapso de espaços e `strip()`) seguida de **SHA-256** detecta apenas **equivalência textual normalizada**.
   - **Não detecta equivalência semântica.** Exemplo de colisão funcional não detectada: `"Como aumentar vendas de uma loja"` vs `"Estratégias para vender mais no varejo"` geram hashes distintos.
   - Não serão introduzidos embeddings ou LLM na deduplicação desta sprint.

### 3. Estratégia para `NULL` e Registros Históricos

> **[COMPROVADO — Comportamento SQL]** Índices `UNIQUE` comuns em **SQLite** e **PostgreSQL** tratam `NULL` como valores distintos, permitindo múltiplas linhas com `NULL` nas colunas indexadas. Portanto, colunas novas anuláveis **não** garantem unicidade para registros históricos incompletos.

#### 3.1. Comparação de Alternativas para `NULL`

| Critério | Alternativa A: Preenchimento + `NOT NULL` | Alternativa B: Índice Único Parcial (RECOMENDADA) | Alternativa C: Estratégia Híbrida |
|---|---|---|---|
| **Vantagens** | Garantia absoluta em todas as linhas | Compatível com colunas aditivas anuláveis; permite excluir `CANCELLED`/`ARCHIVED` | Transição gradual sem travar legado |
| **Desvantagens** | Exige backfill 100% antes de ativar; dificulta rollback | Linhas antigas com `NULL` ficam fora do índice até backfill | Complexidade dupla de regras no código |
| **Compatibilidade SQLite** | Total | Total (SQLite >= 3.8.0 suporta `CREATE UNIQUE INDEX ... WHERE`) | Total |
| **Compatibilidade PostgreSQL** | Total | Total (`CREATE UNIQUE INDEX ... WHERE`) | Total |
| **Registros antigos** | Precisam ser todos preenchidos antes | Ignorados pelo índice se `NULL`, protegidos após backfill | Ignorados inicialmente |
| **Risco de duplicidade** | Nulo após migration | Nulo para registros válidos; exige backfill para históricos | Médio durante transição |
| **Complexidade de migration** | Alta (`ALTER COLUMN SET NOT NULL`) | Baixa/Média (aditiva + `WHERE`) | Média |
| **Facilidade de rollback** | Difícil | Alta (`DROP INDEX`) | Média |

**Estratégia Principal Recomendada:** **Alternativa B — Índice Único Parcial com Tratamento Explícito de `status IS NULL`**, aplicando `UNIQUE INDEX uq_video_job_dedup` com cláusula `WHERE` exigindo que **todos os cinco campos da chave canônica estejam preenchidos (`IS NOT NULL`)**, excluindo apenas estados explicitamente descartados (`CANCELLED`, `ARCHIVED`) e **incluindo explicitamente `status IS NULL`** para impedir que qualquer registro elegível escape da deduplicação.

> **[COMPROVADO — Inspeção Somente Leitura]** Em `cloud_hub/models.py` (linha 63), `VideoJob.status: str = Field(default="PENDING", index=True)` gera `NOT NULL` em DDL novo via SQLModel, porém o repositório não possui migrations Alembic versionadas para garantir `NOT NULL` em bancos legados. Por segurança, o predicado do índice trata `status IS NULL` explicitamente, e a aplicação (`GlobalScheduler` / `cloud_hub`) deve normalizar `status IS NULL` para `'PENDING'` (ou rejeitar na validação estrita).

```sql
CREATE UNIQUE INDEX uq_video_job_dedup
ON video_jobs (channel_id, video_type, production_profile, scheduled_date_local, topic_hash)
WHERE (status IS NULL OR status NOT IN ('CANCELLED', 'ARCHIVED'))
  AND channel_id IS NOT NULL
  AND video_type IS NOT NULL
  AND production_profile IS NOT NULL
  AND scheduled_date_local IS NOT NULL
  AND topic_hash IS NOT NULL;
```

**Compatibilidade PostgreSQL vs. SQLite:**
- Ambos suportam índice único parcial (`CREATE UNIQUE INDEX ... WHERE ...`, SQLite >= 3.8.0 e PostgreSQL), porém **não possuem comportamento idêntico** em cláusulas `ON CONFLICT` e locks:
  - Em **PostgreSQL**, `ON CONFLICT (channel_id, video_type, production_profile, scheduled_date_local, topic_hash) WHERE (status IS NULL OR status NOT IN ('CANCELLED', 'ARCHIVED')) AND channel_id IS NOT NULL AND video_type IS NOT NULL AND production_profile IS NOT NULL AND scheduled_date_local IS NOT NULL AND topic_hash IS NOT NULL DO NOTHING` exige correspondência exata do predicado.
  - Em **SQLite**, a sintaxe `ON CONFLICT (...) WHERE ... DO NOTHING` também exige o mesmo predicado exato, mas o lock de escrita é global ao arquivo/transação (`BEGIN IMMEDIATE`).
  - Por portabilidade, o `GlobalScheduler` deve capturar `IntegrityError` na inserção/atualização em ambos os bancos além de usar verificação prévia.

**Tratamento de Novos Jobs com Chave Incompleta:**
- **Regra obrigatória:** Um job que ainda não possua **todos os cinco campos** (`channel_id`, `video_type`, `production_profile`, `scheduled_date_local`, `topic_hash`) **nunca** é considerado deduplicado por uma chave parcial/incompleta.
- **No fluxo de criação do `GlobalScheduler.run_daily_cycle()`:** Todos os cinco campos devem ser calculados/preenchidos antes do `INSERT`; se qualquer um estiver ausente, a criação pelo scheduler é **bloqueada** com erro de validação.
- **Em fluxos provisórios/manuais ou Legacy:** Se um job for criado em estado provisório (`PENDING`) com algum campo nulo (ex.: `production_profile IS NULL` ou `topic_hash IS NULL`), ele permanece **fora do índice parcial** até que os cinco campos sejam preenchidos. No momento em que o campo faltante for preenchido (antes de avançar para `APPROVED` / `READY_FOR_EDIT`), o sistema deve validar a unicidade e tratar eventual `IntegrityError` caso colida com job ativo existente.

#### 3.2. Estados Cancelados, Arquivados e Falhos

- **Participam da chave de deduplicação:** `PENDING`, `DRAFTED`, `APPROVED`, `READY_FOR_EDIT`, `RENDERING`, `PENDING_UPLOAD`, `UPLOADING`, `UPLOAD_UNKNOWN`, `PUBLISHED`, `FAILED`, `FAILED_QC`, `FAILED_UPLOAD` (desde que possuam os 5 campos não nulos). Isso impede que um job falho gere duplicidade involuntária em novos ciclos do Scheduler.
- **Excluídos do índice parcial:** Apenas `CANCELLED` e `ARCHIVED` (conforme cláusula `WHERE` acima), permitindo nova produção quando houver decisão legítima de recriação.

#### 3.3. Procedimento Obrigatório para Registros Históricos (Pré-Índice)

1. **Identificar** todos os registros históricos em `video_jobs`, separando os completos dos incompletos (qualquer um dos 5 campos `NULL`, incluindo `production_profile IS NULL` ou `topic_hash IS NULL`).
2. **Verificar** quais registros incompletos possuem dados fonte suficientes (`channel_id`, `video_type`, `production_profile`, `created_at`/`scheduled_at`, `topic`) para backfill.
3. **Calcular** `scheduled_date_local` e `topic_hash` ausentes quando possível (em script de saneamento); registros históricos que permanecerem incompletos ficam documentadamente fora do índice parcial.
4. **Identificar colisões** na chave canônica entre registros ativos/não-cancelados que possuam os 5 campos preenchidos.
5. **Classificar** registros duplicados (ex: publicado vs rascunho/falho).
6. **Definir preservação:** Manter o registro mais avançado (ex: `PUBLISHED` > `UPLOADING` > `PENDING`) ou mais recente.
7. **Definir arquivamento/exclusão:** Marcar excedentes como `ARCHIVED` **somente mediante classificação e aprovação explícita do responsável** (nunca arquivar ou excluir automaticamente apenas por compartilharem a mesma chave).
8. **Validar consistência** (query `GROUP BY` nos 5 campos com `HAVING COUNT(*) > 1` retornando 0 linhas para registros elegíveis) antes de criar o índice único.

### 4. Jobs `PENDING` com `script_data` Preexistente

- **[COMPROVADO]** Em `agents/orchestrator/global_scheduler.py` (linha 192), `process_pending_jobs()` executa `if not job.script_data:`, deixando jobs `PENDING` com `script_data` preenchido presos indefinidamente.
- **Comportamento Correto (PROPOSTA):** Jobs `PENDING` com `script_data` preenchido devem ser encaminhados ao `ContentValidator` para transição a `APPROVED` / `READY_FOR_EDIT`, sem regerar roteiro se válido.

---

## PARTE B — MÁQUINA DE ESTADOS, OWNERSHIP E CONSISTÊNCIA ATÔMICA (TEMA 4)

### 1. Estados e Transições

- **Estados Oficiais no Cloud Hub:** `PENDING`, `DRAFTED`, `APPROVED`, `READY_FOR_EDIT`, `RENDERING`, `PENDING_UPLOAD`, `UPLOADING`, `UPLOAD_UNKNOWN`, `FAILED`, `FAILED_QC`, `FAILED_UPLOAD`, `PUBLISHED`, `CANCELLED`, `ARCHIVED`.
- **Checkpoint Local:** `PUBLISHED_LOCAL` é marcador exclusivamente local do Desktop Node.
- **Transições Proibidas:** `RENDERING → PUBLISHED` sem `video_url`; `UPLOADING → PENDING_UPLOAD` automático após erro ambíguo; `UPLOAD_UNKNOWN → UPLOADING` sem reconciliação; `PUBLISHED → qualquer estado` por fluxo normal.

### 2. Claim Atômico (Especificação Transacional)

A operação `POST /api/worker/jobs/{job_id}/claim` deve executar **dentro de uma única transação** os 8 passos:
1. Verificar o estado atual (`READY_FOR_EDIT` para render, ou `PENDING_UPLOAD` para upload).
2. Verificar se existe lease ativa (`lease_expires_at IS NULL OR lease_expires_at < NOW()`).
3. Validar elegibilidade do job (não está em `UPLOAD_UNKNOWN`, `PUBLISHED`, `CANCELLED` ou `ARCHIVED`).
4. Atualizar `worker_id` para o novo proprietário.
5. Gerar novo `attempt_id` (UUID).
6. Incrementar `fencing_token = fencing_token + 1` atomicamente.
7. Atualizar `lease_expires_at = NOW() + lease_ttl` e `heartbeat_at = NOW()`.
8. Persistir a transição de estado (`RENDERING` ou `UPLOADING`).

**Diferenças SQLite vs. PostgreSQL:**
- **PostgreSQL:** Suporta `SELECT ... FOR UPDATE SKIP LOCKED` ou `UPDATE video_jobs SET ... WHERE id = :id AND status = :expected AND (lease_expires_at IS NULL OR lease_expires_at < NOW()) RETURNING ...`.
- **SQLite:** Não suporta `FOR UPDATE SKIP LOCKED`; exige transação `BEGIN IMMEDIATE` (lock de escrita) combinada com `UPDATE ... WHERE` condicional e verificação de `rowcount == 1` (retornando `409 Conflict` se `rowcount == 0`).

### 3. Heartbeat (Validação Conjunta)

`POST /api/worker/jobs/{job_id}/heartbeat` valida conjuntamente: `job_id`, `worker_id`, `attempt_id`, `fencing_token`, estado atual (`RENDERING` ou `UPLOADING`) e ownership atual.

| Situação | Comportamento do Cloud Hub |
|---|---|
| **Worker válido** (todos os campos conferem e lease ativa) | Estende `lease_expires_at` e atualiza `heartbeat_at`; retorna `200 OK` |
| **Worker obsoleto** (`fencing_token` ou `worker_id` divergente) | **Rejeita com `409 Conflict`**; worker obsoleto nunca renova lease |
| **Lease expirada** (se outro worker já assumiu) | **Rejeita com `409 Conflict`** |
| **Tentativa encerrada** (`attempt_id` não é mais o ativo) | **Rejeita com `409 Conflict`** |
| **Job já publicado** (`PUBLISHED`) | **Rejeita com `409 Conflict`** (informa estado final) |
| **Job em `UPLOAD_UNKNOWN`** | **Rejeita com `409 Conflict`** (bloqueado aguardando reconciliação) |

### 4. Fencing Token (Especificação)

- **Tipo de dado:** `BIGINT` (inteiro 64 bits com sinal).
- **Valor inicial:** `0` na criação do job; primeiro claim gera `1`.
- **Incremento:** Monotônico (`+1` atômico no banco a cada novo claim bem-sucedido).
- **Escopo:** Por `job_id`.
- **Persistência:** Armazenado em `video_jobs.fencing_token` e copiado para `upload_attempts.fencing_token`.
- **Validação:** Obrigatória em `heartbeat`, `status`, `confirm-upload` e qualquer transição crítica.
- **Overflow:** Em 64 bits (`9.22 × 10^18`), é praticamente inatingível; por segurança, rejeitar se atingir limite máximo do tipo.
- **Rollback/Recuperação:** O `fencing_token` **nunca** é decrementado em caso de rollback de tentativa ou recuperação de falha.
- **Limitação Externa:** **O sistema garante exclusividade de claim e de atualização de estado no Cloud Hub. A prevenção de publicações externas duplicadas depende também do bloqueio de novas tentativas, do controle de processos antigos e da reconciliação de resultados desconhecidos.** O `fencing_token` **não** cancela nem bloqueia uma operação Playwright já iniciada no YouTube Studio.

### 5. Separação Obrigatória: Render Lease vs. Upload Lease

Renderização local e publicação externa possuem riscos distintos e **não** podem compartilhar a mesma política de recuperação após expiração de lease.

#### A. Render Lease (Operação Local Reversível)

1. **Obtenção:** Worker solicita `claim` para job em `READY_FOR_EDIT`; Cloud Hub transiciona para `RENDERING`, gera `attempt_id`, incrementa `fencing_token` e define `lease_expires_at`.
2. **Heartbeat:** Worker envia `heartbeat` periódico validando `job_id`, `worker_id`, `attempt_id`, `fencing_token` e estado `RENDERING`.
3. **Expiração:** Cloud Hub identifica lease expirado quando `lease_expires_at < NOW()` (ou por timeout de monitoramento).
4. **Substituição:** Outro worker pode assumir a renderização via novo `claim` após expiração, recebendo novo `attempt_id` e `fencing_token` incrementado.
5. **Proteção contra resultado antigo:** Todo reporte de conclusão/artefato valida `attempt_id` + `fencing_token`; o worker antigo recebe `409 Conflict` e não pode sobrescrever o resultado da tentativa mais recente.
6. **Artefatos locais e temporários:** Diretórios/arquivos temporários de renderização devem ser isolados por `attempt_id` (ou limpos no início de nova tentativa/restart), impedindo que artefatos parciais de um worker antigo contaminem a tentativa ativa.

#### B. Upload Lease (Operação Externa Potencialmente Irreversível)

1. **Obtenção:** Worker solicita `claim` para job em `PENDING_UPLOAD`; Cloud Hub transiciona para `UPLOADING`, gera `attempt_id`, incrementa `fencing_token`, define `lease_expires_at` e registra `UploadAttempt(status=INTENT)`.
2. **Heartbeat:** Worker envia `heartbeat` periódico validando `job_id`, `worker_id`, `attempt_id`, `fencing_token` e estado `UPLOADING`.
3. **Perda de comunicação:** Cloud Hub identifica perda quando `lease_expires_at < NOW()`.
4. **Registro de início externo:** Antes de iniciar o Playwright no YouTube Studio, o worker marca `UploadAttempt(status=IN_PROGRESS)` com `upload_started_at`.
5. **Regra obrigatória de bloqueio:** **Lease de upload expirado não significa autorização automática para outro worker iniciar um novo upload.** Se o Playwright pode ter iniciado a operação externa (`upload_started_at IS NOT NULL` ou estado incerto), o job deve permanecer bloqueado em `UPLOAD_UNKNOWN` até reconciliação.
6. **Reconciliação:** Processo separado consulta o YouTube Studio (`FOUND_PUBLISHED`, `CONFIRMED_ABSENT`, `CHECK_FAILED`, `MULTIPLE_CANDIDATES`) inspecionando todas as tentativas do job.
7. **Evidência tardia:** Se o worker antigo concluir a publicação após perder o lease, registra `STALE_PUBLISHED_EVIDENCE` (com `video_url` e `attempt_id`) sem sobrescrever diretamente o estado do job; o Cloud Hub reconcilia a evidência antes de qualquer nova decisão.

#### C. Tabela Comparativa: Render Lease vs. Upload Lease

| Critério | Render Lease | Upload Lease |
|---|---|---|
| **Operação externa irreversível** | Não | Potencialmente sim (YouTube Studio) |
| **Recuperação automática após expiração** | Condicionada (novo claim permitido após expiração) | **Não**, se upload puder ter iniciado |
| **Novo worker pode assumir** | Após validação de expiração e novo `fencing_token` | **Somente após `CONFIRMED_ABSENT` + tentativa anterior comprovadamente encerrada/isolada** |
| **Fencing token** | Obrigatório | Obrigatório (protege Cloud Hub, não cancela Playwright) |
| **Evidência tardia** | Resultado de renderização (descartado com `409`) | Possível publicação externa (`STALE_PUBLISHED_EVIDENCE`) |
| **Estado de incerteza** | Recovery controlado (`READY_FOR_EDIT` / novo claim) | `UPLOAD_UNKNOWN` (bloqueio total de novo upload) |

### 6. Confirmação de Upload e Evidência Tardia

`POST /api/worker/jobs/{job_id}/confirm-upload` valida: `job_id`, `attempt_id`, `worker_id`, `fencing_token`, estado esperado (`UPLOADING`), `video_url` válida, `channel_id` correspondente e integridade da tentativa.
- **Regra para worker antigo com publicação tardia:** Se um worker antigo descobrir que publicou o vídeo após perder ownership (recebendo `409 Conflict` no `confirm-upload`), ele **não** pode atualizar o job diretamente com seu token antigo, mas deve registrar a evidência (`video_url` + `attempt_id`) no endpoint de evidência tardia para que o Cloud Hub bloqueie o novo worker e encaminhe para reconciliação autorizada.

### 6. Especificação de Testes Futuros (Não Implementados Nesta Etapa)

1. Dois workers tentando `claim` simultaneamente no mesmo job (apenas 1 vence; outro recebe `409`).
2. `Heartbeat` com `fencing_token` antigo (rejeitado com `409`).
3. Atualização de status após expiração de lease e novo claim (rejeitada com `409`).
4. Worker antigo tentando `confirm-upload` após perda de ownership (rejeitado com `409`).
5. Worker novo assumindo job após expiração de lease do worker anterior.
6. Tentativa anterior publicando no YouTube após perda de ownership (preservação da evidência sem perda de `video_url` e bloqueio de duplicidade).
7. Job em `UPLOAD_UNKNOWN` bloqueando `claim`, `heartbeat` e retry automático.
8. Dois workers operando em canais diferentes simultaneamente (sem interferência).
9. Isolamento por tenant (`channel_id` divergente rejeitado).
