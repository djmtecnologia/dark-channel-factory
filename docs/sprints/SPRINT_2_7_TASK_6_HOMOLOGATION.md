# SPRINT 2.7 — TASK 6 HOMOLOGATION REPORT
## Safe Process Termination & Termination Evidence Engine

- **Projeto:** Dark Channel Factory (DCF)
- **Sprint:** 2.7 — Local Watchdog
- **Task:** 6 — Safe Process Termination & Termination Evidence Engine
- **Ambiente:** Windows 11 / Python 3.13.7
- **Data:** 2026-11-19
- **Parecer Final:** ---

## 9. Última Correção — Strict Launcher Identity, Watchdog Instance Binding & Concurrent IPC Shutdown

### 9.1 Identidade Obrigatória do Launcher (Fail-Closed)
- `JobHandleTransferServer.__init__` e `JobHandleTransferServer.start()` agora **exigem** `expected_launcher_pid` (inteiro positivo) e `expected_launcher_create_time` (inteiro positivo FILETIME de 64 bits). A ausência de qualquer um dos campos impede a inicialização do servidor IPC (`ValueError` / `WatchdogIPCError`), sem fallback permissivo.
- `_validate_peer_identity()` executa em **todas** as operações (`PING`, `REGISTER_HANDLE`, `UNREGISTER_HANDLE`), exigindo correspondência exata do PID e `process_create_time` obtidos diretamente do kernel do Windows (`GetExtendedTcpTable` + `GetProcessTimes`). O `source_pid` informado pelo cliente nunca é confiado e qualquer divergência é rejeitada (`spoofed_source_pid`).
- **Estabelecimento e proteção da identidade inicial**: o supervisor/processo pai que inicia o `WatchdogDaemon` com `termination_engine` (ou `ipc_enabled=True`) obtém o PID e o `process_create_time` do Launcher diretamente do kernel (`GetProcessTimes`) e os injeta na inicialização do `WatchdogDaemon` juntamente com o segredo HMAC (`ipc_auth_token`, nunca logado nem transmitido em texto plano).

### 9.2 Reautorização Explícita após Reinicialização do Launcher
- Nenhum comando recebido via socket IPC altera automaticamente o Launcher autorizado.
- Se o Launcher reiniciar, a troca de identidade ocorre exclusivamente via `WatchdogDaemon.reauthorize_launcher(...)` -> `JobHandleTransferServer.reauthorize_launcher(...)`, exigindo:
  1. Daemon ativo, com Mutex adquirido e servidor IPC fora de shutdown;
  2. Autenticação em tempo constante (`hmac.compare_digest`) com `auth_token`;
  3. Verificação no kernel (`GetProcessTimes` + `STILL_ACTIVE`) de que o novo processo existe, está ativo e possui o `create_time` declarado;
  4. Atualização atômica sob `_identity_lock` com log de auditoria (sem expor o segredo).

### 9.3 Vínculo com a Instância Atual do Watchdog
- `PING` retorna `watchdog_pid`, `watchdog_create_time` (`watchdog_process_create_time`) e `daemon_instance_id`.
- `REGISTER_HANDLE` exige `target_watchdog_pid`, `target_watchdog_create_time` e `target_daemon_instance_id` correspondentes à instância atual. Qualquer uso de informações de `PING` de uma instância anterior é rejeitado com `watchdog_instance_mismatch` antes de registrar ou tocar em handles.
- `AuthorizedJobHandle` registra o vínculo da instância do Watchdog e `_validate_authorized_job_handle` valida que `watchdog_pid == os.getpid()`.

### 9.4 Shutdown Seguro do IPC sob Concorrência
- `JobHandleTransferServer.close(timeout_sec)` sinaliza `_stop_event` (bloqueando novas requisições com `server_shutting_down`), fecha o socket de escuta para impedir novas conexões e aguarda `thread.join(timeout=timeout_sec)`. Se a thread ainda estiver viva, retorna `False` e preserva a referência da thread.
- `WatchdogDaemon.stop(timeout_sec)` verifica o retorno de `_stop_ipc_server(timeout_sec)`: se a thread IPC não encerrar dentro do timeout, **não declara shutdown limpo**, retorna `False` e **mantém o Mutex adquirido, o `StateStore` aberto e o `termination_engine` intacto**.
- O comportamento da Task 5 (timeout de ciclo ativo mantendo Mutex e `StateStore`) e o fechamento diferido de handles durante terminação ativa (`_active_executions` + `_deferred_close_handles`) foram preservados e verificados sob concorrência.

