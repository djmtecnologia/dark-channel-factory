# SPRINT 2.7 — TASK 4: HOMOLOGAÇÃO (OUTBOX DURÁVEL)

**Status:** HOMOLOGADA (PASS — Camada Local Isolada; Integração Real com Cloud Hub Documentada como Lacuna Pendente)  
**Data:** 2026-11-19  
**Escopo:** Implementação exclusiva da Outbox Durável (`desktop_worker/watchdog/outbox.py`) sobre o SQLite do State Store da Task 3, preservando integralmente as Tasks 1, 2 e 3 e sem iniciar a Task 5 ou o daemon completo do Watchdog.

---

## 1. Diagnóstico Inicial (Auditoria Pré-Implementação)

- **State Store (Task 3 — Baseline Protegida):** `desktop_worker/watchdog/state_store.py` utiliza SQLite em modo `WAL` com `synchronous = FULL`, `foreign_keys = ON`, `busy_timeout = 5000`, `isolation_level = None`, `threading.RLock()`, transações explícitas (`BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`) e `SCHEMA_VERSION = 1`.
- **Contratos existentes no Cloud Hub (`cloud_hub/main.py` e `desktop_worker/main.py`):**
  - `GET /worker/next-job/{market_region}`
  - `POST /worker/report-status/{job_id}?status=...&error=...` (retorna `{"status": "ok"}`, HTTP 404 se job não existir).
- **Lacunas identificadas no Cloud Hub (não existentes no código atual):**
  1. Não há endpoint dedicado para ingestão de eventos do Watchdog.
  2. Não há autenticação/autorização de worker (headers de API key/JWT).
  3. Não há suporte a `Idempotency-Key` / deduplicação por `event_id`.
  4. Não há contrato estruturado de ACK vinculando `event_id` na resposta.
  5. Não há semântica formal de rejeição permanente vs. falha temporária nem validação de `fencing_token` no Hub.

---

## 2. Arquitetura Implementada

- Criado `desktop_worker/watchdog/outbox.py` (`OutboxStore`), reutilizando diretamente a conexão SQLite, o lock (`_lock`) e as transações (`_transaction()`) do `StateStore`, sem criar uma segunda conexão SQLite independente e sem alterar `state_store.py`.
- Suporte opcional ao parâmetro `conn` em `enqueue_event(...)` para permitir que uma operação local de estado de negócio e o enfileiramento na Outbox ocorram na **mesma transação SQLite** (sem criar falsa atomicidade entre SQLite e Cloud Hub).
- Nenhuma transação SQLite é mantida aberta durante chamadas externas (`claim_event` commita antes de retornar; `deliver_claimed_event` executa o transporte fora de transação).

---

## 3. Modelo da Tabela (`outbox_event`) e Índices

| Coluna | Tipo / Constraint | Finalidade |
|---|---|---|
| `event_id` | `TEXT PRIMARY KEY` | Identificador único e estável do evento (também usado como `idempotency_key`). |
| `event_type` | `TEXT NOT NULL` | Tipo do evento do Watchdog. |
| `execution_id` | `TEXT` | Execução relacionada, quando aplicável. |
| `attempt_id` | `TEXT` | Tentativa relacionada, quando aplicável. |
| `job_id` | `TEXT` | Job relacionado, quando aplicável. |
| `worker_id` | `TEXT NOT NULL` | Worker de origem. |
| `fencing_token` | `INTEGER CHECK (fencing_token IS NULL OR fencing_token >= 0)` | Token de fencing associado. |
| `payload_json` | `TEXT NOT NULL` | Payload JSON canônico (`sort_keys=True`, objeto JSON validado). |
| `created_at_utc` | `TEXT NOT NULL` | Timestamp UTC de criação. |
| `available_at_utc` | `TEXT NOT NULL` | Próximo instante UTC permitido para entrega. |
| `attempt_count` | `INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)` | Contador de tentativas de entrega. |
| `last_attempt_at_utc` | `TEXT` | Timestamp UTC da última tentativa. |
| `delivered_at_utc` | `TEXT` | Timestamp UTC da confirmação (ACK). |
| `last_error` | `TEXT` | Diagnóstico da última falha. |
| `status` | `TEXT NOT NULL` | Estado de entrega (`PENDING`, `IN_FLIGHT`, `DELIVERED`, `RETRY`, `DEAD_LETTER`). |
| `lease_owner` | `TEXT` | Identificador do proprietário atual do lease. |
| `lease_expires_at_utc` | `TEXT` | Instante UTC de expiração do lease. |
| `ack_id` | `TEXT` | Recibo/identificador opcional retornado pelo Cloud Hub no ACK. |
| `schema_version` | `INTEGER NOT NULL` | Versão do registro (`OUTBOX_SCHEMA_VERSION = 1`). |
| `checksum_sha256` | `TEXT NOT NULL` | SHA-256 canônico de integridade do registro. |

