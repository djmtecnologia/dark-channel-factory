# SPRINT 2.7 — AUDITORIA DE COMPATIBILIDADE DE PERFIS E VALIDADORES

**Data:** 2026-11-17  
**Modo:** Somente leitura (sem alterações em código ou testes)  
**Referência de falha:** `tests/test_pilot_profile_and_contracts.py::TestValidatorsIntegration::test_content_validator_with_pilot_profile`

---

## 1. Resumo da Falha Observada (`PILOT_5MIN`)

- **Resultado reportado na Etapa 0:** 128 testes aprovados, 1 teste falhou (`test_content_validator_with_pilot_profile`).
- **Causa raiz comprovada no código:**
  1. **Prompt do LLM hardcoded para 1800 palavras:** Em `agents/content_validator/agent.py` (linha 44), `SYSTEM_PROMPT` instrui o LLM a reprovar qualquer roteiro com `- Less than 1800 words total`. O `user_prompt` (linhas 130–151) envia `WORD COUNT: {word_count}` (655 palavras no teste), mas **não informa o `production_profile` nem os limites do perfil**.
  2. **Dependência de LLM externo em testes:** Em `agents/content_validator/agent.py` (linha 125), `llm = get_llm_client()` e `llm.complete(...)` são chamados mesmo quando `os.environ["MOCK_LLM"] = "true"` está definido (como em `tests/test_pilot_profile_and_contracts.py:23`). Isso expõe o teste a timeout, indisponibilidade e saldo insuficiente de provedores externos.
  3. **Divergência entre validação determinística e LLM:** A validação determinística (`validate_script_word_count` em `shared/production_profiles.py:217-222`) aprova 600–850 palavras para `PILOT_5MIN`, mas o `SYSTEM_PROMPT` do LLM continua exigindo 1800 palavras.

---

## 2. Mapeamento dos Perfis (`shared/production_profiles.py`)

| Perfil | Duração (s) | Palavras (min–max) | Cenas (min–max) | Resolução | Orientação |
|---|---|---|---|---|---|
| `PILOT_5MIN` | 270–330s | 600–850 | 12–18 | 1280x720 | Horizontal (`is_vertical=False`) |
| `STANDARD_10MIN` | 480–720s | 1200–1600 | 30–45 | 1280x720 | Horizontal (`is_vertical=False`) |
| `DOCUMENTARY_20MIN` | 900–1800s | 2200–4000 | 60–90 | 1280x720 | Horizontal (`is_vertical=False`) |
| `SHORTS` | 20–59s | 100–200 | 3–8 | 720x1280 | Vertical (`is_vertical=True`) |

### Velocidade de Fala e Coerência Duração × Palavras
- `agents/content_validator/agent.py` (linha 201) estima duração com `word_count / 2.5` (~150 wpm / 2,5 palavras/s).
- **Coerência a 150 wpm:**
  - `PILOT_5MIN`: 600–850 palavras → 240–340s (compatível com 270–330s; 655 palavras ≈ 262s).
  - `STANDARD_10MIN`: 1200–1600 palavras → 480–640s (compatível com 480–720s).
  - `DOCUMENTARY_20MIN`: 2200–4000 palavras → 880–1600s (compatível com 900–1800s).
  - `SHORTS`: 100–200 palavras → 40–80s (200 palavras a 150 wpm = 80s, acima do máximo de 59s; `max_words` deve ser tratado com atenção na narração ou mantido como teto de roteiro com QC validando duração <= 59s).

---

## 3. Divergências Identificadas Entre Validadores

1. **`agents/content_validator/agent.py`:**
   - `MIN_WORD_COUNT = 1800` (linha 21) é usado apenas como fallback legado (linha 75), mas o `SYSTEM_PROMPT` (linha 44) mantém `- Less than 1800 words total` para todos os perfis.
   - Não respeita `MOCK_LLM=true` antes de chamar `get_llm_client()`.
2. **`shared/production_profiles.py`:**
   - `validate_script_word_count()` (linhas 217–222) valida apenas `word_count < config.min_words`, **não valida `word_count > config.max_words`**.
3. **`shared/script_validator.py`:**
   - Valida `_MIN_WORDS_PER_SCENE = 15` (linha 26) e `validate_script_word_count` (linha 354), mas não valida `validate_scene_count()`.
4. **`agents/validator/qc_agent.py`:**
   - Valida duração via `validate_duration()` (linha 123) e orientação/resolução via `is_vertical` (linhas 135–147). Está alinhado com `shared/production_profiles.py`, mas se o roteiro tiver excesso de palavras (não bloqueado no `script_validator`), o MP4 pode estourar `max_duration_s` no QC.

---

## 4. Recomendações Determinísticas e Testáveis (Para Implementação na Sprint 2.7)

1. **`agents/content_validator/agent.py`:**
   - Parametrizar o prompt do LLM com `profile_label`, `min_words` e `max_words` do `resolved_profile` (ou usar `1800` apenas quando `resolved_profile is None`).
   - Respeitar `MOCK_LLM=true` retornando avaliação heurística determinística sem chamadas externas.
2. **`shared/production_profiles.py`:**
   - Avaliar inclusão de validação de `max_words` (ou warning/erro configurável) em `validate_script_word_count()`.
3. **Testes:**
   - Todos os testes de `test_pilot_profile_and_contracts.py` devem rodar 100% offline com `MOCK_LLM=true` ou mocks explícitos, cobrindo os 4 perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`).
