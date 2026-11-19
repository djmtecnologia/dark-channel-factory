# SPRINT 2.7 — ETAPA H: PROTOCOLO DE HOMOLOGAÇÃO OPERACIONAL (E2E)

**Ambiente:** Isolado / Staging (banco de testes, credenciais de teste).

1. **Criar job de teste:** Inserir `VideoJob` com `production_profile="PILOT_5MIN"`, `video_type="LONG"`, `status="PENDING"`.
2. **Tema documental:** Definir tema documental válido no nicho do canal.
3. **Gerar roteiro + Master AV Plan:** Executar `process_pending_jobs()` e verificar `status="READY_FOR_EDIT"`, `script_data` preenchido e metadados do Video Director presentes.
4. **Entrega ao Desktop Node:** Chamar `GET /worker/next-job/{market_region}` e confirmar transição imediata para `status="RENDERING"`.
5. **Narração e legendas:** Confirmar geração do áudio (Edge-TTS) e arquivo `.srt` (Whisper) com duração compatível com `PILOT_5MIN` (~5 min).
6. **Aquisição de mídia e renderização:** Confirmar download/cache de assets via `director_media_router` e renderização do MP4 final via `assemble_production()` + `director_camera_engine`.
7. **Quality Control (QC):** Confirmar aprovação pelo `ValidatorAgent.execute_quality_control()`.
8. **Validação do MP4:** Validar via `ffprobe` duração, resolução (>=1280x720), streams de áudio e vídeo.
9. **Simulação de publicação (Dry-Run):** Executar fluxo de upload com mock do Playwright e confirmar que `status` não vira `PUBLISHED` antes do retorno da URL.
10. **Publicação controlada (mediante autorização explícita):** Executar `publish_video_to_youtube()` em canal de teste (privado/não listado).
11. **Confirmação final:** Verificar `published_url`, `published_at` e `status="PUBLISHED"` persistidos no `VideoJob`.
