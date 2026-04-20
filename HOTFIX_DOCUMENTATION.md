# 🔥 HOTFIX: Critical Race Conditions & State Management

## 🎯 Problema Resolvido

Este hotfix corrige **bugs críticos de concorrência** que causavam:
- Jobs sendo processados por múltiplos workers simultaneamente
- Jobs ficando presos em estados intermediários (RENDERING, UPLOADING)
- Perda de estado devido a transições inválidas
- Locks permanentes no banco de dados

## 🔧 Correções Implementadas

### 1. **SELECT FOR UPDATE SKIP LOCKED** (`cloud_hub/main.py`)
**Antes:**
```python
@app.get("/worker/next-job/{market_region}")
def get_next_job(market_region: str, session: Session = Depends(get_session)):
    job = session.exec(select(VideoJob).where(VideoJob.status == "APPROVED")...).first()
    return {"job_id": job.id}  # ❌ Múltiplos workers podem pegar o mesmo job
```

**Depois:**
```python
@app.get("/worker/next-job/{market_region}")
def get_next_job(market_region: str, request: Request, session: Session = Depends(get_session)):
    job = session.exec(
        select(VideoJob)
        .where(VideoJob.status == "APPROVED")
        .where(VideoJob.worker_id.is_(None))
        .with_for_update(skip_locked=True)  # ✅ Lock otimista
        .limit(1)
    ).first()
    
    # Marca como PROCESSING atomicamente
    job.status = "PROCESSING"
    job.worker_id = request.headers.get("X-Worker-ID")
    job.updated_at = datetime.now(timezone.utc)
    session.commit()
```

**Benefícios:**
- Previne race conditions entre múltiplos workers
- Garante que apenas 1 worker processa cada job
- Lock é liberado automaticamente se transaction falhar

---

### 2. **Validação FSM (Finite State Machine)** (`cloud_hub/main.py`)
**Antes:**
```python
@app.post("/worker/report-status/{job_id}")
def report_status(job_id: UUID, status: str, ...):
    job.status = status  # ❌ Aceita qualquer transição (PUBLISHED -> RENDERING é possível!)
    session.commit()
```

**Depois:**
```python
VALID_TRANSITIONS = {
    "APPROVED": ["PROCESSING", "FAILED"],
    "PROCESSING": ["RENDERING", "FAILED"],
    "RENDERING": ["UPLOADING", "FAILED"],
    "UPLOADING": ["PUBLISHED", "FAILED"],
    ...
}

@app.post("/worker/report-status/{job_id}")
def report_status(job_id: UUID, status: str, ...):
    valid_next_states = VALID_TRANSITIONS.get(job.status, [])
    if status not in valid_next_states:
        raise HTTPException(409, detail=f"Invalid transition: {job.status} -> {status}")
    
    job.status = status  # ✅ Apenas transições válidas
    session.commit()
```

**Benefícios:**
- Previne corrupção de estado
- Garante que jobs seguem o fluxo correto: APPROVED → PROCESSING → RENDERING → UPLOADING → PUBLISHED
- Facilita debugging (HTTP 409 indica tentativa de transição inválida)

---

### 3. **Job Timeout Watchdog** (`cloud_hub/main.py`)
**Novo componente:**
```python
async def job_timeout_watchdog():
    """Detecta jobs travados há mais de 30 minutos."""
    while True:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        stale_jobs = session.exec(
            select(VideoJob)
            .where(VideoJob.status.in_(["PROCESSING", "RENDERING", "UPLOADING"]))
            .where(VideoJob.updated_at < cutoff)
        ).all()
        
        for job in stale_jobs:
            job.status = "FAILED"
            job.error_message = "Job timeout - worker likely crashed"
            job.worker_id = None  # Libera para retry
        
        await asyncio.sleep(60)
```

**Benefícios:**
- Detecta workers que crasharam
- Libera jobs travados automaticamente
- Permite retry por outros workers

---

### 4. **Campos Adicionados ao Modelo** (`cloud_hub/models.py`)
```python
class VideoJob(SQLModel, table=True):
    # ... campos existentes ...
    
    # ✅ Novos campos para tracking
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**Benefícios:**
- `worker_id`: Identifica qual worker está processando o job
- `error_message`: Armazena detalhes de falhas
- `updated_at`: Usado pelo watchdog para detectar timeouts

---

## 🧪 Como Testar

### Teste 1: Race Condition (CORRIGIDO)
```bash
# Terminal 1
python -m desktop_worker.main

# Terminal 2 (iniciar 1s depois)
python -m desktop_worker.main

# Terminal 3
curl -X POST http://localhost:8000/admin/trigger-scout/<channel_id>

# ✅ ESPERADO: Apenas 1 worker pega o job
# ❌ ANTES: Ambos pegavam o mesmo job
```

### Teste 2: Validação de Transição
```bash
# Tentar transição inválida
curl -X POST http://localhost:8000/worker/report-status/<job_id> \
  -d '{"status": "RENDERING"}' \
  # Se job está PUBLISHED

# ✅ ESPERADO: HTTP 409 "Invalid transition: PUBLISHED -> RENDERING"
# ❌ ANTES: Aceitava e job voltava para RENDERING
```

### Teste 3: Timeout Watchdog
```bash
# Simular worker travado
1. Inicie worker
2. Force um breakpoint no meio do processamento
3. Aguarde 30 minutos

# ✅ ESPERADO: Job resetado para FAILED após 30min
# ❌ ANTES: Job ficava preso forever
```

---

## 📊 Migração de Banco de Dados

Execute este SQL no PostgreSQL/Neon para adicionar os novos campos:

```sql
ALTER TABLE video_jobs 
  ADD COLUMN IF NOT EXISTS worker_id TEXT,
  ADD COLUMN IF NOT EXISTS error_message TEXT,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- Atualizar jobs existentes
UPDATE video_jobs 
SET created_at = CURRENT_TIMESTAMP, 
    updated_at = CURRENT_TIMESTAMP 
WHERE created_at IS NULL;
```

---

## 🚀 Próximos Passos (Não Incluídos Neste PR)

### FASE 2 - High Priority
- [ ] Retry exponencial no Playwright upload
- [ ] Migrar seletores CSS para role/text selectors

### FASE 3 - Hardening
- [ ] Implementar Dead Letter Queue (DLQ)
- [ ] Adicionar metrics (Prometheus)
- [ ] Idempotency keys

---

## ⚠️ Breaking Changes

**NENHUM!** Todas as mudanças são backward-compatible:
- Novos campos do modelo têm valores default
- Workers antigos continuam funcionando (apenas sem worker_id tracking)
- API retorna campos extras, mas não quebra contratos existentes

---

## 📝 Checklist de Deployment

- [ ] Executar migration SQL no banco
- [ ] Fazer deploy do `cloud_hub` (FastAPI)
- [ ] Reiniciar workers com novo código
- [ ] Monitorar logs por 1 hora para validar
- [ ] Verificar que jobs não ficam mais travados

---

**Autor:** Claude AI (Anthropic)  
**Data:** 2025-04-20  
**Severidade:** CRITICAL  
**Risco de Regressão:** LOW (backward-compatible)
