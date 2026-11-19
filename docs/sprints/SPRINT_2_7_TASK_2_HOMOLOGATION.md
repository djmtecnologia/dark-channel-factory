# SPRINT 2.7 — TASK 2: HOMOLOGAÇÃO (PÓS-AUDITORIA INDEPENDENTE)

**Status:** HOMOLOGADA (PASS)  
**Data:** 2026-11-19  
**Escopo:** Correção exclusiva dos 4 problemas críticos e pontos de atenção apontados pela auditoria independente da Task 2, preservando integralmente a Task 1 homologada e sem iniciar a Task 3.

---

## 1. Problemas Encontrados na Auditoria Independente

1. **Sequência de inicialização do processo (`ResumeThread` antes do `pre_resume_hook`):**
   - `JobObjectProcessManager.launch()` executava `ResumeThread` antes de retornar para `launch_isolated_upload()`, permitindo que o processo de upload executasse antes da persistência exigida pelo `pre_resume_hook`.
2. **Validação inconsistente do Job Object:**
   - O Job Object era criado sem nome (`create_job_object(None)`), enquanto `IdentityValidator` tentava abri-lo por nome (`OpenJobObjectW`), além de `is_process_in_job()` retornar `tuple[bool, bool]` tratado incorretamente como `bool` e de `in_expected_job=None` ser aceito como `VALID`.
3. **Mutação temporária de `os.environ`:**
   - `launch_isolated_upload()` alterava `os.environ` temporariamente antes de chamar `CreateProcessW`, criando risco de race condition em execuções simultâneas.
4. **Incompatibilidade nos modelos de identidade e unidade de tempo:**
   - `CanonicalExecutionIdentity.process_create_time` usava `float` enquanto `ProcessIdentity.process_creation_time` (Task 1) usa `int` (FILETIME 100-ns).
   - `to_process_identity()` passava `job_object_name` (inexistente em `ProcessIdentity`) e `IdentityValidator.verify()` acessava `process_create_time` e `job_object_name` mesmo quando recebia `ProcessIdentity`.

---

## 2. Arquivos Modificados

- `desktop_worker/watchdog/job_object.py` (extensão retrocompatível estritamente necessária para `lpEnvironment`, `pre_resume_hook` antes de `ResumeThread` e assinaturas Win32 de `OpenProcess` / `OpenJobObjectW` com `wintypes.HANDLE`)
- `desktop_worker/watchdog/process_launcher.py`
- `desktop_worker/watchdog/identity.py`
- `tests/unit/test_win_job_object.py` (`FakeWin32Api.create_process_suspended` atualizado para aceitar `env: Optional[Mapping[str, str]] = None`)
- `tests/unit/test_watchdog_identity.py`
- `tests/integration/test_win_process_launcher.py`
- `docs/sprints/SPRINT_2_7_TASK_2_HOMOLOGATION.md`

---

## 3. Decisões Arquiteturais

1. **Hook pré-retomada dentro do `JobObjectProcessManager.launch()`:**
   - Adicionado parâmetro opcional `pre_resume_hook: Optional[Callable[[ProcessIdentity], None]] = None` em `JobObjectProcessManager.launch()`, executado após `confirm_identity` (etapa 6) e antes de `resume_thread` (etapa 8). Falhas no hook acionam `_safe_abort(...)` ainda com a thread suspensa.
2. **Validação estrita de Job Object via handle real:**
   - `IdentityValidator.verify()` aceita `job_handle: Optional[int] = None` (ou usa `expected.job_handle` de `ProcessIdentity`, ou `OpenJobObjectW` quando nomeado). Se nenhum handle válido estiver disponível ou `IsProcessInJob` falhar, retorna `IdentityStatus.UNVERIFIABLE` com `in_expected_job=None` — nunca `VALID`.
3. **Isolamento de ambiente via `lpEnvironment` + `CREATE_UNICODE_ENVIRONMENT`:**
   - `Win32Api.create_process_suspended()` aceita `env: Optional[Mapping[str, str]] = None`, constrói o bloco Unicode terminado em duplo `\0` e define `CREATE_UNICODE_ENVIRONMENT` (`0x00000400`), eliminando qualquer mutação de `os.environ`.
4. **Padronização de FILETIME inteiro:**
   - `CanonicalExecutionIdentity.process_create_time` e `IdentityVerificationResult` padronizados para `int` (Win32 FILETIME em intervalos de 100 ns desde 1601-01-01 UTC), alinhados com `ProcessIdentity.process_creation_time` e `GetProcessTimes`.

---

## 4. Evidências de Cada Correção

