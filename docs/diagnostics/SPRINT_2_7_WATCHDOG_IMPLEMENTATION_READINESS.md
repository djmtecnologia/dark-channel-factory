# SPRINT 2.7 — PRONTIDÃO PARA IMPLEMENTAÇÃO DO WATCHDOG (ETAPA 0.8 — ESPECIFICAÇÃO TÉCNICA CONSOLIDADA)

**Status:** Especificação Técnica Operacional (Somente Análise e Documentação)  
**Escopo:** Análise de Impacto, Compatibilidade, Pendências e Decisão GO/NO-GO Documental

---

## 1. ANÁLISE DE IMPACTO

| Componente | Impacto Estimado | Risco | Observações de Compatibilidade |
|---|---|---|---|
| `desktop_node.py` | **ALTO** — `upload_worker_loop()` (L74) passa a lançar subprocesso Python isolado em Windows Job Object em vez de rodar `publish_video_to_youtube()` em thread direta. | Médio | Preserva `render_video()` e todo o fluxo de renderização. |
| `desktop_worker/` | **MÉDIO** — Inclusão do módulo de watchdog local, State Store (Outbox) e wrapper de Job Object Windows. | Médio | Isolado do fluxo de geração de vídeo/áudio. |
| `cloud_hub/main.py` | **MÉDIO** — Novo endpoint idempotente para receber evidência de encerramento e validar `attempt_id` + `fencing_token` antes de liberar retry. | Médio | Deve manter compatibilidade com workers antigos durante transição (rejeitando retry automático se worker não enviar evidência). |
| `cloud_hub/models.py` | **BAIXO/MÉDIO** — Adição de campos/tabela para `UploadAttempt` e registro de evidência de encerramento. | Baixo | Colunas aditivas (`nullable=True` ou `default`) compatíveis com SQLite e PostgreSQL/Neon. |
| Mecanismo de upload existente (`playwright_uploader.py`) | **BAIXO** — Recebe `user_data_dir` exclusivo por tentativa e emite heartbeat local. | Baixo | Sem alteração na lógica de automação do YouTube Studio. |
| Persistência local | **NOVO** — Criação de State Store/Outbox local (SQLite ou JSON atômico) no Desktop Node. | Baixo | Requer controle de permissões e limpeza periódica de registros antigos. |
| Comunicação Desktop Node ↔ Cloud Hub | **MÉDIO** — Novo fluxo assíncrono de Outbox para evidências de encerramento. | Médio | Requer versionamento de protocolo (`watchdog_version`). |
| Banco SQLite / PostgreSQL (Neon) / Migrations | **MÉDIO** — Necessidade de migration aditiva para `UploadAttempt` e tabela de evidências. | Médio | Testar migration em SQLite e PostgreSQL antes de produção. |
| Logs e Observabilidade | **BAIXO** — Logs estruturados com `execution_id`, `attempt_id`, `fencing_token` e `verification_result`. | Baixo | Facilita auditoria de incidentes. |

### 1.1. Compatibilidade Retroativa e Implantação Coordenada
- **Risco de versões mistas:** Um Desktop Node antigo (sem watchdog) não enviará evidências de encerramento. Nesse caso, o Cloud Hub **não** deve liberar retry automático após `UPLOAD_UNKNOWN` — o comportamento seguro padrão é manter em `UPLOAD_UNKNOWN` exigindo intervenção humana.
- **Ordem recomendada de implantação:** 1) Migrations e endpoints no Cloud Hub; 2) Atualização dos Desktop Nodes com watchdog.

---

## 2. CRITÉRIOS DE ACEITAÇÃO DOCUMENTAL (ETAPA 0.8)

- [x] Consulta de `ActiveProcesses` usa `JobObjectBasicAccountingInformation` (`JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`).
- [x] Lista de PIDs usa `JobObjectBasicProcessIdList` (`JOBOBJECT_BASIC_PROCESS_ID_LIST`) com validação contra truncamento (`NumberOfAssignedProcesses == 0` e `NumberOfProcessIdsInList == 0`).
- [x] Eliminação da janela de execução anterior à associação via `CREATE_SUSPENDED` + `AssignProcessToJobObject` + `IsProcessInJob` antes de `ResumeThread`.
- [x] Falha de associação impede o início do upload.
- [x] Recuperação de Job Objects após reinício sem reboot especificada (`OpenJobObject`, ACL restrita, diferenciação entre inexistente/vazio/ativo/inacessível).
- [x] Evidência durável com recuperação de gravação interrompida, detecção de corrupção, idempotência (`event_id`) e Outbox local.
- [x] Concorrência e fencing protegidos (Mutex Windows + `UPDATE` atômico com `attempt_id` e `fencing_token`).
- [x] Comprovação de reboot baseada em `current_boot_time_utc > attempt_boot_time_utc` (`LastBootUpTime` UTC), imune a ajustes de relógio.
- [x] Todos os cenários inconclusivos continuam bloqueando retry automático.

---

## 3. LIMITAÇÕES TÉCNICAS EXPLICITAMENTE REGISTRADAS

1. **Sem garantia de *exactly-once*:** Se o upload já tiver sido concluído no YouTube milissegundos antes do encerramento local, a única proteção contra duplicidade é a reconciliação no YouTube Studio (`FOUND_PUBLISHED`).
2. **Dependência do Windows Job Object:** Se o sistema operacional negar criação/consulta do Job Object ou um processo ficar preso em I/O de kernel sem encerrar, o resultado será `INCONCLUSIVE` e exigirá intervenção humana.
3. **Calibração operacional:** Os timeouts (`WATCHDOG_HEARTBEAT_TIMEOUT_SEC`, `TERMINATION_GRACEFUL_TIMEOUT_SEC`, etc.) dependem de calibração em staging e não devem ser fixados arbitrariamente.

---

## 4. PROTEÇÃO DAS HOMOLOGAÇÕES (SPRINT 2.6)

Esta especificação preserva integralmente:
- Pipeline B como arquitetura operacional.
- AI Video Director, `DirectorExecutionInterpreter`, `DirectorMediaRouter`, `Director Camera Engine`, Master AV Plan e Contrato Universal Scene V2.
- Todos os testes e homologações da Sprint 2.6.

---

## 5. DECISÃO FINAL (ETAPA 0.8 — ESPECIFICAÇÃO TÉCNICA CONSOLIDADA)

- **Decisão Documental:** **GO para futura implementação** da arquitetura de Watchdog Local com Windows Job Objects, Outbox local e validação no Cloud Hub.
- **Exceção explícita (NO-GO):** **NO-GO para retry automático** em qualquer cenário `INCONCLUSIVE` ou sem identificação confiável do processo.
- **Escopo desta aprovação:** Apenas maturidade documental. **Nenhuma implementação** está autorizada sem aprovação explícita posterior.
