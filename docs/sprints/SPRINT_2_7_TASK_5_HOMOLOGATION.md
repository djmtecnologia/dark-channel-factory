# SPRINT 2.7 — TASK 5 — RELATÓRIO DE HOMOLOGAÇÃO (RODADA CORRETIVA)

**Projeto:** Dark Channel Factory (DCF)  
**Sprint:** 2.7 — Local Watchdog Daemon  
**Task:** Task 5 — Rodada Corretiva (Segurança do Ciclo de Vida e Acesso ao State Store)  
**Data:** 2026-11-19  
**Ambiente de Execução:** Windows 11 (`win32`), Python 3.13.7, pytest 8.4.2  
**Parecer Final:** **PASS — TASK 5 HOMOLOGADA**

---

## 1. Problemas Encontrados na Auditoria Independente e Causa Raiz

| # | Severidade | Problema Identificado | Causa Raiz |
|---|---|---|---|
| 1 | **CRÍTICA** | `WatchdogDaemon.stop()` fechava o State Store e liberava o Mutex mesmo quando `_cycle_lock.acquire(timeout=...)` expirava com um ciclo ativo. | O bloco `try:` após `acquire(timeout)` executava `_close_store()` e `release_mutex()` incondicionalmente, permitindo que uma segunda instância assumisse o Mutex enquanto a primeira ainda utilizava a conexão SQLite. |
| 2 | **CRÍTICA** | `spawn_detached_watchdog_daemon()` realizava fallback silencioso sem `CREATE_BREAKAWAY_FROM_JOB` quando recebia `ERROR_ACCESS_DENIED (5)`. | As flags de fallback (`DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP \| CREATE_NO_WINDOW`) não removem o processo filho do Job Object do processo pai, retornando um processo como independente quando ele ainda podia ser encerrado junto com o pai. |
| 3 | **ALTA** | `_check_outbox_readonly()` instanciava `OutboxStore(store)` em rotina declarada somente leitura. | O construtor de `OutboxStore` executa `_ensure_schema()`, que pode migrar o schema v1 para v2, criar índices ausentes e atualizar `schema_metadata`, violando a garantia somente leitura. |
| 4 | **ALTA** | `_open_store()` instanciava `StateStore(db_path)` diretamente sem validar existência prévia do arquivo. | O construtor de `StateStore` cria diretórios/arquivo SQLite e inicializa schema vazio quando o caminho não existe, fazendo um daemon configurado com caminho errado aparentar estado operacional saudável. |

---

## 2. Arquivos Modificados (Escopo Exclusivo da Rodada Corretiva)

- `desktop_worker/watchdog/watchdog_daemon.py`
- `tests/unit/test_watchdog_daemon.py`
- `tests/integration/test_watchdog_daemon_windows.py`
- `docs/sprints/SPRINT_2_7_TASK_5_HOMOLOGATION.md`

**Preservação das Tasks 1–4:** `job_object.py`, `identity.py`, `process_launcher.py`, `state_store.py` e `outbox.py` **não foram modificados**.

---

## 3. Correções Implementadas

### 3.1. Correção 1 — Proteção do State Store e Mutex em `stop()`
- `WatchdogDaemon.stop()` sinaliza `_stop_event`, marca `_running = False` sob `_state_lock` e aguarda `_cycle_lock.acquire(timeout=wait_timeout)`.
- Se o timeout expirar (`clean_cycle_finish == False`):
  - **Não** chama `_close_store()`.
  - **Não** chama `release_mutex()`.
  - Registra `UNCLEAN_SHUTDOWN` em nível `ERROR` e retorna `False`.
- O State Store e o Mutex permanecem protegidos até o término do ciclo ativo (quando uma chamada posterior de `stop()` adquire `_cycle_lock` e libera os recursos com segurança na thread proprietária do Mutex) ou até o encerramento do processo pelo SO.

### 3.2. Correção 2 — Independência Real do Job Object Pai
- Removido o fallback inseguro sem `CREATE_BREAKAWAY_FROM_JOB` em `spawn_detached_watchdog_daemon()`.
- Quando o Job Object pai nega `CREATE_BREAKAWAY_FROM_JOB` (`ERROR_ACCESS_DENIED = 5`), o método registra erro explícito e lança `WatchdogSpawnError` com diagnóstico claro recomendando inicialização via supervisor externo fora do Job Object do Desktop Node (ex.: Windows Service, Task Scheduler ou launcher externo).
- Os Job Objects de upload homologados na Task 1 não foram alterados (`JOB_OBJECT_LIMIT_BREAKAWAY_OK` e `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` continuam desabilitados).

