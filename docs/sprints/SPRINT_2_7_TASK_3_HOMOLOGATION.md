# SPRINT 2.7 — TASK 3: HOMOLOGAÇÃO (STATE STORE)

**Status:** HOMOLOGADA (PASS)  
**Data:** 2026-11-19  
**Escopo:** Implementação exclusiva do State Store local durável em SQLite (`desktop_worker/watchdog/state_store.py`), preservando integralmente as Tasks 1 e 2 e sem iniciar a Task 4 (`outbox.py`).

---

## 1. Objetivo e Escopo Implementado

Implementar a camada de persistência local durável do Watchdog (`StateStore`) para registrar e recuperar execuções de upload, tentativas, identidades canônicas de processo, evidências de encerramento e checkpoints, com validação de integridade por SHA-256 e proteção contra corrupção e identidades antigas (`fencing_token`).

## 2. Arquitetura do State Store

- Camada independente em `desktop_worker/watchdog/state_store.py`.
- Não executa upload, não encerra processos, não se comunica com Cloud Hub e não realiza reconciliação ou retry.
- Controle de concorrência por `threading.RLock` + `PRAGMA busy_timeout = 5000` + transações explícitas (`BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`).

## 3. Schema SQLite

Configurações obrigatórias aplicadas em toda conexão:
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = FULL;`
- `PRAGMA foreign_keys = ON;`
- `PRAGMA busy_timeout = 5000;`

Tabelas criadas:
- `schema_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)` — armazena `schema_version = 1`.
- `upload_execution` — execuções locais, `fencing_token`, `boot_time_utc`, `status`, `schema_version`, `checksum_sha256`.
- `upload_attempt` — tentativas de upload, `fencing_token`, `hub_status`, `last_heartbeat_at_utc`, `schema_version`, `checksum_sha256`.
- `process_identity` — identidades de processo com `process_create_time INTEGER NOT NULL` (FILETIME 64-bit), `job_object_name` lógico, FK para `upload_execution`, `schema_version`, `checksum_sha256`.
- `termination_evidence` — evidências de encerramento com FK para `upload_execution`, `payload_json`, `schema_version`, `checksum_sha256`.
- `watchdog_checkpoint` — checkpoint do daemon por `worker_id`, `schema_version`, `checksum_sha256`.

Índices: `idx_exec_status`, `idx_exec_attempt`, `idx_identity_exec`, `idx_evidence_exec`.

*(Nota: `outbox_event` permanece reservada à Task 4.)*

## 4. Campos Persistidos e Tipos

- Suporte integral aos 11 campos de `CanonicalExecutionIdentity`.
- `process_create_time` persistido como `INTEGER` (Win32 FILETIME em intervalos de 100 ns), alinhado às Tasks 1 e 2 (em vez de `REAL` do rascunho do plano).
- `job_object_name` preservado como identificador lógico.
- `fencing_token` preservado como `INTEGER NOT NULL CHECK (fencing_token >= 0)`.

## 5. Estratégia de Integridade e Checksum

- Todo registro persistido recebe `checksum_sha256` calculado deterministicamente via SHA-256 sobre JSON canônico (`sort_keys=True, separators=(",", ":")`) contendo `schema_version` e todos os campos do registro.
- Toda leitura valida `schema_version` e recalcula o checksum.
- Divergências de checksum ou status inválido lançam `StateStoreCorruptionError` (classificando como `INCONCLUSIVE`) e `verify_integrity()` retorna `IntegrityVerificationReport(status=IntegrityStatus.INCONCLUSIVE, raw_diagnostic=...)` sem nunca promover dados corrompidos a evidência válida nem liberar retry.

## 6. Estratégia de Transações e Recuperação

- `isolation_level=None` com controle explícito `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`.
- Qualquer erro durante escrita dispara `ROLLBACK` automático e propaga `StateStorePersistenceError`.
- Atualizações de `upload_attempt` e `upload_execution` validam `fencing_token`, rejeitando tokens antigos com `StateStoreConflictError`.
- `close()` idempotente e suporte a context manager (`with StateStore(...) as store:`).

## 7. Arquivos Criados e Modificados

- **Criado:** `desktop_worker/watchdog/state_store.py`
- **Modificado:** `desktop_worker/watchdog/__init__.py` (exportação dos símbolos do `state_store.py`)
- **Criado:** `tests/unit/test_watchdog_state_store.py`
- **Criado:** `tests/integration/test_watchdog_state_store_integration.py`
- **Criado:** `docs/sprints/SPRINT_2_7_TASK_3_HOMOLOGATION.md`

## 8. Testes Executados e Resultados por Arquivo

Comando:
`pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v`

- `tests/unit/test_watchdog_state_store.py`: **18 passed, 0 failed, 0 skipped**
- `tests/integration/test_watchdog_state_store_integration.py`: **1 passed, 0 failed, 0 skipped**
- **Total Task 3:** **19 passed in 0.55s**

## 9. Resultado da Regressão das Tasks 1 e 2 (Ambiente Windows)

Comando:
`pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v`

- **Resultado:** **69 passed, 0 failed, 0 skipped** (0.67s). Nenhuma regressão.
- **Testes ignorados:** Nenhum.

## 10. Correções Pós-Auditoria Independente Aplicadas

1. **`verify_integrity()` em todas as 5 tabelas:** Passou a verificar `upload_execution`, `upload_attempt`, `process_identity`, `termination_evidence` e `watchdog_checkpoint`, capturando erros de conversão/estrutura e continuando a varredura sem interromper.
2. **`upsert_attempt()` com validação prévia:** Passou a validar o checksum/schema do registro existente antes de atualizar, rejeitando registros corrompidos (`StateStoreCorruptionError`), divergência de `job_id` e regressão de `fencing_token`.
3. **`save_evidence()` com validação de consistência:** Passou a validar campos obrigatórios, `payload_json` (JSON object), existência/integridade da execução referenciada, correspondência de `attempt_id`, `job_id`, `worker_id`, `fencing_token` e `(pid, process_create_time)` contra `process_identity`.
4. **`get_checkpoint()`:** Implementada recuperação de checkpoint com validação de `schema_version` e `checksum_sha256`, classificando corrupção como `INCONCLUSIVE`.
5. **`_ensure_schema()` atômico:** Removido `executescript()` (commit implícito); DDL executada statement-a-statement dentro de `BEGIN IMMEDIATE ... COMMIT`, evitando schema parcial.
6. **`_transaction()` com quarentena em falha de rollback:** Se `ROLLBACK` falhar, registra erro crítico com a falha original e a de rollback, invalida a conexão (`_unsafe_state = True`) e bloqueia reutilização sem mascarar a exceção original.

## 11. Limitações / Riscos Residuais

- Operações de `outbox_event` (`outbox.py`) não foram implementadas nesta etapa por pertencerem exclusivamente à Task 4.

## 12. Decisão Final

**PASS** — Task 3 homologada pós-auditoria com 19/19 testes novos aprovados e 69/69 testes de regressão das Tasks 1 e 2 aprovados em ambiente Windows.


---

## 13. Atualização Pós-Auditoria Final (2026-11-19)

### Correções Aplicadas
1. **Validação completa de evidências (`save_evidence()`):**
   - Validação obrigatória de `job_object_name` além dos demais campos obrigatórios e `payload_json` (JSON object).
   - Correspondência integral exigida contra `process_identity`: `pid`, `process_create_time`, `execution_id`, `attempt_id` e `job_object_name` (além da validação prévia de `schema_version` e `checksum_sha256` da execução e da identidade).
2. **Segurança na inicialização do schema (`_initialize()` e `_ensure_schema()`):**
   - Em falha de `ROLLBACK` durante `_ensure_schema()`, registra log crítico e lança `StateStorePersistenceError` preservando o erro original e o erro de rollback.
   - Em qualquer falha de `_initialize()`, fecha explicitamente a conexão SQLite (sem silenciar erro de fechamento) e marca o store como fechado e em estado inseguro (`_unsafe_state = True`).

### Testes Adicionados (`tests/unit/test_watchdog_state_store.py`)
- `test_save_evidence_rejects_mismatched_job_object_name_and_attempt_id_in_identity`
- `test_save_evidence_rejects_corrupted_execution_or_corrupted_identity`
- `test_init_failure_on_schema_version_insert_and_rollback_failure_closes_conn`

### Resultados Reais Executados (Ambiente Windows — Python 3.13.7)
- **Task 3 (`pytest tests/unit/test_watchdog_state_store.py tests/integration/test_watchdog_state_store_integration.py -v`):** **22 passed, 0 failed, 0 skipped** (0.71s).
- **Regressão Tasks 1 e 2 (`pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v`):** **69 passed, 0 failed, 0 skipped** (0.79s).

### Limitações Remanescentes
- Operações de `outbox_event` (`outbox.py`) permanecem reservadas à Task 4.

### Decisão Final
**PASS** — Task 3 definitivamente homologada pós-auditoria (22/22 testes Task 3 + 69/69 regressão Tasks 1 e 2).
