# SPRINT 2.4 — AI VIDEO DIRECTOR ORCHESTRATOR (`AIVideoDirector`)

**Fase:** 2 — AI Video Director  
**Sprint:** 2.4  
**Componentes:**
- `agents/video_director/director_engine.py`
- `agents/video_director/master_plan_models.py`
- `agents/video_director/__init__.py`

---

## 1. Objetivo

Integrar **NarrativeAnalyzer**, **VisualDirector** e **ScenePlanner** em um fluxo único, determinístico, validado e observável, produzindo o artefato consolidado **Master AV Plan** (`MasterAVPlan`), preservando integralmente o contrato **Scene V2** (`shared.models.Scene`) e o **Pipeline B** operacional.

---

## 2. Arquitetura e Fluxo de Execução

```text
ROTEIRO VALIDADO (script_data)
       |
       v  [input_validation + deep copy]
NARRATIVE ANALYZER (NarrativeAnalysisResult)
       |
       v  [visual_director.direct]
VISUAL DIRECTOR (VisualDirectionResult + VisualBible)
       |
       v  [scene_planner.plan_scenes]
SCENE PLANNER (ScenePlanResult)
       |
       v  [consolidated validation + JSON round-trip]
MASTER AV PLAN (MasterAVPlan)
```

---

## 3. Contratos de Entrada e Saída

### 3.1 `AIVideoDirector`
```python
director = AIVideoDirector(
    narrative_analyzer=None,
    visual_director=None,
    scene_planner=None,
    llm_client=None,
    timeout=30.0,
)

master_plan = director.create_master_plan(
    script_data=script_data,
    production_profile="PILOT_5MIN",
    planning_params=None,
    visual_language_override=None,
)
```

### 3.2 `MasterAVPlan`
Campos principais:
- `plan_id`: identificador único (`avplan_<hex>`) sem dados sensíveis.
- `schema_version`: versão explícita do contrato (`"2.4.0"`).
- `created_at`: timestamp UTC ISO-8601.
- `production_profile`: perfil utilizado (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`).
- `central_theme`: tema central extraído pelo `NarrativeAnalyzer`.
- `visual_bible`: `VisualBible` consolidada pelo `VisualDirector`.
- `total_scenes`: quantidade total de cenas planejadas.
- `total_duration_seconds`: duração total consolidada.
- `scenes`: lista ordenada de `PlannedScene`.
- `validation_summary`: resumo das validações determinísticas.
- `execution_metadata`: `ExecutionMetadata` com métricas monotônicas por etapa e warnings.
- `to_canonical_scenes()`: conversão direta para `List[shared.models.Scene]` (Scene V2).

---

## 4. Validações Consolidadas

O `MasterAVPlan` só é aprovado quando todas as condições abaixo são satisfeitas:
1. Todas as etapas obrigatórias concluídas (`narrative_analyzer`, `visual_director`, `scene_planner`, `master_plan_validation`).
2. Quantidade de cenas consistente entre entrada, análise narrativa, direção visual e planejamento.
3. IDs únicos e na mesma ordem entre todas as etapas.
4. Nenhuma cena inventada ou descartada.
5. Narração original preservada integralmente (verbatim).
6. Durações numéricas, finitas e positivas (`> 0`, sem `NaN`, `Inf` ou `bool`).
7. Soma das durações das cenas igual a `total_duration_seconds` dentro da tolerância.
8. `production_profile` válido e consistente.
9. `VisualBible` presente e válida.
10. Saída 100% serializável em JSON.

---

## 5. Tratamento de Erros, Timeout e Observabilidade

- **`AIVideoDirectorError`**: preserva `stage`, `error_type` e `message` sanitizada via `sanitize_secret()`.
- **Timeout por chamada LLM e deadline global**: `AIVideoDirector` propaga `timeout=self.timeout` para `NarrativeAnalyzer`, `VisualDirector` e `ScenePlanner`. Clientes LLM síncronos devem declarar explicitamente o parâmetro `timeout` em sua assinatura (não basta aceitar `**kwargs`); caso contrário, são rejeitados antes da chamada. No `LLMClient.complete()`, um deadline global (`time.monotonic() + timeout`) controla toda a cascata de provedores e os retries: o tempo restante é propagado a cada tentativa e novas tentativas ou backoffs são interrompidos imediatamente quando o prazo expira.
- **Timeout assíncrono**: chamadas assíncronas no `ScenePlanner` utilizam `asyncio.wait_for(..., timeout=self.timeout)`.
- **Limite de duração por etapa e limitações de cancelamento síncrono**: `_run_with_timeout()` executa cada etapa diretamente na thread atual (sem `ThreadPoolExecutor`), evitando threads abandonadas em segundo plano, pois `Future.cancel()` não interrompe funções síncronas em execução. Além do timeout nativo na camada LLM, `_run_with_timeout()` mede o tempo decorrido com `time.monotonic()` e levanta `AIVideoDirectorError(error_type="TimeoutError")` caso a etapa ultrapasse `self.timeout`.
- **Observabilidade**: `StageExecutionMetrics` registra `duration_seconds` (relógio monotônico `time.monotonic()`), `status`, `scene_count_in` e `scene_count_out` sem expor narração completa, prompts ou credenciais.

---

## 6. Testes e Critérios de Aceite

- Suíte dedicada: `tests/test_ai_video_director_engine.py` (testes cobrindo sucesso, integridade, falhas por etapa, timeout nativo/assíncrono/rejeição de cliente sem timeout, ausência de threads abandonadas, imutabilidade, injeção de dependências e integração real com LLM determinístico).
- Preservação integral do Pipeline B (`desktop_node.py`, `cloud_hub/main.py`, `cloud_hub/core/copywriter.py` intocados).