**Índices criados:**
- `idx_outbox_status_available` em `(status, available_at_utc)`
- `idx_outbox_inflight_lease` em `(status, lease_expires_at_utc)`
- `idx_outbox_exec_attempt` em `(execution_id, attempt_id)`
- `idx_outbox_delivered` em `(status, delivered_at_utc)`

---

## 4. Estados e Transições Válidas

- `PENDING` → `IN_FLIGHT`, `DEAD_LETTER`
- `IN_FLIGHT` → `DELIVERED`, `RETRY`, `DEAD_LETTER`, `PENDING` (exclusivamente via recuperação de lease expirado quando `attempt_count == 0`)
- `RETRY` → `IN_FLIGHT`, `DEAD_LETTER`
- `DELIVERED` → terminal (ACK repetido para o mesmo `event_id` é idempotente; qualquer tentativa de transição para outro estado lança `OutboxStateTransitionError`)
- `DEAD_LETTER` → terminal (nunca tratado como entregue; payload e histórico preservados)

---

## 5. Política de Lease, Retry/Backoff, Idempotência e Contrato de ACK

- **Lease:** `claim_event(lease_owner, lease_duration_seconds=30.0, ...)` realiza reserva atômica dentro de `BEGIN IMMEDIATE ... COMMIT`, impedindo que dois workers reservem o mesmo evento simultaneamente. Eventos `IN_FLIGHT` cujo `lease_expires_at_utc <= now_utc` são recuperados por `recover_expired_leases()`.
- **Retry e Backoff (`RetryPolicy`):**
  - Padrão configurável: `max_attempts=5`, `base_delay_seconds=2.0`, `max_delay_seconds=300.0`, `jitter_ratio=0.2`.
  - Fórmula: `min(base_delay * 2^(attempt_count - 1), max_delay)` com jitter simétrico limitado a `[0, max_delay]`.
  - Ao atingir `max_attempts`, o evento transita automaticamente para `DEAD_LETTER`.
- **Idempotência (at-least-once):**
  - `enqueue_event` com mesmo `event_id` e conteúdo semântico idêntico retorna o registro existente; conteúdo divergente lança `OutboxConflictError`.
  - `deliver_claimed_event` sempre envia `idempotency_key=event.event_id`.
  - `mark_delivered` é idempotente para ACKs repetidos de eventos já `DELIVERED`.
- **Contrato mínimo de ACK (`HubAckResponse` / `OutboxTransport`):**
  - Somente `accepted is True` com `ack.event_id == event_id` confirma `DELIVERED`.
  - ACK de outro evento, `accepted=False`, ou `None` lançam `OutboxAckError` (nunca confirmam entrega).
  - HTTP `4xx` (exceto `429`) ou `permanent_rejection=True` direcionam para `DEAD_LETTER`.
  - Timeout, erro de rede, HTTP `429` ou `5xx` direcionam para `RETRY`.

---

## 6. Migração de Schema e Estratégia de Recuperação

- `state_store.py` (`SCHEMA_VERSION = 1`) foi mantido intacto como baseline protegida.
- `OutboxStore._ensure_schema()` executa migração explícita e transacional criando `outbox_event` e seus 4 índices, registrando `outbox_schema_version = 1` em `schema_metadata` e preservando todos os dados das tabelas da Task 3.
- Valores incompatíveis de `outbox_schema_version` lançam `OutboxSchemaError`.
- Recuperação pós-reinicialização:
  - Eventos `PENDING` e `RETRY` são recuperados via `get_pending_events()`.
  - Eventos `IN_FLIGHT` abandonados são recuperados via `recover_expired_leases()`.
  - Corrupção de checksum/schema é detectada em leitura (`OutboxCorruptionError`) e em varredura global (`verify_integrity()` -> `IntegrityStatus.INCONCLUSIVE`).

---

## 7. Testes e Resultados (Ambiente Windows — Python 3.13.7)

