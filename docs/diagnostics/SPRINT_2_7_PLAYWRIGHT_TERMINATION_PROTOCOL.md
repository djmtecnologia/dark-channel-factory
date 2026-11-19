# SPRINT 2.7 — PROTOCOLO DE ENCERRAMENTO SEGURO DO PLAYWRIGHT (ETAPA 0.8 — ESPECIFICAÇÃO TÉCNICA CONSOLIDADA)

**Status:** Especificação Técnica Operacional (Somente Análise e Documentação)  
**Escopo:** Árvore de processos de upload no Windows (`desktop_node.py` -> Python Uploader -> Playwright -> Chromium/Edge)

---

## 1. ANATOMIA DA ÁRVORE DE PROCESSOS NO WINDOWS

Quando o Desktop Node executa um upload via Playwright, a árvore de processos tem a seguinte estrutura:

1. **Processo Principal do Desktop Node (`desktop_node.py`):** Deve permanecer **intacto** e isolado.
2. **Subprocesso Python Uploader:** Processo filho lançado especificamente para a tentativa (`attempt_id`).
3. **Playwright Driver (`node.exe`):** Processo Node.js gerenciado pela biblioteca Python do Playwright.
4. **Navegador (Chromium / Edge):**
   - Processo principal do navegador (`chrome.exe` / `msedge.exe` / `chrome-headless-shell.exe`).
   - Processos filhos: GPU, Network Service, Renderer, Utility, Crashpad.
5. **Contextos e Sessões:** Cada tentativa deve usar um `user_data_dir` exclusivo (ou contexto isolado) para evitar compartilhamento de sessão/lock de perfil com outras execuções ou navegadores pessoais do usuário.

---

## 2. MECANISMO DE CONTROLE NO WINDOWS (T3)

### 2.1. Mecanismo Primário Recomendado: Windows Job Objects
O uso de **Windows Job Objects** (via `pywin32` / `ctypes`: `CreateJobObjectW`, `SetInformationJobObject`, `AssignProcessToJobObject`, `IsProcessInJob`, `TerminateJobObject`, `QueryInformationJobObject`) é o mecanismo mais confiável no Windows porque:
- Agrupa o subprocesso Python Uploader e **todos** os seus descendentes (Playwright `node.exe`, Chromium/Edge e filhos) automaticamente, mesmo que um processo pai morra antes dos filhos.
- Permite consultar a contabilidade de processos ativos e a lista de PIDs dentro do Job Object.
- Permite encerrar toda a árvore atomicamente com `TerminateJobObject` quando o encerramento gracioso falha.

#### Eliminação da Janela de Execução Anterior à Associação (T2)
Para impedir que o subprocesso Python crie processos filhos antes de estar associado ao Job Object, a criação deve seguir obrigatoriamente esta sequência:
1. **Criar o Windows Job Object** (`CreateJobObjectW`) com nome único não previsível (`Local\DCF_UPLOAD_<attempt_id>_<execution_id>`) e ACL restrita ao SID do usuário/serviço atual.
2. **Configurar limites e políticas** (`SetInformationJobObject` com `JobObjectExtendedLimitInformation`):
   - **Não** definir `JOB_OBJECT_LIMIT_BREAKAWAY_OK` nem `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`, impedindo que processos filhos escapem via `CREATE_BREAKAWAY_FROM_JOB`.
3. **Criar o subprocesso Python suspenso** usando `CREATE_SUSPENDED` em `CreateProcessW` (ou `subprocess.Popen(..., creationflags=subprocess.CREATE_SUSPENDED)`).
4. **Associar o processo ao Job Object** via `AssignProcessToJobObject(job_handle, process_handle)`.
5. **Confirmar a associação** via `IsProcessInJob(process_handle, job_handle)`.
6. **Se `AssignProcessToJobObject` ou `IsProcessInJob` falhar:** encerrar imediatamente o processo suspenso (`TerminateProcess`), fechar os handles e **não** retomar a execução (resultado `FAILED_PRE_UPLOAD` / `INCONCLUSIVE`, sem iniciar Playwright).
7. **Retomar a execução** (`ResumeThread(thread_handle)`) somente após confirmação positiva de `IsProcessInJob`.
8. Somente após a retomada, o subprocesso uploader inicializa o Playwright.

#### Análise de Compatibilidade e Jobs Aninhados
- **Windows 8 / Server 2012 e superiores:** Suportam *nested jobs* (jobs aninhados). Se `desktop_node.py` já rodar dentro de um Job Object (ex.: gerenciador de serviço, IDE ou container), `AssignProcessToJobObject` funciona normalmente, desde que o Job pai não imponha limites conflitantes.
- **Versões anteriores ao Windows 8:** Não suportam *nested jobs*; se o processo pai já estiver em um Job Object sem `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`, `AssignProcessToJobObject` falhará com `ERROR_ACCESS_DENIED`.
- **Regra:** Nunca presumir que a associação terá sucesso em qualquer ambiente; a falha explícita na etapa 4/5 aborta o upload antes do início do Playwright.