### 3.3. Correção 3 — Verificação Estritamente Somente Leitura da Outbox
- Removida a importação e instanciação de `OutboxStore` em `watchdog_daemon.py`.
- `_check_outbox_readonly()` executa exclusivamente consultas SQLite de leitura (`SELECT` e `PRAGMA`) sob `store._lock`:
  - Verifica existência da tabela `outbox_event`.
  - Verifica `outbox_schema_version == 2` em `schema_metadata` sem migrar.
  - Verifica colunas obrigatórias v2 (`PRAGMA table_info`) e estrutura dos 4 índices obrigatórios (`PRAGMA index_list` / `PRAGMA index_info`) sem recriar índices.
  - Revalida `schema_version`, `status`, `payload_json` e `checksum_sha256` canônico de todos os registros sem alterar nenhuma linha.
- Qualquer divergência (v1, índice ausente, checksum inválido) retorna falha > 0, classificando o ciclo como `INCONCLUSIVE`.

### 3.4. Correção 4 — Validação Prévia do Caminho do State Store
- Adicionado `_validate_state_store_path()` chamado antes de `_store_factory(db_path)` em `_open_store()`:
  - Verifica `db_path.exists()` e `db_path.is_file()` (rejeita caminhos inexistentes e diretórios sem criar arquivos ou pastas).
  - Abre conexão SQLite temporária em modo estritamente somente leitura (`file:<path>?mode=ro`, `uri=True`) para validar integridade básica do arquivo SQLite, presença das 6 tabelas obrigatórias e `schema_version == 1` em `schema_metadata`.
  - Em qualquer falha, registra `STATE_STORE_PATH_INVALID`, mantém `_state_store_accessible = False` e o ciclo retorna `STORE_UNAVAILABLE` sem invocar `StateStore` nem criar/migrar schema.

---

## 4. Evidências de Testes (Windows Real — Python 3.13.7)

### 4.1. Concorrência e Encerramento (Correção 1)
- `test_19_stop_timeout_keeps_store_open_and_mutex_held_until_cycle_finishes` (unit): **PASSED** — ciclo bloqueado além de `stop_timeout_sec`, `stop()` retorna `False` com `UNCLEAN_SHUTDOWN`, State Store permanece aberto, Mutex permanece retido, segunda instância bloqueada (`WatchdogAlreadyRunningError`), liberação do ciclo permite `stop()` subsequente retornar `True` sem deadlock.
- `test_windows_stop_timeout_keeps_real_mutex_held_until_cycle_finishes` (integration Windows): **PASSED** — mesma validação com `Win32MutexApi` real e segundo processo Python real recebendo `EXIT_ALREADY_RUNNING (2)` durante o ciclo ativo.

### 4.2. Independência do Job Object Pai (Correção 2)
- `test_windows_daemon_survives_parent_job_object_when_breakaway_allowed` (integration Windows): **PASSED** — processo pai associado a Job Object real com `KILL_ON_JOB_CLOSE | BREAKAWAY_OK`; após `TerminateJobObject` no Job pai, pai morre e daemon sobrevive mantendo o Mutex.
- `test_windows_daemon_spawn_fails_explicitly_when_parent_job_denies_breakaway` (integration Windows): **PASSED** — processo pai associado a Job Object real com `KILL_ON_JOB_CLOSE` sem `BREAKAWAY_OK`; `spawn_detached_watchdog_daemon()` falha explicitamente com `WatchdogSpawnError` (`CREATE_BREAKAWAY_FROM_JOB` / `ERROR_ACCESS_DENIED`), sem fallback inseguro e sem encerrar processo externo inofensivo.
- `test_windows_daemon_survives_parent_process_termination` (integration Windows): **PASSED**.