### Testes Executados (Reais em SQLite Temporário + Transporte Mockado)
1. **Task 4 (`pytest tests/unit/test_watchdog_outbox.py tests/integration/test_watchdog_outbox_integration.py -v`):**
   - `tests/unit/test_watchdog_outbox.py`: **14 passed, 0 failed, 0 skipped**
   - `tests/integration/test_watchdog_outbox_integration.py`: **4 passed, 0 failed, 0 skipped**
   - **Total Task 4:** **18 passed in 0.93s**
2. **Regressão Task 3 (`pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v`):**
   - **22 passed, 0 failed, 0 skipped** (0.86s)
3. **Regressão Tasks 1 e 2 no Windows (`pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v`):**
   - **69 passed, 0 failed, 0 skipped** (1.01s)

### Testes Simulados
- Toda a comunicação com o Cloud Hub (ACK válido, ACK repetido, ACK divergente, resposta ambígua, timeout, queda de rede, HTTP 503/429/400 e deduplicação remota por `idempotency_key`) foi validada com **mocks** (`_MockTransport` e `_IdempotentHubMock`).

### Testes Não Executados / Integrações Reais Não Homologadas
- **Não executados:** Testes contra endpoint HTTP real do Cloud Hub ou banco de produção (proibidos nesta etapa e bloqueados pela inexistência do contrato no Cloud Hub).
- **Integração real não homologada:** Entrega ponta-a-ponta para o Cloud Hub permanece bloqueada até que o Cloud Hub implemente endpoint de eventos do Watchdog com autenticação, `Idempotency-Key` por `event_id`, validação de `fencing_token` e resposta estruturada de ACK.

---

## 8. Limitações Conhecidas e Contratos Pendentes no Cloud Hub

- O Cloud Hub atual não oferece deduplicação por `event_id` nem contrato de ACK estruturado; portanto, a entrega real para produção **não pode ser declarada segura** até a implementação desse contrato no servidor.
- O daemon completo do Watchdog (Task 5), reconciliação YouTube e fluxo de upload não fazem parte desta entrega.

---

## 9. Decisão Final

**PASS** — Task 4 homologada no escopo autorizado (18/18 testes Task 4 + 22/22 regressão Task 3 + 69/69 regressão Tasks 1 e 2 no Windows; lacuna do contrato real do Cloud Hub documentada).

---

## 10. Rodada Corretiva — Lease Fencing e Crash Recovery

**Data:** 2026-11-19  
**Decisão da Rodada Corretiva:** **PASS** (28/28 testes Task 4 + 22/22 regressão Task 3 + 69/69 regressão Tasks 1 e 2 no Windows; arquivos protegidos das Tasks 1, 2 e 3 intactos).

### 10.1 Achados da Auditoria e Causa Raiz

1. **Achado Crítico 1 — Ausência de identidade única de lease (`lease_id`):**
   - **Causa raiz:** `outbox_event` armazenava apenas `lease_owner` e `fencing_token`. Duas reservas sucessivas do mesmo worker não podiam ser distinguidas.
2. **Achado Crítico 2 — Parâmetros de posse opcionais em transições `IN_FLIGHT`:**
   - **Causa raiz:** `mark_delivered`, `mark_retry` e `mark_dead_letter` aceitavam `lease_owner=None` e não validavam `lease_id` nem expiração do lease dentro da transação `BEGIN IMMEDIATE`.
3. **Achado Crítico 3 — ACK tardio de worker antigo:**
   - **Causa raiz:** Sem `lease_id` obrigatório e sem verificação de expiração/posse na transação de confirmação, um ACK atrasado de uma reserva expirada podia sobrescrever o estado de uma reserva ativa mais recente (inclusive do mesmo worker).
4. **Achado Crítico 4 — Claims abandonados não contabilizados em `max_attempts`:**
   - **Causa raiz:** `attempt_count` só era incrementado em `mark_retry` / `mark_dead_letter`. Crashes repetidos durante `IN_FLIGHT` permitiam recuperação infinita via `recover_expired_leases()` sem nunca atingir `DEAD_LETTER`.

### 10.2 Tabela Comparativa — Antes vs. Corrigido