### 9.5 Evidência de Execução no Windows 11
- **Testes obrigatórios (12 cenários)**: `tests/unit/test_strict_launcher_identity_and_ipc_shutdown.py` — **11/11 PASSED** (cobrindo os 12 cenários obrigatórios).
- **Regressão completa Tasks 1–6 (unitários + integração + processos independentes Windows 11)**: `pytest tests/unit tests/integration -v` — **273 passed in 32.82s**.

**PASS — Task 6 homologada (com última correção Strict Launcher Identity & IPC Shutdown).**

---

## 1. Objetivo

Implementar um mecanismo seguro, controlado e verificável de encerramento de árvores de processos de upload no Local Watchdog, com validação rigorosa de identidade (anti-PID-reuse), encerramento exclusivo via Job Object Win32 real, verificação independente pós-encerramento (`ActiveProcesses == 0` e lista de PIDs vazia e não truncada), persistência durável de evidências com checksum SHA-256 no State Store e registro atômico de eventos na Durable Outbox.

---

## 2. Arquitetura Implementada

A implementação preserva integralmente os módulos homologados das Tasks 1–5 e organiza a Task 6 em dois componentes coesos:

1. **`TerminationEvidenceRecorder` (`desktop_worker/watchdog/termination_evidence.py`)**
   - Modela `TerminationVerificationResult`, `TerminationOutboxEventType`, `TerminationEvidencePayload` e `EvidencePersistenceResult`.
   - Constrói `TerminationEvidenceRecord` com payload JSON canônico, `schema_version` e checksum SHA-256.
   - Persiste evidência + evento Outbox na **mesma transação SQLite** quando `OutboxStore` compartilha o mesmo `StateStore`, garantindo atomicidade e rollback completo em caso de falha.
   - Garante idempotência por `event_id` sem sobrescrever evidências anteriores e rejeitando conteúdo divergente.

2. **`SafeTerminationEngine` (`desktop_worker/watchdog/termination_engine.py`)**
   - Implementa detecção e classificação explícita de execuções órfãs (`OrphanClassification`).
   - Reutiliza `IdentityValidator` (Task 2), `ManagedJobProcess` / `Win32Api` (Task 1), `StateStore` (Task 3) e `OutboxStore` (Task 4).
   - Protege operações concorrentes por `execution_id` via lock interno (`_active_executions`).
   - Revalida identidade imediatamente antes de `TerminateJobObject` e executa verificação independente pós-encerramento.

3. **Integração mínima com `WatchdogDaemon` (`desktop_worker/watchdog/watchdog_daemon.py`)**
   - Adicionado parâmetro opcional `termination_engine` em `WatchdogDaemon.__init__` (100% retrocompatível com a Task 5).
   - Adicionado método `request_safe_termination(...)` que exige posse do Mutex, adquire `_cycle_lock` (preservando shutdown limpo) e delega ao `SafeTerminationEngine`.

---

## 3. Arquivos Criados e Modificados

### Arquivos Criados
- `desktop_worker/watchdog/termination_evidence.py`
- `desktop_worker/watchdog/termination_engine.py`
- `tests/unit/test_termination_engine.py`
- `tests/integration/test_termination_engine_integration.py`
- `tests/integration/test_termination_engine_windows.py`
- `docs/sprints/SPRINT_2_7_TASK_6_HOMOLOGATION.md`

### Arquivos Modificados
- `desktop_worker/watchdog/watchdog_daemon.py` (adição mínima de `termination_engine` opcional no construtor e método `request_safe_termination`)
- `desktop_worker/watchdog/__init__.py` (exportação dos símbolos públicos da Task 6)

### Módulos Protegidos Preservados (Sem Alterações)
- `desktop_worker/watchdog/job_object.py`
- `desktop_worker/watchdog/identity.py`
- `desktop_worker/watchdog/process_launcher.py`
- `desktop_worker/watchdog/state_store.py`
- `desktop_worker/watchdog/outbox.py`

---

## 4. Fluxo de Identificação (Orphan Detection)

