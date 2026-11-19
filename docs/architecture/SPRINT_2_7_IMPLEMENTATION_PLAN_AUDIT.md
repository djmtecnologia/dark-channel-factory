# SPRINT 2.7 — AUDITORIA INDEPENDENTE DO IMPLEMENTATION PLAN
## ETAPA 1 — REVISÃO TÉCNICA E PREPARAÇÃO DA TASK 1

**Status:** Auditoria Técnica Independente (Somente Análise e Documentação)  
**Objeto Auditado:** [SPRINT_2_7_IMPLEMENTATION_PLAN.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/architecture/SPRINT_2_7_IMPLEMENTATION_PLAN.md)  
**Base de Comparação:** Especificação Consolidada da Etapa 0.8

---

## 1. EXECUTIVE SUMMARY

O plano técnico [SPRINT_2_7_IMPLEMENTATION_PLAN.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/architecture/SPRINT_2_7_IMPLEMENTATION_PLAN.md) está **altamente alinhado** com a especificação homologada da Etapa 0.8. Ele preserva integralmente a Sprint 2.6 e a Pipeline B, resolve tecnicamente a ambiguidade do Process Launcher no Windows via `ctypes` + `CreateProcessW` + `CREATE_SUSPENDED`, define o Watchdog como subprocesso independente com Mutex Windows, especifica o schema SQLite (`WAL`, `synchronous=FULL`), o ciclo do Outbox durável, o contrato idempotente do Cloud Hub e a estratégia de rollout em modo observacional.

Foram identificados **2 pontos `PARTIAL` de severidade `MEDIUM`** (um sobre isolamento de processo do próprio Watchdog caso `desktop_node.py` rode em Job Object pai, e outro sobre esclarecimento de ACL/breakaway no `CreateJobObjectW`), **nenhum dos quais bloqueia o início da TASK 1** (`desktop_worker/watchdog/job_object.py`).

**Decisão Final:** **GO** — O plano está aprovado e a **TASK 1** pode iniciar com escopo estritamente isolado.

---

## 2. ARQUIVOS AUDITADOS

1. [SPRINT_2_7_IMPLEMENTATION_PLAN.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/architecture/SPRINT_2_7_IMPLEMENTATION_PLAN.md)
2. [SPRINT_2_7_LOCAL_WATCHDOG_DESIGN.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_LOCAL_WATCHDOG_DESIGN.md) *(referenciado no prompt como `SPRINT_2_7_WATCHDOG_LOCAL_DESIGN.md`)*
3. [SPRINT_2_7_WATCHDOG_IMPLEMENTATION_READINESS.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_WATCHDOG_IMPLEMENTATION_READINESS.md)
4. [SPRINT_2_7_WATCHDOG_FAILURE_MATRIX.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_WATCHDOG_FAILURE_MATRIX.md)
5. [SPRINT_2_7_PLAYWRIGHT_TERMINATION_PROTOCOL.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_PLAYWRIGHT_TERMINATION_PROTOCOL.md)
6. [SPRINT_2_7_UPLOAD_TERMINATION_EVIDENCE.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_UPLOAD_TERMINATION_EVIDENCE.md)

---

## 3. MATRIZ DE CONFORMIDADE

| Requisito Auditado | Classificação | Referência no Plano / Etapa 0.8 |
|---|---|---|
| Consistência com Etapa 0.8 e preservação da Sprint 2.6 / Pipeline B | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §1.2 |
| Não introduz garantias de *exactly-once* | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §2.2 (G), §8.2; `READINESS.md` §3 |
| Não permite retry baseado apenas em timeout, heartbeat ou ausência de PID | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §8.2 |
| Exige simultaneamente evidência + `CONFIRMED_ABSENT` + `attempt_id` + `fencing_token` | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §8.2; `EVIDENCE.md` §4 |
| Trata `INCONCLUSIVE` como bloqueio automático | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §8.2 |
| Não aceita `REQUESTED`, `OS_SIGNAL_SENT` ou `MAIN_PROCESS_EXITED` | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §8.2; `EVIDENCE.md` §2 |
| Trata falha de persistência como bloqueio | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §3.2 (6), §6.2 |
| Trata corrupção SQLite/Outbox como `INCONCLUSIVE` | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §6.2; `EVIDENCE.md` §3.1 |
| Impede que evidência/watchdog antigo altere attempt atual | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §7.3 (`409`), §8.2 |
| Windows Process Launcher (`ctypes` + `CreateProcessW` + `CREATE_SUSPENDED` + `IsProcessInJob` + `ResumeThread`) | **PARTIAL** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §3.2 (detalhes de ACL e breakaway) |
| Watchdog Subprocesso Independente (Mutex, sobrevivência a crash do `desktop_node.py`) | **PARTIAL** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §4 (lançamento desacoplado de Job pai) |
| State Store SQLite (`WAL`, `synchronous=FULL`, SHA-256, schema completo) | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §5 |
| Outbox Durável (idempotência `event_id`, quarentena, retenção até ACK) | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §6 |
| API Cloud Hub (`POST /worker/upload-termination-evidence`, `UPDATE` atômico) | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §7 |
| Migrations aditivas, compatibilidade SQLite/PostgreSQL, rollback sem perda | **PASS** | `SPRINT_2_7_IMPLEMENTATION_PLAN.md` §9 |