| Requisito | Implementação Anterior (v1) | Implementação Corrigida (v2) |
|---|---|---|
| Identidade do lease | Apenas `lease_owner` | `lease_id` (UUID único por `claim_event`), persistido no SQLite e incluído no checksum |
| Posse em `mark_delivered` / `mark_retry` / `mark_dead_letter` | `lease_owner` opcional (`None` ignorava validação) | `lease_owner` e `lease_id` obrigatórios (nunca aceita `None`/vazio); validação atômica em `BEGIN IMMEDIATE` |
| Validação de expiração do lease | Não validada nas mutações | Rejeita qualquer mutação se `lease_expires_at_utc <= now_utc` |
| Validação de `fencing_token` | Ignorada se `expected_fencing_token=None` | Estrita: obrigatória e exata quando o evento possui token; explícita (`None` obrigatório) quando não possui |
| ACK tardio (outro worker ou mesmo worker) | Podia alterar evento ou confundir reservas | Rejeitado com `OutboxConflictError`; estado e checksum no SQLite permanecem intactos |
| Contabilização de claims abandonados | `attempt_count` não incrementava na recuperação | `claim_count` contabiliza reservas iniciadas; `recover_expired_leases()` incrementa `attempt_count` e move para `DEAD_LETTER` ao atingir `max_attempts` |
| Registros corrompidos na recuperação | Abortava recuperação | Ignorados pela recuperação automática, preservados como evidência e classificados como `INCONCLUSIVE` |
| Schema da Outbox | `outbox_schema_version = 1` | `outbox_schema_version = 2` com migração transacional explícita v1 → v2 (`SCHEMA_VERSION` do `StateStore` intacto em `1`) |

### 10.3 Arquivos Alterados

- `desktop_worker/watchdog/outbox.py`
- `tests/unit/test_watchdog_outbox.py`
- `tests/integration/test_watchdog_outbox_integration.py`
- `docs/sprints/SPRINT_2_7_TASK_4_HOMOLOGATION.md`

Arquivos protegidos (`state_store.py`, `job_object.py`, `identity.py`, `process_launcher.py` e testes homologados das Tasks 1, 2 e 3) **não foram modificados**.

### 10.4 Migração do Schema (v1 → v2)

- `OUTBOX_SCHEMA_VERSION` atualizado de `1` para `2` em `schema_metadata` (`key = 'outbox_schema_version'`), sem alterar `SCHEMA_VERSION = 1` do `StateStore`.
- `_migrate_v1_to_v2()` executa dentro de `BEGIN IMMEDIATE ... COMMIT`:
  - Adiciona `lease_id TEXT` e `claim_count INTEGER NOT NULL DEFAULT 0 CHECK (claim_count >= 0)`.
  - Valida o checksum v1 de cada registro existente; registros íntegros são atualizados para `schema_version = 2` com preservação de payload, histórico, ACKs e `lease_id = None` para eventos históricos sem lease, recalculando o checksum canônico v2.
  - Registros v1 corrompidos são preservados intactos (sem mascarar corrupção) para continuarem classificados como `INCONCLUSIVE`.
  - Reexecução é idempotente; falha em qualquer etapa provoca rollback completo.

### 10.5 Novo Ciclo de Vida do Lease e Semântica dos Contadores

- **Ciclo de vida:** `PENDING`/`RETRY` → `claim_event()` gera novo `lease_id` (UUID), incrementa `claim_count`, define `lease_owner` e `lease_expires_at_utc` → `IN_FLIGHT` → mutação (`mark_delivered` / `mark_retry` / `mark_dead_letter`) exige posse válida (`event_id`, `lease_owner`, `lease_id`, lease não expirado, `expected_fencing_token` conforme regra estrita e checksum válido) dentro da mesma transação `BEGIN IMMEDIATE` → ao concluir ou recuperar, `lease_id`, `lease_owner` e `lease_expires_at_utc` são limpos (`None`).
- **Contadores:**
  - `claim_count`: número total de reservas `IN_FLIGHT` iniciadas de forma durável.
  - `attempt_count`: número de tentativas consideradas para `RetryPolicy.max_attempts`, somando falhas explícitas (`mark_retry` / `mark_dead_letter`) e reservas abandonadas recuperadas por `recover_expired_leases()`.
- **Garantia de entrega:** **at-least-once** (nunca exactly-once). Em crash após envio ao Hub e antes do ACK local, o resultado externo é desconhecido; o evento é reentregue com `idempotency_key = event_id` dependendo de deduplicação no Cloud Hub.

### 10.6 Proteção contra ACK Tardio

- Tanto ACKs tardios de outro worker (`worker-A` vs. `worker-B`) quanto ACKs tardios do **mesmo worker** com reserva anterior (`lease_id_1` vs. `lease_id_2`) são rejeitados com `OutboxConflictError`, sem modificar status, `lease_owner`, `lease_id`, `last_error` ou `checksum_sha256` no SQLite.

### 10.7 Testes Executados (Windows — Python 3.13.7)