`SafeTerminationEngine.classify_orphan()` consulta `ExecutionRecord`, `AttemptRecord`, `ProcessIdentityRecord` e `CheckpointRecord` no State Store e classifica explicitamente em:
- `NOT_ORPHANED`: heartbeat ativo dentro do timeout e mesmo boot.
- `ORPHAN_CANDIDATE`: heartbeat expirado isoladamente (sem `job_handle` real validado) — nunca autoriza encerramento por si só.
- `SAFE_TO_TERMINATE`: heartbeat expirado, mesmo boot, `fencing_token` íntegro e `IdentityValidator.verify() == VALID`.
- `ALREADY_TERMINATED`: processo não encontrado (`PROCESS_NOT_FOUND`), encaminhado para verificação independente do Job Object.
- `IDENTITY_MISMATCH`: divergência de `fencing_token`, `PID_REUSED` ou `NOT_IN_JOB`.
- `INCONCLUSIVE`: checksum inválido/corrupção, identidade incompleta, boot divergente ou identidade `UNVERIFIABLE`.

---

## 5. Fluxo de Validação de Identidade (Persisted Identity Authorization)

Antes de qualquer chamada a `TerminateJobObject`, `SafeTerminationEngine._execute_termination()` executa obrigatoriamente a sequência de 15 etapas abaixo (nenhuma operação destrutiva ocorre antes da conclusão das etapas 1–12):

1. Carrega `ExecutionRecord` do State Store.
2. Valida integridade e checksum SHA-256 do `ExecutionRecord`.
3. Carrega `AttemptRecord` correspondente, valida sua existência, checksum SHA-256 e correspondência de `attempt_id` e `job_id`.
4. Valida `fencing_token` entre `AttemptRecord`, `ExecutionRecord`, `TerminationContext.identity` e `expected_fencing_token` (quando informado), bloqueando qualquer divergência sem mutar tokens.
5. Carrega os `ProcessIdentityRecord` da execução e valida existência e checksum SHA-256 de todos os registros.
6. Exige correspondência exata entre `TerminationContext.identity` e os dados persistidos nos 11 campos obrigatórios (`execution_id`, `attempt_id`, `job_id`, `tenant_id`, `channel_id`, `worker_id`, `fencing_token`, `pid`, `process_create_time`, `job_object_name`, `browser_user_data_dir`), vinculando-se a um `ProcessIdentityRecord` persistido exato.
7. Valida o status da execução contra a lista explícita permitida (`ALLOWED_TERMINATION_STATUSES = {RUNNING, TERMINATING}`), bloqueando `CLOSED`, `INCONCLUSIVE` e qualquer status desconhecido/inconsistente.
8. Exige `job_handle` Win32 real (`> 0`); `job_object_name` lógico nunca substitui o handle real.
9. Executa `IdentityValidator.verify()` utilizando a identidade canônica construída a partir dos registros persistidos.
10. Captura snapshot pré-encerramento (`active_processes_before`, `pids_before`).
11. Revalida a identidade persistida imediatamente antes da operação (defense-in-depth).
12. Verifica `stop_event` do daemon.
13. Executa `win32.terminate_job_object(job_handle, exit_code)` no handle real do Job Object.
14. Executa verificação independente pós-encerramento até `timeout_ms`.
15. Persiste evidência e enfileira evento na Outbox.

---

## 6. Idempotência de `event_id`

`TerminationEvidenceRecorder.build_evidence_record()` deriva um `event_id` determinístico (`derive_stable_event_id`) a partir dos campos canônicos da operação quando o chamador não informa um `event_id` explícito, eliminando UUIDs aleatórios em repetições:
- Repetir a mesma operação reutiliza o mesmo `event_id` sem duplicar evidência nem evento Outbox.
- Reutilizar o mesmo `event_id` com payload divergente gera conflito (`StateStoreConflictError`) e nunca sobrescreve eventos anteriores.

---

## 7. Critérios de Confirmação

`TerminationVerificationResult.TREE_TERMINATED` somente é emitido quando **todas** as condições abaixo são satisfeitas simultaneamente:
- `WaitForSingleObject` (quando `process_handle` fornecido) não retornou `WAIT_FAILED`.
- `QueryInformationJobObject(BasicAccountingInformation)` retornou `ActiveProcesses == 0`.
- `QueryInformationJobObject(BasicProcessIdList)` retornou `ok == True`, `truncated == False` e lista de PIDs vazia (`[]`).
- O daemon não foi interrompido (`stop_event` não sinalizado) nem o timeout foi excedido.

Falhas de consulta, truncamento (`ERROR_MORE_DATA`), `WAIT_FAILED`, timeout ou interrupção nunca produzem `TREE_TERMINATED`.

---

## 8. Modelo de Evidências

