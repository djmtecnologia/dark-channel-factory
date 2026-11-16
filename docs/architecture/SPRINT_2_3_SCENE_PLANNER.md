# SPRINT 2.3 — SCENE PLANNER (ESPECIFICAÇÃO TÉCNICA)

**Status:** Implementado (Aguardando Auditoria Independente)  
**Fase:** Fase 2 — AI Video Director  
**Componente:** `agents/video_director/scene_planner.py`  

---

## 1. Objetivo Funcional

O **Scene Planner** transforma as saídas de:
- `NarrativeAnalyzer` (`NarrativeAnalysisResult`)
- `VisualDirector` (`VisualDirectionResult`)

em um planejamento estruturado de cenas (`ScenePlanResult`), compatível com o contrato canônico **Scene V2** (`shared/models.py`, `shared/scene_normalizer.py`) e preparado para compor o **Master AV Plan** (planejado para o Sprint 2.4).

---

## 2. Responsabilidades Previstas

1. Analisar a progressão narrativa extraída pelo `NarrativeAnalyzer`.
2. Organizar cenas em sequência cinematográfica coerente.
3. Definir a função narrativa de cada cena (ex.: `hook`, `context`, `escalation`, `climax`, `resolution`, `cta`).
4. Planejar a duração por cena respeitando a duração total do perfil de produção (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`).
5. Preservar integralmente a narração original (`narration` / `text_content`), sem reescrita ou omissão.
6. Associar a direção visual correspondente (`VisualDirector`) a cada cena.
7. Evitar redundância visual entre cenas consecutivas (queries, conceitos ou enquadramentos repetidos).
8. Preservar continuidade temporal, espacial e visual ao longo do roteiro.
9. Identificar transições narrativas entre cenas (`cut`, `crossfade`, `fade_black`, `j_cut`, `l_cut`, etc.).
10. Validar quantidade de cenas, unicidade de IDs e ordenação sequencial.
11. Preparar estrutura compatível com o **Master AV Plan**.

---

## 3. Restrições Arquiteturais

- **Reutilização:** Reutilizar os modelos e validadores existentes (`shared/models.py`, `shared/production_profiles.py`, `shared/scene_normalizer.py`, `agents/video_director/security.py`, `ProductionProfile`).
- **Sem duplicação:** Não duplicar `Scene` V2.
- **Isolamento operacional:**
  - Não alterar o Copywriter operacional.
  - Não alterar `desktop_node.py`.
  - Não alterar FFmpeg, QC ou uploader.
  - Não alterar banco de produção, migrations ou Neon.
  - Não introduzir Redis/RQ.
  - Não migrar do Pipeline B.
- **Testes:** Não realizar chamadas pagas de IA nos testes (usar mocks determinísticos).
- **Escopo:** Não iniciar produção de assets nesta etapa.

---

## 4. Contratos de Entrada e Saída

### 4.1 Entrada (`ScenePlannerInput`)
- `narrative_analysis`: `NarrativeAnalysisResult` (saída validada do `NarrativeAnalyzer`)
- `visual_direction`: `VisualDirectionResult` (saída validada do `VisualDirector`)
- `production_profile`: `ProductionProfile` (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`)
- `original_scenes`: lista opcional/obrigatória de cenas originais com `id`/`scene_id` e `narration` imutável.

### 4.2 Saída (`ScenePlanResult`)
- `profile`: perfil de produção utilizado.
- `total_duration_seconds`: soma exata das durações das cenas planejadas.
- `scenes`: lista ordenada de `PlannedScene` (compatível com Scene V2), contendo:
  - `scene_id` / `id`: identificador único sequencial.
  - `order`: índice sequencial (`1..N`).
  - `narration`: texto original preservado verbatim.
  - `narrative_role`: função narrativa da cena.
  - `duration_seconds`: duração planejada (> 0).
  - `start_time` / `end_time`: timestamps contínuos sem gaps ou sobreposições.
  - `visual_direction`: referência/campos de direção visual associados (`b_roll_query`, `visual_style`, `camera_movement`, etc.).
  - `transition_in` / `transition_out`: transição narrativa identificada.
  - `continuity_notes`: metadados de continuidade temporal/espacial/visual.