| Escopo | Comando | Executados | Passados | Falhos | Ignorados | Duração |
|---|---|---:|---:|---:|---:|---:|
| Task 4 (unit + integration) | `pytest tests/unit/test_watchdog_outbox.py tests/integration/test_watchdog_outbox_integration.py -v` | 28 | 28 | 0 | 0 | 1.75s |
| Task 3 (regressão) | `pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v` | 22 | 22 | 0 | 0 | 1.18s |
| Tasks 1 e 2 (regressão Windows) | `pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v` | 69 | 69 | 0 | 0 | 1.04s |

- **Novos testes adicionados (+10):** cobrem todos os 29 cenários obrigatórios (identidade única de `lease_id`, rejeição de `None`/owner/ID divergente/lease expirado sem mutação no SQLite, ACK tardio entre workers e mesmo worker, fencing estrito para eventos com e sem token, limite `max_attempts` para claims abandonados repetidamente, `DEAD_LETTER` não reenviado, checksum incluindo `lease_id`, detecção de adulteração manual de `lease_id`, corrompidos não recuperados automaticamente, migração v1→v2 preservando dados, idempotência e rollback em falha).

### 10.8 Riscos Remanescentes e Pendência de Integração Real com Cloud Hub

- **Clock skew local:** a expiração do lease depende do relógio UTC local (`utc_now()`); saltos abruptos de relógio podem antecipar ou atrasar a expiração do lease.
- **Integração real com Cloud Hub (pendente):** o Cloud Hub ainda não implementa endpoint de ingestão de eventos do Watchdog com autenticação, deduplicação idempotente por `event_id` e ACK estruturado (`HubAckResponse`). Nenhum upload ou publicação real foi executado.


---

## 11. Rodada Corretiva Final — Schema Consistency e Corruption Isolation

**Decisão Final:** **PASS FINAL — CAMADA LOCAL**

### 11.1 Problemas Encontrados e Causa Raiz

1. **Detecção de Schema Inconsistente (`_ensure_schema`):**
   - **Problema:** Quando `outbox_schema_version` não existia em `schema_metadata`, `_ensure_schema()` assumia banco novo, executava `CREATE TABLE IF NOT EXISTS` e inseria `outbox_schema_version = 2` mesmo que já existisse uma tabela `outbox_event` sem metadado ou com estrutura incompatível. Além disso, bancos com `outbox_schema_version = 2` não validavam a presença efetiva de todas as colunas e índices obrigatórios.
   - **Causa raiz:** Ausência de inspeção explícita via `sqlite_master` e `PRAGMA table_info(outbox_event)` para distinguir os cenários A (novo), B (legado v1), C (atual v2) e D (inconsistente).
2. **Bloqueio de Fila por Eventos Corrompidos (`claim_event`):**
   - **Problema:** `claim_event()` selecionava apenas o primeiro candidato elegível (`LIMIT 1`) e validava seu checksum; se o registro estivesse corrompido, levantava `OutboxCorruptionError` e impedia a reserva e entrega de eventos íntegros posteriores na fila.
   - **Causa raiz:** Seleção restrita a `LIMIT 1` sem isolamento de registros corrompidos durante a varredura determinística de candidatos elegíveis.

### 11.2 Solução Aplicada

- **Schema Consistency (`desktop_worker/watchdog/outbox.py`):**
  - `_ensure_schema()` executa na transação explícita `BEGIN IMMEDIATE` do `StateStore` (sem `executescript()`), inspecionando `sqlite_master` (tabela e índices) e `PRAGMA table_info(outbox_event)` para distinguir explicitamente:
    - **Cenário A (Banco novo):** sem `outbox_event` e sem `outbox_schema_version` → cria tabela v2, índices obrigatórios e registra `outbox_schema_version = 2` atomicamente.
    - **Cenário B (Banco legado v1):** `outbox_event` com colunas v1 obrigatórias e `outbox_schema_version = 1` → executa `_migrate_v1_to_v2()` (incluindo criação idempotente dos índices obrigatórios), valida schema v2 resultante e mantém rollback integral em caso de falha.
    - **Cenário C (Banco atual v2):** `outbox_event` e `outbox_schema_version = 2` → valida todas as colunas (`_OUTBOX_V2_REQUIRED_COLUMNS`) e índices obrigatórios (`_OUTBOX_REQUIRED_INDEXES`) sem executar migração desnecessária.
    - **Cenário D (Banco inconsistente):** tabela sem metadado, metadado sem tabela, versão inválida/incompatível, ou coluna/índice obrigatório ausente → interrompe a inicialização com `OutboxSchemaError` sem alterar metadados, estrutura ou dados originais.
