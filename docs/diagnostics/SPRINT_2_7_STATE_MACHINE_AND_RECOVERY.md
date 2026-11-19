# SPRINT 2.7 — MÁQUINA DE ESTADOS, LOCK E RECUPERAÇÃO DE JOBS (T2 & T4)

**Data:** 2026-11-17  
**Modo:** Somente leitura (análise pré-implementação)  

---

## 1. Diagnóstico Atual de Concorrência (`get_next_job`)

**Evidência:** `cloud_hub/main.py:678-711`
- `get_next_job(market_region, session)` executa `SELECT` em `VideoJob` onde `status IN ("DRAFTED", "APPROVED", "READY_FOR_EDIT")`.
- **Falhas comprovadas:**
  1. Não há lock de linha (`FOR UPDATE SKIP LOCKED` no PostgreSQL ou transação `BEGIN IMMEDIATE` / `UPDATE ... WHERE status IN (...)` no SQLite).
  2. Não transiciona `job.status` para `"RENDERING"`.
  3. Não grava `job.worker_id`.
  4. Dois workers concorrentes recebem o **mesmo** `job_id`.

### Solução Compatível com SQLite (Testes) e PostgreSQL (Produção)

Não presumir que `FOR UPDATE SKIP LOCKED` funciona no SQLite (SQLite não suporta `SKIP LOCKED`).

**Estratégia Dual-DB Segura:**
1. **PostgreSQL:** `SELECT ... WHERE status IN (...) ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED`, seguido de `UPDATE` para `status = "RENDERING"`, `worker_id = worker_id`, `updated_at = now()`, `commit()`.
2. **SQLite:** Selecionar candidato `id` e executar `UPDATE video_jobs SET status = 'RENDERING', worker_id = :wid, updated_at = :now WHERE id = :id AND status IN ('DRAFTED', 'APPROVED', 'READY_FOR_EDIT')` verificando `rowcount == 1`. Se `rowcount == 0` (outro worker pegou antes), tentar o próximo candidato (retry curto até 3 vezes).

---

## 2. Máquina de Estados Proposta

### Estados Oficiais
- `PENDING` → `DRAFTED` → `APPROVED` / `READY_FOR_EDIT` → `RENDERING` → `PENDING_UPLOAD` → `UPLOADING` → `PUBLISHED`
- Estados de falha/recuperação: `FAILED`, `FAILED_QC`, `FAILED_UPLOAD`, `STALE_RECOVERY` (ou retorno controlado para `READY_FOR_EDIT` / `PENDING_UPLOAD`).

### Transições Permitidas em `report_status` (`cloud_hub/main.py:714-725`)

| Estado Atual | Próximos Estados Permitidos |
|---|---|
| `PENDING` | `DRAFTED`, `FAILED` |
| `DRAFTED` | `APPROVED`, `READY_FOR_EDIT`, `RENDERING`, `FAILED` |
| `APPROVED` / `READY_FOR_EDIT` | `RENDERING`, `FAILED` |
| `RENDERING` | `PENDING_UPLOAD`, `UPLOADING`, `FAILED`, `FAILED_QC`, `READY_FOR_EDIT` (recovery) |
| `PENDING_UPLOAD` | `UPLOADING`, `PUBLISHED`, `FAILED_UPLOAD`, `FAILED` |
| `UPLOADING` | `PUBLISHED`, `PENDING_UPLOAD` (recovery/quota), `FAILED_UPLOAD`, `FAILED` |
| `PUBLISHED` | *(Terminal — idempotente: se receber `PUBLISHED` novamente com mesmo `published_url`, retorna `200 OK` sem alterar)* |
| `FAILED` / `FAILED_QC` / `FAILED_UPLOAD` | `READY_FOR_EDIT`, `PENDING_UPLOAD` (retry manual/automático) |

**Proibição explícita:** `RENDERING` → `PUBLISHED` direto **sem** passar por upload válido (ou sem `published_url` válido) deve ser rejeitado com `409 Conflict` / `422`.

---

## 3. Política de Recuperação de Jobs Interrompidos (T4 — Especificação)

*(Nota: Não implementar a recuperação automática completa nesta etapa sem aprovação; documentar política e timeouts.)*

1. **Detecção de Jobs Abandonados:**
   - `RENDERING` com `updated_at < now - 45 min` (timeout de renderização).
   - `UPLOADING` com `updated_at < now - 20 min` (timeout de upload).
   - `PENDING_UPLOAD` com `updated_at < now - 60 min` (fila parada).
2. **Identificação do Worker:** Via `job.worker_id` + `updated_at` (heartbeat futuro).
3. **Recuperação Segura:**
   - `RENDERING` expirado → volta para `READY_FOR_EDIT` (incrementando `retry_count`), se `retry_count < max_retries` (ex: 3); caso contrário, `FAILED`.
   - `PENDING_UPLOAD` expirado → permanece `PENDING_UPLOAD` e libera `worker_id = None` para outro worker assumir.
   - `UPLOADING` expirado → **NÃO** re-enfileirar cegamente! Deve passar por reconciliação de idempotência (ver `SPRINT_2_7_UPLOAD_IDEMPOTENCY_DESIGN.md`) para checar se o vídeo já foi publicado no YouTube antes de permitir novo upload.
4. **Concorrência na Recuperação:** O job de recuperação deve usar `UPDATE ... WHERE status = :old_status AND updated_at < :cutoff` atômico para evitar recuperar um job que acabou de ser atualizado por um worker vivo.
