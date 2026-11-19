# SPRINT 2.7 — DESIGN DE IDEMPOTÊNCIA DO UPLOADER (T3)

**Data:** 2026-11-17  
**Modo:** Somente leitura (análise pré-implementação)  

---

## 1. Cenário Crítico Analisado

1. Desktop Node envia o vídeo ao YouTube via Playwright (`publish_video_to_youtube` em `agents/publisher/playwright_uploader.py`).
2. O YouTube conclui a publicação e retorna a URL.
3. O Desktop Node cai **antes** de persistir `published_url` e `status = PUBLISHED` no Cloud Hub.
4. O sistema reinicia e tenta processar novamente o mesmo job.

**Limitação técnica real:** O Playwright interagindo com o YouTube Studio **não oferece transação distribuída (2PC) nem chave de idempotência nativa do YouTube Studio UI**. Portanto, **não é possível afirmar garantia matemática absoluta de "exatamente uma publicação" (exactly-once)** apenas pelo cliente; o que se garante é **at-least-once com prevenção forte de duplicação via checkpoint local + reconciliação**.

---

## 2. Estratégia Recomendada

1. **Chave de Idempotência Determinística:**
   - `upload_idempotency_key = sha256(f"{job_id}:{channel_id}:{title}")` (ou `job_id` como chave primária de publicação).
2. **Checkpoint Local Persistente (SQLite/JSON local no Desktop Node):**
   - Antes de iniciar `publish_video_to_youtube()`, gravar localmente `{job_id, status: "UPLOADING", started_at, title}`.
   - Imediatamente após `publish_video_to_youtube()` retornar `video_url`, gravar localmente `{job_id, status: "PUBLISHED_LOCAL", video_url, published_at}` **antes** de chamar o Cloud Hub.
   - Se o Desktop Node cair após gravar `PUBLISHED_LOCAL` e antes de confirmar no Cloud Hub, na reinicialização ele lê o checkpoint local e apenas sincroniza `PUBLISHED` + `video_url` com o Cloud Hub, **sem reenviar ao YouTube**.
3. **Sincronização com Cloud Hub (`/worker/report-status/{job_id}`):**
   - `desktop_node.py` deve parar de chamar `/worker/job-status` (inexistente, linha 142) e parar de reportar `PUBLISHED` após `render_video()` (linha 2721).
   - Fluxo correto: `render_video()` sucesso → reporta `PENDING_UPLOAD` → `upload_worker_loop()` inicia → reporta `UPLOADING` → `publish_video_to_youtube()` retorna URL válida → reporta `PUBLISHED` com `published_url`.
4. **Tratamento de Timeout / Resultado Desconhecido:**
   - Se `publish_video_to_youtube()` sofrer timeout ou crash durante o upload (antes de obter `video_url`), marcar como `FAILED_UPLOAD` (ou `UPLOAD_UNKNOWN`) e **exigir verificação manual ou busca por título no YouTube Studio** antes de re-upload automático.

---

## 3. Critérios de Aceite

| Cenário | Comportamento Esperado |
|---|---|
| **Upload bem-sucedido** | `published_url` salvo no checkpoint local e persistido no Cloud Hub; status = `PUBLISHED`; `published_at` preenchido. |
| **Upload com falha** | Status = `FAILED_UPLOAD` (ou `PENDING_UPLOAD` se cota/quotaExceeded); `error_message` registrado; não marca `PUBLISHED`. |
| **Timeout** | Status = `FAILED_UPLOAD` com flag de resultado desconhecido; não reenvia automaticamente sem reconciliação. |
| **Queda do Desktop Node pós-URL** | Ao reiniciar, lê checkpoint `PUBLISHED_LOCAL` e sincroniza `PUBLISHED` + `published_url` com Cloud Hub sem novo upload. |
| **Queda do Cloud Hub** | Desktop Node mantém checkpoint `PUBLISHED_LOCAL` e faz retry exponencial no `/worker/report-status/{job_id}`. |
| **Tentativa duplicada** | Cloud Hub já possui `status == PUBLISHED` e `published_url`; retorna `200 OK` idempotente sem alterar estado. |