- **Corruption Isolation (`desktop_worker/watchdog/outbox.py`):**
  - `claim_event()` mantém proteção `BEGIN IMMEDIATE`, consulta candidatos elegíveis em ordem determinística (`available_at_utc ASC, created_at_utc ASC, event_id ASC`) e valida integridade de cada candidato antes da reserva.
  - Registros corrompidos são logados com classificação `INCONCLUSIVE`, preservados byte a byte no SQLite (sem alteração de payload, status ou recálculo de checksum) e pulados para permitir a reserva atômica do próximo evento íntegro.
  - Quando todos os candidatos estão corrompidos (ou em `claim_event(..., event_id=corrupt_id)`), retorna `None` sem entrega indevida. `verify_integrity()` continua classificando registros corrompidos como `INCONCLUSIVE`.

### 11.3 Arquivos Alterados

- `desktop_worker/watchdog/outbox.py`
- `tests/unit/test_watchdog_outbox.py`
- `docs/sprints/SPRINT_2_7_TASK_4_HOMOLOGATION.md`

Arquivos protegidos (`state_store.py`, `job_object.py`, `identity.py`, `process_launcher.py` e testes homologados das Tasks 1, 2 e 3) **permaneceram inalterados** (confirmado via `git status`).

### 11.4 Testes Adicionados (+9 novos testes em `tests/unit/test_watchdog_outbox.py`)

- **Schema:**
  - `test_schema_new_db_creates_v2_columns_indexes_and_metadata`
  - `test_schema_reopen_valid_v2_is_idempotent_no_duplicate_columns_or_indexes`
  - `test_schema_existing_table_without_metadata_rejected_and_preserves_data`
  - `test_schema_v2_metadata_with_missing_column_rejected_and_preserves_data`
  - `test_schema_v2_metadata_with_missing_index_rejected_and_preserves_data`
  - `test_schema_v1_migration_failure_rolls_back_completely`
- **Eventos corrompidos:**
  - `test_corrupted_first_event_does_not_block_intact_second_event_and_is_preserved_byte_for_byte`
  - `test_all_eligible_events_corrupted_returns_none_and_delivers_nothing`
  - `test_concurrent_workers_skip_corrupted_and_claim_intact_event_only_once`

### 11.5 Resultados Reais Executados no Windows (Python 3.13.7, `win32`)

| Escopo | Tipo | Comando | Executados | Passados | Falhos | Duração |
|---|---|---|---:|---:|---:|---:|
| Task 4 (unit + integration) | Executado localmente no Windows (transporte Hub simulado via mock) | `pytest tests/unit/test_watchdog_outbox.py tests/integration/test_watchdog_outbox_integration.py -v` | 37 | 37 | 0 | 4.48s |
| Task 3 (regressão) | Executado localmente no Windows (SQLite real) | `pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v` | 22 | 22 | 0 | 1.08s |
| Tasks 1 e 2 (regressão Windows) | Executado no Windows (Win32 Job Object + Process Launcher reais + unitários) | `pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v` | 69 | 69 | 0 | 1.00s |

### 11.6 Limitações Restantes e Integrações Não Implementadas

- **Integrações reais não implementadas (fora do escopo da Task 4):** daemon do Watchdog, integração HTTP real com o Cloud Hub, endpoint de ingestão/ACK idempotente no Cloud Hub, autenticação de workers, Reconciliation Engine e retry de upload YouTube.
- **Testes simulados:** o transporte remoto (`OutboxTransport`) nos testes da Task 4 utiliza mocks locais para simular ACKs, erros 4xx/5xx e crashes de entrega.


---

## 12. Rodada Corretiva Final — Validação Estrutural dos Índices

**Decisão Final:** **PASS FINAL — CAMADA LOCAL**

### 12.1 Problema Identificado

A auditoria independente identificou que `_validate_v2_schema()` em `desktop_worker/watchdog/outbox.py` verificava a existência dos índices obrigatórios apenas pelo nome em `sqlite_master`, sem validar a definição estrutural (tabela associada, colunas indexadas, ordem das colunas, unicidade `UNIQUE` e condição parcial `WHERE`). Como resultado, um índice recriado com o mesmo nome mas com colunas, ordem, unicidade ou cláusula `WHERE` incorretas era aceito indevidamente.

### 12.2 Índices Obrigatórios e Definições Reais (Contrato Task 4)

