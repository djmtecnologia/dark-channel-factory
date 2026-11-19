# Sprint 2.7 — Task 1.7.2: Análise da Falha Unitária `test_10_terminate_process_failure_during_abort`

## 1. Resumo Executivo

- **Teste:** `tests/unit/test_win_job_object.py::test_10_terminate_process_failure_during_abort`
- **Resultado observado:** `AssertionError: assert True is False` em `assert res.error.aborted_cleanly is False`
- **Status do repositório:** Arquivos da Task 1.7.2 (`desktop_worker/watchdog/job_object.py`, `tests/unit/test_win_job_object.py`, `tests/integration/test_win_job_object_integration.py`) estão não rastreados (`??`) no git.

---

## 2. Sequência de Execução Reconstruída

No teste `test_10_terminate_process_failure_during_abort` (`tests/unit/test_win_job_object.py:325-334`):

```python
api = FakeWin32Api()
api.assign_ok = False
api.terminate_proc_ok = False
mgr = JobObjectProcessManager(api=api)
res = mgr.launch("python.exe")
```

1. `mgr.launch("python.exe")` (`desktop_worker/watchdog/job_object.py:765`):
   - `create_job_object(None)` retorna `1001`.
   - `set_information_job_object(...)` retorna `True`.
   - `create_process_suspended(...)` retorna `PROCESS_INFORMATION` com `hProcess = 2001`, `hThread = 3001`.
   - `assign_process_to_job_object(1001, 2001)` retorna `False` (porque `api.assign_ok = False`).
2. `launch()` chama `self._safe_abort(job_handle=1001, process_handle=2001, thread_handle=3001)` (`desktop_worker/watchdog/job_object.py:848`).
3. Em `_safe_abort()` (`desktop_worker/watchdog/job_object.py:735-763`):
   - `aborted_cleanly` inicia como `True`.
   - `terminated = self._api.terminate_process(2001, 1)` retorna `False` (`api.terminate_proc_ok = False`).
   - `wait_timeout = 3000 if terminated else 0` -> `wait_timeout = 0`.
   - `wait_res = self._api.wait_for_single_object(2001, 0)`.
   - No `FakeWin32Api` (`tests/unit/test_win_job_object.py:150-152`), `wait_for_single_object` retorna `self.wait_result`, cujo valor padrão é `WAIT_OBJECT_0` (`0`), independentemente de `terminate_proc_ok` ser `False`.
   - A condição `if wait_res != WAIT_OBJECT_0:` é avaliada como `False`.
   - Os handles `(3001, 2001, 1001)` são fechados com sucesso (`close_handle` retorna `True`).
   - `_safe_abort()` retorna `True`.
4. `launch()` retorna `LaunchResult(ok=False, error=JobObjectError(..., aborted_cleanly=True))`.
5. O teste falha em `assert res.error.aborted_cleanly is False`.

---

## 3. Verificação do Contrato de Segurança

1. **Falha de `TerminateProcess`:** Em `_safe_abort()`, quando `terminate_process` retorna `False`, `aborted_cleanly` não é marcado como `False` diretamente; o código ainda consulta `wait_for_single_object(process_handle, 0)` para verificar se o processo já havia encerrado.
2. **Uso de `TerminateJobObject`:** `_safe_abort()` não utiliza `TerminateJobObject` (o processo pode nem ter sido associado ao Job, como neste caso em que `assign_process_to_job_object` falhou).
3. **Consulta de processos ativos:** Não há consulta de `BasicAccountingInformation` no `_safe_abort()`; a verificação é apenas via `WaitForSingleObject` no `process_handle`.
4. **Comportamento do Fake vs Win32:**
   - Na API Win32 real, se `TerminateProcess` falha porque o processo ainda está vivo e sem acesso, `WaitForSingleObject(hProcess, 0)` retorna `WAIT_TIMEOUT` (`258`), fazendo `aborted_cleanly = False`.
   - No `FakeWin32Api`, `wait_result` tem valor padrão `WAIT_OBJECT_0` (`0`) mesmo quando `terminate_proc_ok = False`. Assim, o fake simula que o processo terminou instantaneamente apesar da falha de `TerminateProcess`.

---

## 4. Causa Raiz

A causa raiz é dupla (interação entre `_safe_abort` e `FakeWin32Api`):

1. **No mock (`FakeWin32Api`):** `wait_for_single_object` retorna `WAIT_OBJECT_0` por padrão mesmo quando `terminate_proc_ok = False`, fazendo o teste simular um processo que já está sinalizado como encerrado.
2. **Na implementação (`_safe_abort`):** Quando `terminate_process` retorna `False`, o código não marca `aborted_cleanly = False` se `wait_for_single_object(process_handle, 0)` retornar `WAIT_OBJECT_0`.

---

## 5. Correção Mínima Recomendada

- **No `FakeWin32Api` (`tests/unit/test_win_job_object.py`):** Fazer `wait_for_single_object` retornar `WAIT_TIMEOUT` quando `terminate_proc_ok` for `False` (a menos que `wait_result` tenha sido explicitamente sobrescrito), refletindo o comportamento real da Win32 quando `TerminateProcess` falha em um processo ainda ativo.
- **Opcionalmente em `_safe_abort` (`desktop_worker/watchdog/job_object.py`):** Avaliar se `if not terminated:` já deve definir `aborted_cleanly = False` (ou manter o check de `WaitForSingleObject(..., 0)` apenas para o caso de processo que já havia encerrado antes da chamada).

> Nenhuma alteração foi aplicada nesta execução. Aguardando autorização para implementar a correção.