### 4.3. Verificação Somente Leitura da Outbox (Correção 3)
- `test_20a_outbox_v2_valid_readonly_no_mutations`: **PASSED** (snapshot `sqlite_master`, `schema_metadata` e `outbox_event` idêntico antes/depois).
- `test_20b_outbox_v1_classified_inconclusive_without_migration`: **PASSED** (classificado `INCONCLUSIVE`, schema permanece v1, snapshot idêntico).
- `test_20c_outbox_invalid_checksum_classified_inconclusive_without_mutation`: **PASSED** (classificado `INCONCLUSIVE`, registro intacto).
- `test_20d_outbox_missing_index_classified_inconclusive_without_recreating`: **PASSED** (classificado `INCONCLUSIVE`, índice não recriado).

### 4.4. Não Criação de State Store por Erro de Caminho (Correção 4)
- `test_21a_nonexistent_state_store_returns_store_unavailable_without_creating_files`: **PASSED** (`STORE_UNAVAILABLE`, `STATE_STORE_PATH_INVALID`, nem arquivo nem diretório criados).
- `test_21b_directory_as_state_store_returns_store_unavailable`: **PASSED** (`STORE_UNAVAILABLE`, `STATE_STORE_PATH_INVALID`).
- `test_21c_corrupted_sqlite_file_returns_store_unavailable_without_mutation`: **PASSED** (`STORE_UNAVAILABLE`, bytes do arquivo inalterados).
- `test_21d_incompatible_schema_file_returns_store_unavailable_without_migration`: **PASSED** (`STORE_UNAVAILABLE`, tabelas do State Store não criadas).

---

## 5. Resultados Consolidados por Task e Plataforma

Todas as suítes executadas em **Windows 11 (`win32`), Python 3.13.7**, com **0 testes ignorados (0 skipped)**:

| Task | Suítes Executadas | Plataforma | Resultado |
|---|---|---|---|
| **Task 5 (Unit)** | `tests/unit/test_watchdog_daemon.py` | Windows (`win32`, Py 3.13.7) | **38 passed, 0 failed, 0 skipped** |
| **Task 5 (Integration Windows)** | `tests/integration/test_watchdog_daemon_windows.py` | Windows (`win32`, Py 3.13.7) | **7 passed, 0 failed, 0 skipped** |
| **Task 4 (Outbox)** | `tests/unit/test_watchdog_outbox.py`, `tests/integration/test_watchdog_outbox_integration.py` | Windows (`win32`, Py 3.13.7) | **48 passed, 0 failed, 0 skipped** |
| **Task 3 (State Store)** | `tests/unit/test_watchdog_state_store.py`, `tests/integration/test_watchdog_state_store_integration.py` | Windows (`win32`, Py 3.13.7) | **22 passed, 0 failed, 0 skipped** |
| **Tasks 1 e 2 (Job Object, Identity, Launcher)** | `tests/unit/test_win_job_object.py`, `tests/integration/test_win_job_object_integration.py`, `tests/unit/test_watchdog_identity.py`, `tests/integration/test_win_process_launcher.py` | Windows (`win32`, Py 3.13.7) | **69 passed, 0 failed, 0 skipped** |
| **TOTAL** | **10 suítes** | **Windows (`win32`, Py 3.13.7)** | **184 passed, 0 failed, 0 skipped** |

---

## 6. Limitações Remanescentes

1. **Inicialização em ambiente com Job Object restritivo:** Se o processo pai que chama `spawn_detached_watchdog_daemon()` estiver confinado em um Job Object que proíbe `CREATE_BREAKAWAY_FROM_JOB`, a criação falhará explicitamente com `WatchdogSpawnError`. Em produção sob supervisores restritivos, o Watchdog Daemon deve ser iniciado por um supervisor externo independente (Windows Service ou Task Scheduler).
2. **Thread affinity do Win32 `ReleaseMutex`:** Após um `stop()` que expira por timeout (`False`), a chamada subsequente de `stop()` para liberação limpa do Named Mutex deve ocorrer na mesma thread que adquiriu o Mutex (requisito do kernel Win32); caso o processo seja encerrado sem nova chamada, o kernel Windows libera automaticamente o handle e sinaliza `WAIT_ABANDONED` para a próxima instância.
3. **Escopo da Task 5:** Reconciliação YouTube, envio real da Outbox ao Cloud Hub e encerramento ativo de processos órfãos permanecem fora de escopo (reservados às próximas tasks).

---

## 7. Parecer Final

**`PASS — TASK 5 HOMOLOGADA`**