`TerminationEvidencePayload` registra todos os campos mínimos exigidos:
- Identidade completa: `execution_id`, `attempt_id`, `job_id`, `worker_id`, `fencing_token`, `pid`, `process_create_time`, `job_object_name`.
- Validação de identidade: `identity_validation_status`, `identity_validation_reason`.
- Operação e verificação: `termination_request_ok`, `wait_result`, `active_processes_before`, `active_processes_after`, `pids_before`, `pids_after`, `pids_truncated`, `exit_code`.
- Metadados e auditoria: `termination_requested_at_utc`, `verified_at_utc`, `termination_reason`, `watchdog_version`, `verification_result`, `error_code`, `error_message`.
- Integridade: `schema_version` e `checksum_sha256` canônico.

---

## 9. Integração com State Store e Outbox

- Evidências são gravadas na tabela `termination_evidence` vinculadas à identidade persistida (`upload_execution` + `process_identity`) com validação de `fencing_token` e checksum SHA-256.
- Eventos duráveis são enfileirados em `outbox_event` com status `PENDING` nos tipos `TERMINATION_CONFIRMED`, `TERMINATION_FAILED`, `TERMINATION_INCONCLUSIVE` ou `TERMINATION_REQUESTED`.
- Quando `OutboxStore` compartilha o mesmo `StateStore`, ambos os inserts ocorrem na **mesma transação SQLite** (`BEGIN IMMEDIATE ... COMMIT`), com rollback total em caso de erro.
- Falhas de persistência marcam `evidence_persisted = False` e `fully_completed = False`, impedindo falso sucesso.
- Nenhuma comunicação HTTP ou marcação artificial como `DELIVERED` foi implementada.

---

## 10. Testes Executados

1. `tests/unit/test_termination_engine.py` — 35 testes unitários cobrindo autorização por identidade persistida (divergência em todos os campos obrigatórios, `AttemptRecord` ausente/divergente, status `CLOSED`/`INCONCLUSIVE`, `ProcessIdentityRecord` ausente/corrompido, teste positivo de correspondência exata), PID reuse, fencing token, falhas Win32, truncamento, timeout, interrupção, persistência, idempotência com `event_id` estável, rollback transacional, concorrência e classificação de órfãos.
2. `tests/integration/test_termination_engine_integration.py` — 2 testes de integração com `WatchdogDaemon`, Mutex, `StateStore`, `OutboxStore` e recuperação após operação inconclusiva.
3. `tests/integration/test_termination_engine_windows.py` — 6 testes reais no Windows 11 com processos Python inofensivos em Job Objects reais.
4. Regressão completa das Tasks 1–5 (`tests/unit` e `tests/integration`).

---

## 11. Resultados por Suíte

| Suíte | Casos | Resultado |
|---|---|---|
| `tests/unit/test_termination_engine.py` | 35 | 35 PASSED |
| `tests/integration/test_termination_engine_integration.py` | 2 | 2 PASSED |
| `tests/integration/test_termination_engine_windows.py` | 6 | 6 PASSED |
| Regressão Tasks 1–5 (`tests/unit` + `tests/integration`) | 184 | 184 PASSED |
| **Total Geral (Tasks 1–6)** | **227** | **227 PASSED** |

---

## 12. Testes Windows Reais

Executados diretamente no Windows 11 com `JobObjectProcessManager` e `IdentityValidator` reais:
- Processo isolado em Job Object encerrado com `ActiveProcesses = 0`, `pids = []`, `truncated = False` e evidência persistida em SQLite.
- Árvore pai + filho encerrada completamente via `TerminateJobObject`.
- Processo já encerrado verificado independentemente pelo Job Object.
- Identidade divergente (`process_create_time` incorreto) classificada como `PID_REUSED` / `IDENTITY_MISMATCH` com bloqueio total do encerramento (processo mantido intacto).
- PID inexistente tratado como `PROCESS_NOT_FOUND` com verificação do Job Object.
- Timeout de verificação classificado como `TERMINATION_TIMEOUT` (nunca `TREE_TERMINATED`).

---

## 13. Regressões das Tasks 1–5

Nenhuma regressão detectada. Todos os 184 testes das Tasks 1–5 permaneceram passando integralmente sem remoção ou enfraquecimento de asserções.

---

## 14. Limitações Conhecidas

- A Task 6 não implementa nem autoriza retry automático (reservado para etapas posteriores após reconciliação independente com YouTube e `CONFIRMED_ABSENT`).
- Não há transporte HTTP real da Outbox para o Cloud Hub nesta etapa.
- O encerramento requer o handle real do Job Object Win32 (processos de boots anteriores sem handle vivo são classificados como `INCONCLUSIVE`).

---