| Nome do Índice | Tabela | Colunas (Ordem Exata) | `UNIQUE` | Parcial (`WHERE`) |
|---|---|---|---|---|
| `idx_outbox_status_available` | `outbox_event` | `("status", "available_at_utc")` | `False` | `False` (nenhuma) |
| `idx_outbox_inflight_lease` | `outbox_event` | `("status", "lease_expires_at_utc")` | `False` | `False` (nenhuma) |
| `idx_outbox_exec_attempt` | `outbox_event` | `("execution_id", "attempt_id")` | `False` | `False` (nenhuma) |
| `idx_outbox_delivered` | `outbox_event` | `("status", "delivered_at_utc")` | `False` | `False` (nenhuma) |

### 12.3 Estratégia de Validação Implementada

- Introduzida a especificação estrutural `_ExpectedIndexSpec` e a tabela de contrato `_OUTBOX_REQUIRED_INDEX_SPECS` em `desktop_worker/watchdog/outbox.py`.
- `_validate_v2_schema()` (executado dentro da transação `BEGIN IMMEDIATE` do `StateStore`) passou a:
  1. Consultar `PRAGMA index_list('outbox_event')` — garantindo que apenas índices efetivamente associados à tabela `outbox_event` sejam considerados.
  2. Verificar a presença de todos os índices obrigatórios.
  3. Validar `unique` (`bool(idx_row["unique"]) == spec.unique`).
  4. Validar `partial` (`bool(idx_row["partial"]) == spec.partial`) e, quando `spec.partial` for `True` com `where_clause` definida, comparar a cláusula `WHERE` normalizada extraída de `sqlite_master.sql`.
  5. Consultar `PRAGMA index_info('<nome>')`, ordenar por `seqno` e comparar a tupla exata de colunas com `spec.columns` (validando nomes e ordem).
- Em qualquer divergência, lança `OutboxSchemaError` em modo fail-closed sem recriar índices, sem alterar metadados e preservando integralmente o banco original.

### 12.4 Testes Adicionados (+8 testes unitários em `tests/unit/test_watchdog_outbox.py`)

- **Teste A:** `test_index_validation_a_valid_v2_indexes_succeed`
- **Teste B:** `test_index_validation_b_same_name_wrong_column_rejected`
- **Teste C:** `test_index_validation_c_wrong_column_order_rejected`
- **Teste D:** `test_index_validation_d_wrong_uniqueness_rejected`
- **Teste E:** `test_index_validation_e_wrong_table_rejected`
- **Teste F:** `test_index_validation_f_unexpected_partial_where_clause_rejected`
- **Teste G:** `test_index_validation_g_failure_preserves_db_rows_metadata_indexes_and_checksums`
- **Teste H:** `test_index_validation_h_repeated_reopen_is_idempotent`

### 12.5 Resultados Efetivos dos Testes no Windows (`win32`, Python 3.13.7)

| Escopo | Comando | Executados | Passados | Falhos | Duração |
|---|---|---:|---:|---:|---:|
| **Task 4** (unit + integration) | `pytest tests/unit/test_watchdog_outbox.py tests/integration/test_watchdog_outbox_integration.py -v` | 45 | 45 | 0 | 1.96s |
| **Task 3** (regressão) | `pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v` | 22 | 22 | 0 | 0.73s |
| **Tasks 1 e 2** (regressão Windows) | `pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v` | 69 | 69 | 0 | 0.90s |

### 12.6 Arquivos Modificados e Controle de Escopo

- `desktop_worker/watchdog/outbox.py`
- `tests/unit/test_watchdog_outbox.py`
- `docs/sprints/SPRINT_2_7_TASK_4_HOMOLOGATION.md`

Confirmado que `state_store.py`, `identity.py`, `job_object.py`, `process_launcher.py` e todos os testes homologados das Tasks 1, 2 e 3 **não foram alterados**.

### 12.7 Limitações Ainda Existentes

- Integração HTTP real com o Cloud Hub, endpoint de ingestão/ACK idempotente, autenticação de workers, daemon do Watchdog, Reconciliation Engine e retry de upload YouTube permanecem fora do escopo da Task 4.


---

## 13. Correção Final — Validação do Schema na Criação de Banco Novo (Cenário A)

**Decisão Final:** **PASS FINAL — CAMADA LOCAL**

### 13.1 Causa Raiz