---

## 5. Regras Determinísticas vs. Responsabilidades do LLM

### 5.1 Responsabilidades do LLM
- Sugerir função narrativa (`narrative_role`) por cena.
- Sugerir transições narrativas adequadas ao ritmo.
- Propor ajustes de ritmo/ênfase visual respeitando o tom do `VisualDirector`.
- Detectar e sugerir variação para evitar redundância visual consecutiva.

### 5.2 Regras Determinísticas (Python)
- **Preservação da narração:** Verificação byte-a-byte / normalizada de que `narration` não foi alterada, resumida ou reordenada.
- **Unicidade e ordenação de IDs:** Rejeitar IDs duplicados, ausentes ou fora de ordem.
- **Duração e timestamps:**
  - Validar `duration_seconds > 0` (sem NaN/Inf).
  - Respeitar duração explícita quando fornecida, ou distribuir proporcionalmente conforme o perfil (`PILOT_5MIN` ~300s, `STANDARD_10MIN` ~600s, `DOCUMENTARY_20MIN` ~1200s) dentro da tolerância configurada.
  - Recalcular `start_time` e `end_time` sequencialmente para garantir continuidade exata.
- **Compatibilidade V2:** Validar todos os campos obrigatórios do contrato Scene V2.
- **Imutabilidade de entrada:** Não mutar os objetos/dicionários de entrada.

---

## 6. Erros Esperados

- `ScenePlannerValidationError`: IDs duplicados, cenas fora de ordem, campos V2 ausentes, narração alterada.
- `DurationMismatchError`: duração total incompatível com o perfil ou soma inconsistente.
- `IncompleteLLMResponseError`: resposta do LLM incompleta, malformada ou com cenas faltantes.
- `VisualContinuityError`: falha crítica de associação visual quando não há fallback determinístico.

---

## 7. Integração Futura com Master AV Plan

O `ScenePlanResult` será a espinha dorsal do **Master AV Plan** (Sprint 2.4+), fornecendo:
- Linha do tempo determinística (`start_time`, `end_time`, `duration_seconds`).
- Texto exato para síntese de voz (TTS) e legendagem.
- Queries e diretrizes visuais livres de redundância para o `MediaFetcher` / gerador de B-roll.
- Metadados de transição para composição final no motor de edição.

---

## 8. Critérios de Aceitação

1. 100% compatível com o contrato canônico Scene V2 sem duplicar modelos.
2. Preservação absoluta da narração original em todos os cenários.
3. Validação determinística de IDs, ordem, durações e timestamps contínuos.
4. Suporte explícito aos perfis `PILOT_5MIN`, `STANDARD_10MIN` e `DOCUMENTARY_20MIN`.
5. Zero chamadas pagas de IA na suíte de testes.
6. Nenhuma alteração nos módulos proibidos (Copywriter, `desktop_node.py`, FFmpeg, QC, uploader, banco de produção).

---

## 9. Estratégia de Testes (Unitários e Integração)

Todos os testes deverão rodar offline com mocks determinísticos de LLM, cobrindo obrigatoriamente:
1. Roteiro com poucas cenas (ex.: 2–3 cenas).
2. Roteiro extenso (ex.: 30+ cenas).
3. Cenas com duração explícita preservada.
4. Duração total incompatível com o perfil (deve falhar deterministicamente).
5. IDs duplicados (rejeição).
6. Cenas fora de ordem (rejeição ou falha de validação).
7. Narração alterada pelo LLM (rejeição imediata).
8. Continuidade visual (detecção/prevenção de redundância consecutiva).
9. Campos V2 ausentes (rejeição).
10. Resposta incompleta do LLM (tratamento de erro explícito).
11. Perfil `PILOT_5MIN`.
12. Perfil `STANDARD_10MIN`.
13. Perfil `DOCUMENTARY_20MIN`.

