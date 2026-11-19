# SPRINT 2.7 — EVIDÊNCIA DURÁVEL DE ENCERRAMENTO E COMUNICAÇÃO COM CLOUD HUB (ETAPA 0.8 — ESPECIFICAÇÃO TÉCNICA CONSOLIDADA)

**Status:** Especificação Técnica Operacional (Somente Análise e Documentação)  
**Escopo:** Modelo de Evidência, Persistência Local, Comunicação Idempotente e Máquina de Estados `UploadAttempt`

---

## 1. MODELO DO REGISTRO DE EVIDÊNCIA (T4)

Cada evento de encerramento gerado pelo watchdog deve produzir um registro estruturado e imutável:

| Campo | Tipo | Descrição |
|---|---|---|
| `event_id` | UUIDv4 | Identificador único do evento de evidência (chave de idempotência) |
| `attempt_id` | UUID | Identificador da `UploadAttempt` no Cloud Hub |
| `execution_id` | UUID | Identificador local da execução |
| `job_id` | UUID | Identificador do `VideoJob` |
| `worker_id` | string | Identificador do Desktop Node |
| `fencing_token` | int/string | Token de fencing da tentativa encerrada |
| `pid` | int | PID do processo Python uploader |
| `process_create_time` | datetime/float | Timestamp de criação do processo no Windows |
| `descendant_pids` | list[object] | Lista de `{pid, create_time, name}` dos processos descendentes observados |
| `termination_requested_at` | datetime (UTC) | Momento em que o encerramento foi solicitado |
| `verified_at` | datetime (UTC) | Momento em que a verificação foi concluída |
| `verification_result` | enum | Resultado comprovado (ver seção 2) |
| `termination_reason` | enum/string | Motivo (ex: `HEARTBEAT_TIMEOUT`, `LEASE_REVOKED`, `HOST_REBOOTED`, `HUB_DISCONNECT_TIMEOUT`) |
| `evidence_source` | string | Origem (ex: `WINDOWS_JOB_OBJECT`, `HOST_BOOT_TIME_CHECK`, `PROCESS_INSPECTION`) |
| `watchdog_version` | string | Versão do mecanismo de watchdog |

---

## 2. DISTINÇÃO OBRIGATÓRIA DE ESTADOS DA EVIDÊNCIA

Apenas estados **comprovados** podem autorizar a transição de `UNKNOWN` para `TERMINATED` no Cloud Hub:

| `verification_result` | Significado | Válido para `TERMINATED`? |
|---|---|---|
| `REQUESTED` | Encerramento apenas solicitado internamente | **Não** |
| `OS_SIGNAL_SENT` | Chamada de encerramento enviada ao SO | **Não** |
| `MAIN_PROCESS_EXITED` | Apenas o processo Python saiu (descendentes não verificados) | **Não** |
| `TREE_TERMINATED` | Processo principal e toda a árvore no Job Object comprovadamente encerrados | **Sim** |
| `EXECUTION_ISOLATED` | Execução comprovadamente isolada (ex: host reiniciado após `started_at`) | **Sim** |
| `INCONCLUSIVE` | Não foi possível comprovar encerramento completo | **Não** |

---

## 3. PERSISTÊNCIA LOCAL E COMUNICAÇÃO COM CLOUD HUB (T5)

### 3.1. Persistência Local (Outbox Durável e Integridade — T4)
- A evidência deve ser gravada em disco **antes** de qualquer tentativa de envio ao Cloud Hub.
- **Mecanismo de gravação segura:** Utilizar SQLite local em modo `WAL` com `PRAGMA synchronous = FULL`, ou escrita atômica em arquivo (`write-temp` -> `flush` + `os.fsync` -> `os.replace`) contendo checksum SHA-256 do payload.
- **Recuperação de gravação interrompida e detecção de corrupção:**
  - Arquivos temporários órfãos (`.tmp`) encontrados no startup são descartados (nunca promovidos a evidência).
  - Ao ler o State Store/Outbox, validar o checksum/schema de cada registro. Se um registro estiver corrompido ou parcial, movê-lo para quarentena e tratar o estado da tentativa como **`INCONCLUSIVE`** — **nunca** promover evidência parcial a evidência válida.
