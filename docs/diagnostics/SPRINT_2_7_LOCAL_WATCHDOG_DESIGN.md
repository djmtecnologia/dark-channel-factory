# SPRINT 2.7 — DESIGN DO WATCHDOG LOCAL (ETAPA 0.8 — ESPECIFICAÇÃO TÉCNICA CONSOLIDADA)

**Status:** Especificação Técnica Operacional (Somente Análise e Documentação)  
**Escopo:** Desktop Node (`desktop_node.py`, `desktop_worker/`) no Windows  
**Pré-requisito:** [SPRINT_2_7_UPLOAD_RECOVERY_FINAL.md](file:///c:/Users/User/.gemini/antigravity/scratch/dark-channel-factory/docs/diagnostics/SPRINT_2_7_UPLOAD_RECOVERY_FINAL.md)

---

## 1. OBJETIVO E PREMISSAS

O Watchdog Local tem como objetivo monitorar execuções de upload via Playwright no Desktop Node (Windows), detectar execuções abandonadas ou desconectadas, encerrar com segurança apenas a árvore de processos pertencente à tentativa específica, verificar o encerramento e emitir evidência durável para o Cloud Hub.

**Premissas fundamentais:**
- Não existe garantia de *exactly-once* para publicação externa no YouTube.
- Perda de heartbeat **não** significa que o processo Playwright terminou.
- Novo `fencing_token` no Cloud Hub **não** impede que um processo Playwright antigo continue rodando localmente.
- Sem comprovação de encerramento/isolamento da tentativa anterior (ou intervenção humana explícita), o job permanece em `UPLOAD_UNKNOWN`.

---

## 2. ARQUITETURA PROPOSTA

```
+-------------------------------------------------------------------+
|                         DESKTOP NODE (Windows)                    |
|                                                                   |
|  +------------------------+       +----------------------------+  |
|  |   desktop_node.py      |       |   Local Watchdog Service   |  |
|  |   upload_worker_loop() |       |   (processo/thread isolado)|  |
|  +-----------+------------+       +-------------+--------------+  |
|              |                                  |                 |
|              | 1. Registra execução             | 2. Monitora     |
|              v                                  v                 |
|  +-------------------------------------------------------------+  |
|  |        Estado Local Durável (SQLite / JSON WAL local)       |  |
|  |  - execution_id, attempt_id, job_id, fencing_token          |  |
|  |  - pid, process_create_time, job_object_name                |  |
|  |  - last_heartbeat_at, status, termination_evidence          |  |
|  +-------------------------------------------------------------+  |
|              |                                  |                 |
|              | 3. Lança em Windows Job Object   | 4. Encerra/     |
|              v                                  v    Verifica     |
|  +-------------------------------------------------------------+  |
|  |   Windows Job Object: DCF_UPLOAD_<attempt_id>               |  |
|  |   - Processo Python Uploader (subprocesso isolado)          |  |
|  |   - Playwright Driver (node.exe)                            |  |
|  |   - Navegador Chromium / Edge + processos filhos            |  |
|  +-------------------------------------------------------------+  |
+-------------------------------------------------------------------+
                                  |
                                  | 5. Envio idempotente (Outbox)
                                  v
+-------------------------------------------------------------------+
|                            CLOUD HUB                              |
|  - Valida attempt_id + fencing_token                              |
|  - Registra evidência de encerramento                             |
|  - Avalia: CONFIRMED_ABSENT + TERMINATED_EVIDENCE -> Retry        |
+-------------------------------------------------------------------+
```

---

## 3. IDENTIDADE INEQUÍVOCA DA EXECUÇÃO (T1)

A identificação **não pode depender exclusivamente do PID**, pois o Windows reutiliza PIDs rapidamente após o término de um processo.

### 3.1. Tupla Canônica de Identidade

Cada tentativa de upload deve possuir uma identidade composta persistida localmente antes do início do Playwright:

| Campo | Origem | Finalidade |
|---|---|---|
| `execution_id` | UUIDv4 gerado localmente no Desktop Node | Identificador único da execução local |
| `attempt_id` | UUID gerado pelo Cloud Hub (`UploadAttempt`) | Identificador único da tentativa no Hub |
| `job_id` | Cloud Hub (`VideoJob.id`) | Job associado |
| `tenant_id` | Cloud Hub (`Tenant.id`) | Isolamento multi-tenant |
| `channel_id` | Cloud Hub (`Channel.id`) | Canal de destino |
| `worker_id` | Configuração do Desktop Node | Nó executor |
| `fencing_token` | Cloud Hub (monotônico por job/tentativa) | Proteção contra *split-brain* e eventos obsoletos |
| `pid` | SO Windows (processo Python uploader) | Identificador operacional temporário |
| `process_create_time` | SO Windows (`GetProcessTimes` / `psutil.Process.create_time()`) | Desambiguação contra reuso de PID |
| `job_object_name` | `DCF_UPLOAD_<attempt_id>_<execution_id>` | Nome único do Windows Job Object |
| `browser_user_data_dir` | Diretório exclusivo da tentativa | Isolamento de sessão/contexto do navegador |

### 3.2. Detecção de Divergência

Ao inspecionar um processo pelo `pid` registrado, o watchdog deve validar:
1. O `pid` ainda existe?
2. O `process_create_time` atual coincide com o `process_create_time` registrado (tolerância mínima de precisão de clock do SO)?
3. O processo pertence ao `job_object_name` esperado (via `IsProcessInJob`) ou possui a linha de comando / variável de ambiente de marcação `DCF_EXECUTION_ID=<execution_id>`?

**Regra de Divergência:** Se o `pid` existir, mas `process_create_time` divergir ou não pertencer ao Job Object / `DCF_EXECUTION_ID`, trata-se de **PID reutilizado pelo Windows**. O watchdog **não** deve tocar nesse processo; deve registrar que o processo original desapareceu sem confirmação de encerramento limpo (ou verificar o Job Object diretamente).

---

## 4. COMPONENTES E RESPONSABILIDADES (T2)

1. **Launcher Isolado de Upload (substituto da chamada direta em thread):**
   - Em vez de `upload_worker_loop()` executar `publish_video_to_youtube()` na mesma thread/processo de `desktop_node.py`, o upload deve rodar em um **subprocesso Python dedicado** associado a um **Windows Job Object** exclusivo.
   - Isso garante que encerrar o upload não encerre o `desktop_node.py` nem outros workers.
2. **Registro Local Durável (Outbox + State Store):**
   - Persiste a tupla de identidade antes de iniciar o Playwright.
   - Armazena heartbeats locais e evidências de encerramento pendentes de envio ao Cloud Hub.
3. **Watchdog Local:**
   - Roda em processo ou serviço supervisor independente da thread de upload (idealmente processo separado ou thread supervisora resiliente no `desktop_node.py`, com capacidade de recuperação no boot).
   - Consome o State Store local, monitora heartbeats, detecta desconexões, executa o protocolo de encerramento e enfileira evidências para o Cloud Hub.

---

## 5. FLUXO OPERACIONAL DO WATCHDOG (T2)

### 5.1. Inicialização, Recuperação no Boot e Acesso ao Job Object (T3, T6)
1. Ao iniciar o Desktop Node (ou serviço watchdog), ler o State Store local.
2. Para qualquer execução em estado `INTENT`, `IN_PROGRESS` ou `TERMINATING`:
   - **Comprovação de Reboot do Windows (T6):**
     - Fonte de `boot_time`: `Win32_OperatingSystem.LastBootUpTime` (ou `psutil.boot_time()`), normalizado para **UTC**.
     - No início da tentativa, o State Store deve gravar `attempt_boot_time_utc` (o `LastBootUpTime` vigente no momento do início) além de `started_at` (UTC).
     - Para comprovar reboot, comparar `current_boot_time_utc > attempt_boot_time_utc` (ambos da mesma fonte `LastBootUpTime`). Isso evita falsos positivos por ajustes manuais ou NTP no relógio do Windows entre `started_at` e `boot_time`. Se `current_boot_time_utc` não puder ser obtido ou houver divergência inexplicada, o resultado é `INCONCLUSIVE`.
     - Se `current_boot_time_utc > attempt_boot_time_utc`, nenhum processo volátil anterior sobreviveu: registrar `EXECUTION_ISOLATED` e enviar ao Cloud Hub.
   - **Recuperação sem Reboot (Acesso ao Job Object via `OpenJobObject`):**
     - Tentar abrir o Job Object nomeado (`Local\DCF_UPLOAD_<attempt_id>_<execution_id>`) via `OpenJobObjectW(JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, False, job_object_name)`.
     - **Job Object existente com processos ativos:** Assumir o controle e executar o protocolo de encerramento seguro.
     - **Job Object existente e vazio** (`ActiveProcesses == 0` e `NumberOfProcessIdsInList == 0`): Contribui para `TREE_TERMINATED`, desde que a ausência dos `(pid, process_create_time)` registrados também seja confirmada.
     - **Job Object inexistente (`ERROR_FILE_NOT_FOUND`):** O Windows destrói um Job Object nomeado quando o último handle é fechado e não há processos ativos; porém, sua ausência isolada **não** comprova encerramento se não houver confirmação de que os `(pid, process_create_time)` não existem mais. Se o processo ainda existir ou não puder ser verificado, resultado `INCONCLUSIVE`.
     - **Job Object inacessível (`ERROR_ACCESS_DENIED`) ou identidade não verificável:** Resultado `INCONCLUSIVE` (proibido retry automático).
   - **Proteção de ACL e Nomes:** O Job Object deve ser criado com `SECURITY_ATTRIBUTES` contendo ACL restrita ao SID do usuário/serviço atual e nome com `execution_id` (UUIDv4) não previsível, impedindo abertura ou interferência por outros usuários locais.

### 5.2. Acompanhamento e Heartbeat
- O subprocesso de upload emite heartbeat local (atualização de `last_heartbeat_at` no State Store) em intervalo configurável (`WATCHDOG_HEARTBEAT_INTERVAL_SEC`).
- O watchdog verifica periodicamente (`WATCHDOG_POLL_INTERVAL_SEC`) todas as execuções ativas.

### 5.3. Diferenciação: Execução Lenta vs. Execução Abandonada
- **Execução Lenta:** O upload de vídeo grande pode demorar, mas o subprocesso continua emitindo heartbeat local (ou reportando progresso de bytes/etapas). O watchdog **não** encerra enquanto o heartbeat estiver dentro de `WATCHDOG_HEARTBEAT_TIMEOUT_SEC` e o `fencing_token` / lease estiver válido.
- **Execução Abandonada / Travada:**
  - `now - last_heartbeat_at > WATCHDOG_HEARTBEAT_TIMEOUT_SEC`, **OU**
  - Cloud Hub revogou o lease / retornou `409 Conflict` / novo `fencing_token` detectado, **OU**
  - Desconexão prolongada do Cloud Hub além de `CLOUD_HUB_DISCONNECT_GRACE_SEC` após expiração do lease.
- **Regra Crítica:** A ausência de heartbeat dispara o **protocolo de encerramento e verificação**, nunca a suposição de que o processo já morreu.

### 5.4. Parâmetros Configuráveis (Sem Valores Definitivos Prematuros)
Todos os tempos devem ser configuráveis por ambiente/configuração após calibração operacional:
- `WATCHDOG_HEARTBEAT_INTERVAL_SEC`: intervalo de gravação do heartbeat local.
- `WATCHDOG_HEARTBEAT_TIMEOUT_SEC`: tempo máximo sem heartbeat antes de considerar travamento.
- `WATCHDOG_POLL_INTERVAL_SEC`: frequência de varredura do watchdog.
- `CLOUD_HUB_DISCONNECT_GRACE_SEC`: tolerância offline antes de abortar upload sem lease.
- `TERMINATION_GRACEFUL_TIMEOUT_SEC`: espera após pedido de encerramento normal antes de forçar.
- `TERMINATION_VERIFY_TIMEOUT_SEC`: tempo máximo para confirmar desaparecimento da árvore de processos.

### 5.5. Proteção Contra Processos de Outras Aplicações
O watchdog **nunca** busca processos genéricos por nome (`chrome.exe`, `msedge.exe`, `node.exe`, `python.exe`). Ele atua **exclusivamente** sobre:
1. O Windows Job Object nomeado `DCF_UPLOAD_<attempt_id>_<execution_id>`, **ou**
2. Processos cuja identidade foi validada por `(pid, process_create_time)` e ancestralidade direta / marcador `DCF_EXECUTION_ID`.