No cenário A de `_ensure_schema()` em `desktop_worker/watchdog/outbox.py` (banco novo sem tabela `outbox_event` e sem metadado `outbox_schema_version`), o código executava `_OUTBOX_SCHEMA_STATEMENTS` (`CREATE TABLE IF NOT EXISTS` e `CREATE INDEX IF NOT EXISTS`), registrava `outbox_schema_version = 2` em `schema_metadata` e retornava sem chamar `_validate_v2_schema(conn)`.

No SQLite, `CREATE INDEX IF NOT EXISTS <nome> ON outbox_event(...)` é silenciosamente ignorado caso já exista um índice com o mesmo `<nome>` associado a outra tabela no mesmo banco. Consequentemente, o `OutboxStore` podia concluir a inicialização e registrar schema v2 mesmo sem todos os índices obrigatórios criados na tabela `outbox_event`.

### 13.2 Correção Implementada

No cenário A de `OutboxStore._ensure_schema()` (`desktop_worker/watchdog/outbox.py`):

1. Executa os statements de criação da tabela `outbox_event` e dos 4 índices obrigatórios (`_OUTBOX_SCHEMA_STATEMENTS`).
2. Executa `self._validate_v2_schema(conn)` imediatamente após a criação, antes de gravar qualquer metadado.
3. Somente após a aprovação da validação estrutural completa registra `outbox_schema_version = 2` em `schema_metadata`.
4. Todas as etapas permanecem dentro da mesma transação `self._store._transaction()` do `StateStore`.
5. Em caso de falha na validação (`OutboxSchemaError`), a exceção dispara rollback integral da transação SQLite (incluindo DDL), impedindo que `outbox_event`, índices parciais ou `outbox_schema_version` sejam persistidos e sem modificar tabelas/índices de terceiros.

### 13.3 Testes Adicionados e Evidência de Rollback Integral

Adicionados 3 novos testes unitários em `tests/unit/test_watchdog_outbox.py`:

- **Teste 1 (`test_new_db_index_name_collision_rolls_back_completely`):**
  - Cria `StateStore` novo, cria tabela auxiliar `other_tbl`, insere registro e cria índice `idx_outbox_status_available` sobre `other_tbl(val)`.
  - Tenta inicializar `OutboxStore(store)` e confirma `OutboxSchemaError` (`missing required indexes`).
  - **Evidência de rollback integral:** consulta direta via `sqlite3.connect` após a falha confirma que:
    - `outbox_schema_version` não existe em `schema_metadata`;
    - a tabela `outbox_event` não existe em `sqlite_master`;
    - nenhum dos demais índices criados na transação (`idx_outbox_inflight_lease`, `idx_outbox_exec_attempt`, `idx_outbox_delivered`) permaneceu no banco;
    - `other_tbl`, seus dados (`row-1`) e seu índice original `idx_outbox_status_available` permanecem intactos.
- **Teste 2 (`test_new_db_without_collision_creates_validates_and_registers_v2`):**
  - Inicializa `OutboxStore` em banco novo sem colisões, confirma criação dos 4 índices em `outbox_event`, aprovação de `_validate_v2_schema(conn)` e registro de `outbox_schema_version = 2`.
- **Teste 3 (`test_reopen_valid_v2_db_is_idempotent_and_preserves_schema`):**
  - Reabre banco v2 válido, confirma leitura do evento persistido, idempotência e igualdade exata do snapshot do schema antes e depois.
- **Teste 4 (Regressão A–H):** todos os 8 testes de validação estrutural de índices da rodada anterior (`test_index_validation_a` a `test_index_validation_h`) executados novamente e aprovados.

### 13.4 Resultados Reais das Regressões no Windows (`win32`, Python 3.13.7)

| Escopo | Comando | Executados | Passados | Falhos | Duração |
|---|---|---:|---:|---:|---:|
| **Task 4** (unit + integration) | `pytest tests/unit/test_watchdog_outbox.py tests/integration/test_watchdog_outbox_integration.py -v` | 48 | 48 | 0 | 2.27s |
| **Task 3** (regressão) | `pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v` | 22 | 22 | 0 | 0.66s |
| **Tasks 1 e 2** (regressão Windows) | `pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v` | 69 | 69 | 0 | 0.88s |

Total consolidado: **139 testes executados, 139 aprovados, 0 falhos**.

### 13.5 Arquivos Modificados

- `desktop_worker/watchdog/outbox.py`
- `tests/unit/test_watchdog_outbox.py`
- `docs/sprints/SPRINT_2_7_TASK_4_HOMOLOGATION.md`

Escopo respeitado: nenhum arquivo das Tasks 1, 2 ou 3 foi alterado e nenhuma funcionalidade da Task 5 foi iniciada.