## 15. Parecer Final

---

## 9. Última Correção — Strict Launcher Identity, Watchdog Instance Binding & Concurrent IPC Shutdown

### 9.1 Identidade Obrigatória do Launcher (Fail-Closed)
- `JobHandleTransferServer.__init__` e `JobHandleTransferServer.start()` agora **exigem** `expected_launcher_pid` (inteiro positivo) e `expected_launcher_create_time` (inteiro positivo FILETIME de 64 bits). A ausência de qualquer um dos campos impede a inicialização do servidor IPC (`ValueError` / `WatchdogIPCError`), sem fallback permissivo.
- `_validate_peer_identity()` executa em **todas** as operações (`PING`, `REGISTER_HANDLE`, `UNREGISTER_HANDLE`), exigindo correspondência exata do PID e `process_create_time` obtidos diretamente do kernel do Windows (`GetExtendedTcpTable` + `GetProcessTimes`). O `source_pid` informado pelo cliente nunca é confiado e qualquer divergência é rejeitada (`spoofed_source_pid`).
- **Estabelecimento e proteção da identidade inicial**: o supervisor/processo pai que inicia o `WatchdogDaemon` com `termination_engine` (ou `ipc_enabled=True`) obtém o PID e o `process_create_time` do Launcher diretamente do kernel (`GetProcessTimes`) e os injeta na inicialização do `WatchdogDaemon` juntamente com o segredo HMAC (`ipc_auth_token`, nunca logado nem transmitido em texto plano).

### 9.2 Reautorização Explícita após Reinicialização do Launcher
- Nenhum comando recebido via socket IPC altera automaticamente o Launcher autorizado.
- Se o Launcher reiniciar, a troca de identidade ocorre exclusivamente via `WatchdogDaemon.reauthorize_launcher(...)` -> `JobHandleTransferServer.reauthorize_launcher(...)`, exigindo:
  1. Daemon ativo, com Mutex adquirido e servidor IPC fora de shutdown;
  2. Autenticação em tempo constante (`hmac.compare_digest`) com `auth_token`;
  3. Verificação no kernel (`GetProcessTimes` + `STILL_ACTIVE`) de que o novo processo existe, está ativo e possui o `create_time` declarado;
  4. Atualização atômica sob `_identity_lock` com log de auditoria (sem expor o segredo).

### 9.3 Vínculo com a Instância Atual do Watchdog
- `PING` retorna `watchdog_pid`, `watchdog_create_time` (`watchdog_process_create_time`) e `daemon_instance_id`.
- `REGISTER_HANDLE` exige `target_watchdog_pid`, `target_watchdog_create_time` e `target_daemon_instance_id` correspondentes à instância atual. Qualquer uso de informações de `PING` de uma instância anterior é rejeitado com `watchdog_instance_mismatch` antes de registrar ou tocar em handles.
- `AuthorizedJobHandle` registra o vínculo da instância do Watchdog e `_validate_authorized_job_handle` valida que `watchdog_pid == os.getpid()`.

### 9.4 Shutdown Seguro do IPC sob Concorrência
- `JobHandleTransferServer.close(timeout_sec)` sinaliza `_stop_event` (bloqueando novas requisições com `server_shutting_down`), fecha o socket de escuta para impedir novas conexões e aguarda `thread.join(timeout=timeout_sec)`. Se a thread ainda estiver viva, retorna `False` e preserva a referência da thread.
- `WatchdogDaemon.stop(timeout_sec)` verifica o retorno de `_stop_ipc_server(timeout_sec)`: se a thread IPC não encerrar dentro do timeout, **não declara shutdown limpo**, retorna `False` e **mantém o Mutex adquirido, o `StateStore` aberto e o `termination_engine` intacto**.
- O comportamento da Task 5 (timeout de ciclo ativo mantendo Mutex e `StateStore`) e o fechamento diferido de handles durante terminação ativa (`_active_executions` + `_deferred_close_handles`) foram preservados e verificados sob concorrência.

### 9.5 Evidência de Execução no Windows 11
- **Testes obrigatórios (12 cenários)**: `tests/unit/test_strict_launcher_identity_and_ipc_shutdown.py` — **11/11 PASSED** (cobrindo os 12 cenários obrigatórios).
- **Regressão completa Tasks 1–6 (unitários + integração + processos independentes Windows 11)**: `pytest tests/unit tests/integration -v` — **273 passed in 32.82s**.

**PASS — Task 6 homologada (com última correção Strict Launcher Identity & IPC Shutdown).**
