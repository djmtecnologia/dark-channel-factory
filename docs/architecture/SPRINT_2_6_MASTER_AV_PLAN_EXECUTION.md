# SPRINT 2.6 — ETAPA 1: DIAGNÓSTICO DO CONSUMO DO MASTER AV PLAN

> **Status:** Diagnóstico Técnico (Etapa 1 — Somente Leitura / Sem Alteração de Código)  
> **Baseline:** Sprint 2.5 homologada (AI Video Director + `MasterAVPlan` + `MasterPlanAdapter` + Cloud Hub Integration + `VIDEO_DIRECTOR_MODE`)  
> **Objetivo:** Mapear o fluxo completo do `MasterAVPlan` até o `desktop_node.py` / Pipeline B, identificar o que é consumido, parcialmente utilizado ou ignorado, e propor a arquitetura de execução cinematográfica na Sprint 2.6 preservando integralmente o modo `legacy`.

---

## 1. Arquitetura Operacional Atual

A fábrica opera em uma arquitetura distribuída em dois grandes blocos:

1. **Cloud Hub (`cloud_hub/`):**
   - Orquestra a geração de roteiros via `Copywriter` (`cloud_hub/core/copywriter.py`).
   - Quando `VIDEO_DIRECTOR_MODE=director` (resolvido por `resolve_video_director_mode` em `cloud_hub/core/video_director_integration.py`), executa o `AIVideoDirector` (`agents/video_director/director_engine.py`), produzindo um `MasterAVPlan` (`agents/video_director/master_plan_models.py`).
   - Converte o `MasterAVPlan` para o contrato operacional do Pipeline B via `MasterPlanAdapter.to_pipeline_b()` (`agents/video_director/pipeline_adapter.py`) e persiste o `script_data` atualizado no banco de dados.
   - Em caso de falha, executa fallback seguro para `legacy_fallback`, preservando o `script_data` original do Copywriter.

2. **Desktop Node (`desktop_node.py`):**
   - Consome jobs do Cloud Hub contendo `job_data["script_data"]`.
   - Executa a produção audiovisual local em `render_video(job_data)` (`desktop_node.py:2054`):
     - Síntese de narração (`generate_voice_master`).
     - Geração de legendas (`generate_subtitles_locally` + `pysubs2`).
     - Normalização de cenas (`shared/scene_normalizer.py:normalize_scenes`).
     - Seleção e obtenção de recursos visuais (`download_assets` / `fetch_premium_broll`).
     - Aplicação de efeitos visuais e montagem via FFmpeg (`zoompan`, `scale`, `crop`, concatenação, mixagem de áudio/SFX, burn-in de legendas).
     - QC e upload final.

---

## 2. Fluxo Completo dos Dados (12 Etapas)

```text
[1. Copywriter] (cloud_hub/core/copywriter.py)
       │ gera script_data bruto (title, script_full, scenes[])
       ▼
[2. AI Video Director] (agents/video_director/director_engine.py) [se VIDEO_DIRECTOR_MODE == "director"]
       │ analisa narrativa, cria VisualBible, SceneVisualDirection, PlannedScene
       ▼
[3. MasterAVPlan] (agents/video_director/master_plan_models.py)
       │ artefato Pydantic validado (plan_id, visual_bible, scenes[PlannedScene], execution_metadata)
       ▼
[4. MasterPlanAdapter] (agents/video_director/pipeline_adapter.py)
       │ to_pipeline_b() -> AdaptedPipelineBPayload (scenes[], director_metadata, master_av_plan_summary)
       ▼
[5. Cloud Hub Persistence] (cloud_hub/core/video_director_integration.py:324-325)
       │ persiste script_data com scenes[] adaptadas + director_metadata + master_av_plan_summary
       ▼
[6. Entrega ao Desktop Node] (cloud_hub/main.py -> desktop_node.py:2056)
       │ job_data.get("script_data", {})
       ▼
[7. Interpretação das Cenas] (desktop_node.py:2076 -> shared/scene_normalizer.py)
       │ normalize_scenes(script_obj.get("scenes", []), fallback_topic=topic)
       │ ⚠️ director_metadata e master_av_plan_summary NÃO são lidos pelo desktop_node.py
       ▼
[8. Seleção de Recursos Visuais] (desktop_node.py:2115 download_assets / :950 fetch_premium_broll)
       │ lê apenas b_roll_visual, b_roll_query, sfx_cue, narration/text
       │ ⚠️ Cena 0 ignora o plano e usa hook_prompt hardcoded com `topic`
       ▼
[9. Geração de Imagens/Vídeos] (desktop_node.py:2141, 2206)
       │ Pollinations (hook e fallback de repetição) + stock (Pexels/Pixabay/cache local)
       ▼
[10. Montagem com FFmpeg] (desktop_node.py:1648, 1657, 2024-2029)
       │ zoompan genérico para imagens; vídeos sem zoompan; sem xfade dirigido por transition_in/out
       ▼
[11. Narração, Sincronização e Legendas] (desktop_node.py:2063-2106)
       │ generate_voice_master() -> audio_path -> generate_subtitles_locally() -> srt_path
       │ duração total guiada por real_audio_dur / last_sub_end
       ▼
[12. QC e Entrega Final] (desktop_node.py / desktop_worker/uploader/youtube_manager.py)
```