- **Sequência pré-retomada:** `test_resume_thread_called_only_after_pre_resume_hook_succeeds` comprova que `resume_thread` só é chamado após o retorno bem-sucedido do `pre_resume_hook`; `test_pre_resume_hook_failure_aborts_suspended_process_before_resume` comprova que falha no hook aborta o processo suspenso via `terminate_process` sem chamar `resume_thread`.
- **Job Object obrigatório:** `test_unverifiable_when_job_association_cannot_be_verified` e `test_wi01_launch_isolated_upload_and_validate_identity` comprovam que sem `job_handle` o resultado é `UNVERIFIABLE` (`is_valid=False`, `in_expected_job=None`), e com `job_handle` real o resultado é `VALID` com `in_expected_job is True`.
- **Ambiente isolado:** `test_launch_isolated_upload_does_not_mutate_os_environ` e `test_wi02_isolated_environment_blocks_without_os_environ_mutation` comprovam que `os.environ` permanece intacto e dois subprocessos reais recebem valores distintos de `DCF_EXECUTION_ID`.
- **Modelos de identidade:** `test_process_identity_and_to_process_identity_compatibility` comprova `CanonicalExecutionIdentity.to_process_identity(job_handle=...)` e `IdentityValidator.verify(ProcessIdentity)`.

---

## 5. Testes Executados

Comando executado após a última alteração de código:

```powershell
pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v
```

Resultado real:
- **69 passed in 0.92s** (0 failed, 0 skipped)
  - `tests/unit/test_win_job_object.py` (Task 1 unitários): 50 passed, 0 failed, 0 skipped
  - `tests/integration/test_win_job_object_integration.py` (Task 1 integração): 2 passed, 0 failed, 0 skipped
  - `tests/unit/test_watchdog_identity.py` (Task 2 unitários): 15 passed, 0 failed, 0 skipped
  - `tests/integration/test_win_process_launcher.py` (Task 2 integração): 2 passed, 0 failed, 0 skipped (`test_wi01_launch_isolated_upload_and_validate_identity`, `test_wi02_isolated_environment_blocks_without_os_environ_mutation`)

---

## 6. Resultado Final

**APROVADO (PASS)** — Todos os 4 problemas críticos da auditoria independente foram corrigidos e comprovados por testes unitários e de integração reais no Windows.

---

## 7. Riscos Residuais

- Para Job Objects anônimos (`create_job_object(None)`), o chamador que realiza a verificação (`IdentityValidator.verify`) deve manter e fornecer o `job_handle` do `ManagedJobProcess` ativo; após o fechamento do handle (ou em processos externos sem handle), a verificação retorna `UNVERIFIABLE` por design seguro.

## 8. Correções Pós-Auditoria Independente

1. **`ManagedJobProcess.close()` (`desktop_worker/watchdog/job_object.py`)**:
   - Passou a verificar explicitamente o retorno booleano de `close_handle()`.
   - Quando `close_handle()` retorna `False`, preserva a referência do handle, captura e registra o erro Win32 via `logger.warning`, e mantém `_closed = False` para permitir nova tentativa sem fechar novamente handles já encerrados.
   - Bloco de ambiente Unicode em `create_process_suspended` ordenado de forma case-insensitive (`key=lambda kv: kv[0].casefold()`).

2. **Identidade de Job Object Anônimo (`desktop_worker/watchdog/identity.py`, `desktop_worker/watchdog/process_launcher.py`)**:
   - Removido o fallback `_check_process_in_named_job` baseado em `job_object_name` sintético; sem `job_handle` real, `IdentityValidator.verify()` retorna `UNVERIFIABLE`.
   - Removida a injeção de `DCF_JOB_OBJECT_NAME` em `build_upload_environment()`.

3. **Regressão Pós-Correções**:
   - Comando: `pytest tests/unit/test_win_job_object.py tests/integration/test_win_job_object_integration.py tests/unit/test_watchdog_identity.py tests/integration/test_win_process_launcher.py -v`
   - Resultado: **69 passed in 0.92s** (0 falhas, 0 ignorados).

## 9. Revisão Final de Consistência (`job_object_name` / `CanonicalExecutionIdentity`)

- **Decisão sobre `job_object_name`:** Mantido como campo obrigatório no contrato de 11 campos da `CanonicalExecutionIdentity` (Etapa 0.8), preservando compatibilidade com os testes e com o contrato arquitetural.
- **Semântica explícita:** Documentado em `CanonicalExecutionIdentity` (`desktop_worker/watchdog/identity.py`) e em `AttemptLaunchContext.resolved_job_object_name()` (`desktop_worker/watchdog/process_launcher.py`) que, como o Job Object é criado de forma anônima (`CreateJobObjectW(None)`), `job_object_name` é estritamente um **identificador lógico** para correlação/observabilidade, **não** um nome real de objeto kernel Win32, sem capacidade de abrir (`OpenJobObjectW`) ou validar um Job Object.
- **Validação:** `IdentityValidator.verify()` permanece validando a associação ao Job Object exclusivamente pelo `job_handle` real, e `DCF_JOB_OBJECT_NAME` não foi reintroduzido.