### 2.2. Mecanismo Secundário / Complementar: Rastreamento de Árvore + Marcador
Caso o Job Object não esteja disponível ou para dupla verificação:
- Injetar variável de ambiente exclusiva `DCF_EXECUTION_ID=<execution_id>` e `DCF_ATTEMPT_ID=<attempt_id>` no subprocesso Python Uploader (herdada pelos filhos).
- Rastrear `(pid, process_create_time)` do processo Python e de seus descendentes diretos antes do encerramento.

---

## 3. PROCEDIMENTO DE ENCERRAMENTO EM FASES

### Fase 1 — Solicitação de Encerramento Normal (Gracioso)
1. Marcar a execução localmente como `TERMINATING` e registrar `termination_requested_at`.
2. Se houver canal de controle (ex: evento/pipe/sinal para o subprocesso Python), solicitar encerramento limpo (`page.close()`, `context.close()`, `browser.close()`).
3. Aguardar até `TERMINATION_GRACEFUL_TIMEOUT_SEC`.
4. **Importante:** Uma chamada de encerramento bem-sucedida **não** é prova de término. Prosseguir obrigatoriamente para a verificação.

### Fase 2 — Encerramento Forçado (Quando Gracioso Falha ou Timeout Expira)
Admitido quando:
- O timeout gracioso expira e ainda existem processos no Job Object / árvore; **ou**
- O processo não responde; **ou**
- O Cloud Hub já revogou o lease (`409` / `fencing_token` obsoleto) e há risco iminente de publicação duplicada.

Procedimento:
1. Executar `TerminateJobObject(job_handle, exit_code)` no Job Object da tentativa.
2. Como fallback adicional (se necessário), encerrar individualmente os PIDs validados por `(pid, process_create_time)` na ordem folhas -> raiz.

### Fase 3 — Verificação da Árvore de Processos (T1)
A verificação do Job Object deve utilizar **duas** classes de consulta distintas de `QueryInformationJobObject`:
1. **`JobObjectBasicAccountingInformation`** (`JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`): fornece `ActiveProcesses` (contagem total de processos ativos atualmente no Job Object).
2. **`JobObjectBasicProcessIdList`** (`JOBOBJECT_BASIC_PROCESS_ID_LIST`): fornece `NumberOfAssignedProcesses`, `NumberOfProcessIdsInList` e `ProcessIdList`. Se a chamada retornar `ERROR_MORE_DATA` ou `NumberOfAssignedProcesses > NumberOfProcessIdsInList`, o buffer é insuficiente (lista truncada) e a consulta **não** pode ser considerada completa.

**Critério Conjunto Obrigatório para `TREE_TERMINATED`:**
1. `ActiveProcesses == 0` (via `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`).
2. `NumberOfProcessIdsInList == 0` **e** `NumberOfAssignedProcesses == 0` (via `JOBOBJECT_BASIC_PROCESS_ID_LIST`, sem truncamento).
3. Nenhum dos `(pid, process_create_time)` registrados para a execução permanece ativo no sistema operacional.

Se qualquer consulta falhar, retornar buffer truncado ou produzir resultado divergente, **não** declarar `TREE_TERMINATED` — o resultado é `INCONCLUSIVE`.

---

## 4. TRATAMENTO DE CASOS ESPECIAIS

| Caso | Comportamento do Protocolo | Resultado |
|---|---|---|
| **Processo não responde** | Após `TERMINATION_GRACEFUL_TIMEOUT_SEC`, escalar para `TerminateJobObject`. | Se `ActiveProcesses == 0` após verificação -> `TREE_TERMINATED`. Se persistir -> `INCONCLUSIVE`. |
| **Processo desaparece durante a verificação** | Validar se o PID desapareceu **e** se não há outros processos no Job Object. Se o Job Object estiver vazio, confirmar encerramento. | `TREE_TERMINATED` (se Job Object vazio) ou `INCONCLUSIVE` (se sem Job Object e sem confirmação dos descendentes). |
| **Processo principal encerrado, navegador ainda ativo** | O Job Object mantém os processos do navegador agrupados mesmo após a morte do Python. `TerminateJobObject` encerra os órfãos. | `TREE_TERMINATED` após verificação do Job Object. |
| **Navegador encerrado, Python ainda ativo** | `TerminateJobObject` ou encerramento do Python garante que não haja nova tentativa interna. | `TREE_TERMINATED` após verificação. |
| **Sem mecanismo confiável de identificação** | Se não há Job Object nem `(pid, create_time)` válido (ex: falha ao gravar antes do crash), **não** adivinhar nem matar processos genéricos. | **`INCONCLUSIVE`** — proibido liberar retry. |

---

## 5. REGRA DE OURO

Se a verificação não puder comprovar que **toda a árvore relevante** foi encerrada ou isolada (ex: acesso negado pelo SO, processo preso em kernel I/O, perda de rastreamento sem reboot do host), o resultado deve ser marcado como **`INCONCLUSIVE`** e **não** pode gerar evidência válida para transição para `TERMINATED`.