---

## 3. Pontos de Integração Existentes

| Camada | Arquivo / Símbolo | Estado | Evidência |
|---|---|---|---|
| Copywriter → Integration | `cloud_hub/core/copywriter.py:663` | **Existente** | Chama `integrate_video_director` |
| Director → Adapter | `cloud_hub/core/video_director_integration.py` | **Existente** | Executa `AIVideoDirector` + `MasterPlanAdapter.to_pipeline_b()` |
| Persistência Cloud Hub | `cloud_hub/core/video_director_integration.py:324-325` | **Existente** | Grava `director_metadata` e `master_av_plan_summary` em `updated_script_data` |
| Normalização de Cenas | `desktop_node.py:2076` (`normalize_scenes`) | **Existente** | Normaliza `scenes[]` preservando campos V2 |
| Consumo de `b_roll_*` e `sfx_cue` | `desktop_node.py:2117-2122` | **Existente** | Lê `b_roll_visual`, `b_roll_query`, `sfx_cue` |
| Consumo de `director_metadata` / `visual_bible` | `desktop_node.py` | **Inexistente** | Nenhuma referência a `director_metadata` ou `master_av_plan_summary` em `desktop_node.py` |

---

## 4. Campos do `MasterAVPlan` Efetivamente Consumidos

Conforme `EFFECTIVELY_CONSUMED_BY_PIPELINE_B` em `agents/video_director/pipeline_adapter.py:50-53` e inspeção direta em `desktop_node.py:2076-2223`:

- `narration` (e alias `text`) — usado na síntese de voz e fallback de variação de imagem.
- `id` / `scene_id` / `order` — ordenação e identificação de cena.
- `duration_s` (e `timestamp`) — cálculo de duração da cena (`desktop_node.py:2247`).
- `b_roll_visual` e `b_roll_query` — prioridade de busca de mídia em `download_assets()` (`desktop_node.py:2117-2120`).
- `sfx_cue` — busca de efeito sonoro em `fetch_sfx()` (`desktop_node.py:2122, 2221`).
- `visual_cues` / `micro_cues` — extraídos em `desktop_node.py:2079-2082`.

---

## 5. Campos Ignorados ou Parcialmente Utilizados

### 5.1. Parcialmente Utilizados
- `b_roll_visual` na Cena 0: **ignorado** na prática quando `scene_index == 0`, pois `desktop_node.py:2126-2148` sobrescreve com `hook_prompt` hardcoded baseado apenas em `topic`.
- `duration_s` / `duration_seconds`: lido como fallback, mas a duração real da cena no vídeo final é submetida à sincronização de áudio/legendas (`total_audio_sec`).

### 5.2. Totalmente Ignorados pelo `desktop_node.py`
Conforme `PRESERVED_WITHOUT_CONSUMER` (`agents/video_director/pipeline_adapter.py:55-60`) e `SceneVisualDirection` (`agents/video_director/models.py:248-282`):
- **Nível Global (`MasterAVPlan` / `VisualBible`):**
  - `visual_bible.visual_language`, `dominant_style`, `color_palette`, `lighting_mood`, `pacing_rhythm`.
  - `central_theme`, `production_profile`.
- **Nível de Cena (`PlannedScene` / `SceneVisualDirection`):**
  - Direção de câmera: `camera_framing`, `camera_movement`, `depth_of_field`, `composition`.
  - Estética e atmosfera: `lighting_mood`, `atmosphere`, `color_palette_notes`.
  - Produção e custo: `visual_production_type`, `estimated_cost_tier`.
  - Transições: `transition_in`, `transition_out`, `transition_rationale`.
  - Continuidade: `continuity_anchor`, `visual_connection_previous`, `visual_connection_next`, `continuity_notes`.
  - Sobreposição de texto: `on_screen_text`.
  - Intenção narrativa: `narrative_role`, `narrative_purpose`, `visual_intent`, `what_viewer_sees`, `dominant_element`.