- **Indisponibilidade do Cloud Hub:** O registro permanece na fila Outbox local até confirmação explícita (`200`/`201`) do Cloud Hub, sobrevivendo a reinícios do Desktop Node.

### 3.2. Exclusão Mútua, Idempotência e Reenvio (T5)
- **Exclusão mútua entre watchdogs:** Apenas um watchdog pode operar por Desktop Node, garantido por Mutex nomeado do Windows (`Local\DCF_WATCHDOG_LOCK_<worker_id>`) com recuperação automática caso o processo detentor morra (Windows libera mutex abandonado com `WAIT_ABANDONED`, permitindo que o novo watchdog assuma após limpeza).
- **Idempotência:** O envio para o Cloud Hub (`POST /worker/upload-attempts/{attempt_id}/termination-evidence`) usa `event_id` (UUIDv4) como chave de idempotência (`UNIQUE` no banco do Cloud Hub). Reenvios após falha de rede ou reinício retornam sucesso idempotente sem duplicar efeitos.

### 3.3. Validação Atômica no Cloud Hub (Proteção Contra Eventos Atrasados e Token Antigo — T5)
Ao receber uma evidência de encerramento, o Cloud Hub **deve** executar validação atômica em transação:
1. **Existência da tentativa:** `attempt_id` existe e pertence ao `job_id` e `worker_id` informados.
2. **Validação atômica de `attempt_id` + `fencing_token`:**
   - A transição no banco deve usar condição atômica (`UPDATE upload_attempts SET ... WHERE attempt_id = :id AND fencing_token = :token AND upload_status = 'UNKNOWN'`).
   - Se o `fencing_token` divergir ou já existir tentativa **mais recente** para o mesmo `job_id`, **rejeitar** (`409 Conflict`). Um evento antigo **nunca** pode alterar o estado de uma tentativa mais recente nem liberar o job indevidamente.
3. **Proteção contra duas recuperações simultâneas:** O `UPDATE` condicional atômico garante que apenas uma transição `UNKNOWN -> TERMINATED` ocorra para o mesmo `UploadAttempt`.
4. **Validação do `verification_result`:** Apenas `TREE_TERMINATED` ou `EXECUTION_ISOLATED` são aceitos como prova de encerramento. `INCONCLUSIVE` é registrado apenas para auditoria/diagnóstico e mantém a tentativa em `UNKNOWN`.

---

## 4. INTEGRAÇÃO COM A MÁQUINA DE ESTADOS DE `UploadAttempt` (T6)

Mantendo integralmente os estados da Etapa 0.6 (`INTENT`, `IN_PROGRESS`, `FAILED_PRE_UPLOAD`, `UNKNOWN`, `CONFIRMED`, `STALE_PUBLISHED_EVIDENCE`, `TERMINATED`):

- A reconciliação com o YouTube (`CONFIRMED_ABSENT`) e a evidência local do watchdog (`TREE_TERMINATED` / `EXECUTION_ISOLATED`) são **controles independentes**. Um não substitui o outro.
- **Regra Obrigatória de Transição `UNKNOWN -> TERMINATED`:**
  Somente ocorre quando **todas** as 3 condições forem verdadeiras no Cloud Hub:
  1. `reconciliation_status == CONFIRMED_ABSENT` (YouTube Studio verificado sem falhas e sem candidatos).
  2. Evidência durável válida (`TREE_TERMINATED` ou `EXECUTION_ISOLATED`) recebida e validada.
  3. Associação inequívoca da evidência ao `attempt_id` e `fencing_token` da tentativa em `UNKNOWN`.
- Se qualquer condição falhar, a tentativa permanece em `UNKNOWN` e o `VideoJob` permanece bloqueado em `UPLOAD_UNKNOWN`, **sem liberar nova tentativa**.