---

## 4. PROBLEMAS IDENTIFICADOS

1. **P-01 (`PARTIAL` — Process Launcher / Breakaway e ACL):**
   - Na §3.2 item 10 do plano, menciona-se tentar `CREATE_BREAKAWAY_FROM_JOB` caso o processo pai esteja em um Job restrito. Embora se refira a escapar do Job *pai*, é necessário deixar explícito na implementação que o **novo** Job Object (`DCF_UPLOAD_*`) **nunca** deve definir `JOB_OBJECT_LIMIT_BREAKAWAY_OK` nem `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` (conforme `SPRINT_2_7_PLAYWRIGHT_TERMINATION_PROTOCOL.md` §2.1).
   - Além disso, o diagrama da §3.2 mostra `CreateJobObjectW(lpJobAttributes=None, ...)`. Para a TASK 1, o DACL padrão da sessão do usuário é funcional, mas a restrição de ACL ao SID atual deve ser considerada como hardening opcional.
2. **P-02 (`PARTIAL` — Sobrevivência do Watchdog ao encerramento do `desktop_node.py`):**
   - A §4 define o Watchdog como subprocesso independente, mas não explicita as flags de criação (`DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP` ou inicialização via `RUN_FACTORY.bat`) para garantir que, se `desktop_node.py` rodar dentro de um Job Object de terminal/IDE, o encerramento do `desktop_node.py` não mate o Watchdog junto.

---

## 5. RISCOS TÉCNICOS

- **Processos presos em I/O de kernel:** `TerminateJobObject` pode não encerrar imediatamente processos em D-state/kernel I/O; o protocolo trata corretamente como `INCONCLUSIVE`.
- **Aninhamento de Job Objects:** Ambientes Windows antigos (< Windows 8) não suportam *nested jobs*; a falha em `AssignProcessToJobObject` abortará o lançamento antes de `ResumeThread`, evitando execução desprotegida.
- **Calibração de Timeouts:** Timeouts operacionais dependem de medição em staging (`TASK 10`).

---

## 6. BLOQUEADORES

| ID | Problema | Severidade | Evidência | Correção necessária | Bloqueia TASK 1? |
|---|---|---|---|---|---|
| **BLK-01** | Esclarecimento de que `JOB_OBJECT_LIMIT_BREAKAWAY_OK` e `SILENT_BREAKAWAY_OK` nunca devem ser habilitados no Job `DCF_UPLOAD_*` | **MEDIUM** | `IMPLEMENTATION_PLAN.md` §3.2 (10) vs `PROTOCOL.md` §2.1 | Garantir na implementação de `job_object.py` que apenas `KILL_ON_JOB_CLOSE` (e limites seguros) sejam configurados. | **Não** |
| **BLK-02** | Isolamento do processo Watchdog em relação ao Job pai do `desktop_node.py` | **MEDIUM** | `IMPLEMENTATION_PLAN.md` §4 | Na **TASK 5**, iniciar o Watchdog com `DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP` ou via script supervisor. | **Não** |

---

## 7. ESCOPO SEGURO DA TASK 1

- **Arquivo único de produção:** `desktop_worker/watchdog/job_object.py`
- **Testes:** `tests/unit/test_win_job_object.py` (mocks) e `tests/integration/test_win_job_object_integration.py` (Windows real, se disponível).
- **Proibido na TASK 1:** Alterar `desktop_node.py`, uploader, Cloud Hub, migrations, State Store, Outbox, Sprint 2.6 ou Pipeline B; proibido executar uploads reais ou iniciar Playwright.

---

## 8. CRITÉRIOS DE ACEITE DA TASK 1

- Criação de Windows Job Object via `ctypes` (`CreateJobObjectW`) com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` e sem flags de breakaway.
- Lançamento de processo de teste suspenso (`CreateProcessW` + `CREATE_SUSPENDED`), associação (`AssignProcessToJobObject`), validação (`IsProcessInJob`), retomada (`ResumeThread`) e aborto seguro (`TerminateProcess`) em caso de falha.
- Consulta de `JobObjectBasicAccountingInformation` (`ActiveProcesses`) e `JobObjectBasicProcessIdList` com detecção explícita de truncamento (`ERROR_MORE_DATA` ou `NumberOfAssignedProcesses > NumberOfProcessIdsInList`).
- Encerramento via `TerminateJobObject` e verificação de término (`ActiveProcesses == 0` e lista de PIDs vazia).
- Fechamento seguro de todos os handles (`CloseHandle`) em blocos `try ... finally`.
- Suíte de testes unitários (com mocks) e de integração Windows passando sem tocar em código de upload.

---

## 9. DECISÃO FINAL

### **GO**
O plano técnico está aprovado e consistente com a Etapa 0.8. A **TASK 1** (`desktop_worker/watchdog/job_object.py`) pode começar, observando as recomendações dos itens `BLK-01` e `BLK-02` sem bloqueio.

---

## 10. PRÓXIMO PASSO RECOMENDADO

Autorizar o início da **TASK 1** para implementar e testar isoladamente `desktop_worker/watchdog/job_object.py` no Windows.