---

## 6. Limitações do Desktop Node (`desktop_node.py`)

1. **Ausência de leitura de `director_metadata` / `master_av_plan_summary`:** `render_video()` extrai apenas `title`, `script_full` e `scenes` de `script_obj`.
2. **Hook Engine hardcoded (`desktop_node.py:2126-2148`):** A Cena 0 sempre gera uma imagem Pollinations com prompt fixo, ignorando `visual_bible`, `b_roll_visual`, `lighting_mood` e `color_palette_notes` do Director.
3. **Seleção de mídia agnóstica a `visual_production_type`:** `fetch_premium_broll()` não recebe orientação se a cena pede `stock_footage`, `cinematic_still`, `local_asset`, `motion_graphic` ou `blender_3d`.
4. **Movimento de câmera genérico no FFmpeg:** `zoompan` (`desktop_node.py:1648, 1657, 2024-2029`) não mapeia os valores canônicos de `camera_movement` (`slow_push_in`, `dramatic_pull_out`, `tracking_lateral`, `dutch_tilt`, `impact_micro_shake`, `static`).
5. **Sem aplicação de paleta/iluminação/transições dirigidas:** Não há filtros de cor (`eq`, `colorbalance`) derivados de `lighting_mood`/`color_palette_notes`, nem transições `xfade` mapeadas de `transition_in`/`transition_out`.
6. **Sem motor de continuidade visual:** `continuity_anchor` e `visual_bible` não são injetados nos prompts de geração de imagem (Pollinations) nem usados para manter coerência entre cenas adjacentes.

---

## 7. Proposta de Arquitetura para Consumo do Master AV Plan

Introduzir uma camada **opcional e retrocompatível** no `desktop_node.py` (ex.: `DirectorExecutionInterpreter`), acionada apenas quando `director_metadata` / campos de direção estiverem presentes:

1. **Extração Global (`VisualBible`):**
   - Ler `script_obj.get("director_metadata", {}).get("visual_bible")` (ou `master_av_plan_summary`).
   - Construir um `global_style_suffix` (estilo, paleta, iluminação) para enriquecer prompts de imagem estática (Pollinations) quando necessário.
2. **Roteamento de Recursos por `visual_production_type` (preservando hierarquia de custo):**
   - `local_asset` → priorizar biblioteca local/cache.
   - `stock_footage` → priorizar Pexels/Pixabay.
   - `cinematic_still` → gerar imagem estática (Pollinations) enriquecida com `b_roll_visual` + `lighting_mood` + `color_palette_notes` + `continuity_anchor`.
   - `auto` → manter fluxo atual (`fetch_premium_broll` → fallback Pollinations).
   - `blender_3d` / `motion_graphic` → manter fallback seguro para `cinematic_still` / `stock_footage` caso Blender/assets 3D não estejam habilitados no job.
3. **Hook Engine Dirigido (Cena 0):**
   - Se a Cena 0 possuir `b_roll_visual` e `director_metadata`, compor o `hook_prompt` usando `b_roll_visual` + `VisualBible` em vez do template genérico hardcoded.
4. **Mapeamento Determinístico FFmpeg para `camera_movement` (em imagens):**
   - `slow_push_in` → `zoompan` zoom in suave (`1.0 -> 1.25`).
   - `dramatic_pull_out` → `zoompan` zoom out (`1.3 -> 1.0`).
   - `tracking_lateral` → `zoompan` com pan horizontal em `x`.
   - `static` → sem `zoompan` (apenas `scale`/`crop`).
   - `impact_micro_shake` → micro-oscilação em `x`/`y` nos primeiros frames.
   - Para **vídeos** (`.mp4`), manter comportamento seguro sem `zoompan` (conforme comentário em `desktop_node.py:2025`: `zoompan` congela vídeo).
5. **Continuidade Visual:**
   - Quando `continuity_anchor` estiver presente em cenas que usam geração de imagem, anexar a âncora e `color_palette_notes` ao prompt para preservar identidade de personagem/ambiente/objeto.

---

## 8. Estratégia de Compatibilidade com Legacy

- **Jobs antigos e modo `legacy`:** Não possuem `director_metadata` nem `master_av_plan_summary`. O `DirectorExecutionInterpreter` detecta ausência desses metadados e executa **100% o caminho legado atual**, sem alterações.
- **Contratos:** `MasterAVPlan`, `Scene` (V2) e `normalize_scenes` permanecem intactos.
- **QC e Uploader:** Recebem o mesmo formato de vídeo final (`.mp4` H.264/AAC) e metadados, sem impacto.

---

## 9. Estratégia de Fallback

- Se `director_metadata` estiver malformado ou incompleto → logar warning e cair para defaults seguros do modo legado.
- Se `camera_movement` tiver valor desconhecido → fallback para `slow_push_in` (ou `static`).
- Se `visual_production_type` pedir `cinematic_still` e Pollinations falhar → fallback para `fetch_premium_broll()` (stock/local).
- Se `transition_in`/`transition_out` não for suportado pelo FFmpeg local → fallback para corte seco (`direct_cut`).

---

## 10. Estratégia de Testes

1. **Testes Unitários (sem FFmpeg pesado / sem rede):**
   - Mapeamento `camera_movement` → string de filtro FFmpeg.
   - Construção de prompt enriquecido com `VisualBible` + `continuity_anchor`.
   - Roteamento de `visual_production_type` com mocks de `fetch_premium_broll` e `generate_pollinations_image`.
2. **Testes de Regressão Legacy:**
   - Job sem `director_metadata` deve produzir exatamente os mesmos parâmetros de execução que a baseline da Sprint 2.5.
3. **Testes de Integração Leve:**
   - Payload real gerado por `MasterPlanAdapter.to_pipeline_b()` consumido pelo interpretador do Desktop Node.

---

## 11. Impactos de Performance

- **CPU / Memória:** Mínimo. A leitura de `director_metadata` e construção de strings de filtro FFmpeg é $O(N)$ no número de cenas (microssegundos).
- **Renderização FFmpeg:** `zoompan` já é executado hoje para imagens; variar a expressão de `z`/`x`/`y` conforme `camera_movement` não altera significativamente o tempo de render.
- **Chamadas Externas:** Nenhuma chamada LLM adicional.

---

## 12. Impactos Financeiros

- **Zero custo adicional de LLM:** Toda a inteligência cinematográfica já foi paga/executada no `AIVideoDirector` (Cloud Hub).
- **Hierarquia de custo de mídia preservada:** Cache local → Pexels/Pixabay (gratuitos) → Pollinations (gratuito/baixo custo), respeitando `estimated_cost_tier` e `visual_production_type`.
- **Sem geração de vídeo por IA** nesta etapa.

---

## 13. Riscos Técnicos

1. **Expressões `zoompan` inválidas no FFmpeg:** Sintaxe incorreta em `x`/`y`/`z` pode quebrar o render. *Mitigação:* tabela determinística testada unitariamente.
2. **Excesso de chamadas ao Pollinations:** Se muitas cenas vierem com `visual_production_type=cinematic_still`, pode haver lentidão ou rate-limit. *Mitigação:* respeitar cache em `ASSETS_CACHE` e fallback para `fetch_premium_broll`.
3. **Quebra de compatibilidade com jobs antigos:** *Mitigação:* gate explícito por presença de `director_metadata` + testes de regressão legacy.

---

## 14. Divisão Sugerida em Tarefas Sequenciais

- **Tarefa 1:** Criar módulo/helper `DirectorExecutionInterpreter` (leitura de `director_metadata`/`visual_bible`, construção de prompts com continuidade, mapeamento de `camera_movement` para filtros FFmpeg).
- **Tarefa 2:** Integrar `DirectorExecutionInterpreter` ao `download_assets()` e ao Hook Engine (Cena 0) no `desktop_node.py`, respeitando `visual_production_type` e hierarquia de custo.
- **Tarefa 3:** Integrar mapeamento de `camera_movement` na montagem FFmpeg de imagens no `desktop_node.py`.
- **Tarefa 4:** Suíte de testes unitários e de regressão legacy + validação ponta a ponta em staging.

---

## 15. Critérios de Aceite de Cada Tarefa

- **Tarefa 1:** Helper isolado, 100% testado unitariamente, sem efeitos colaterais em jobs legacy.
- **Tarefa 2:** Cena 0 usa `b_roll_visual` + `VisualBible` quando em modo `director`; `visual_production_type` orienta a seleção de mídia com fallback seguro.
- **Tarefa 3:** Imagens aplicam o movimento de câmera correspondente ao `camera_movement` planejado; vídeos mantêm pipeline seguro sem `zoompan`.
- **Tarefa 4:** Todos os testes existentes da Sprint 2.5 continuam passando + novos testes da Sprint 2.6 aprovados.
