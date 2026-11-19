# SPRINT 2.7 — COMPATIBILIDADE FINAL DE PERFIS E VALIDADORES (T1)

**Data:** 2026-11-17  
**Modo:** Somente leitura (análise pré-implementação)  
**Status:** Pronto para aprovação  

---

## 1. Resumo Executivo

Os quatro perfis de produção (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`) estão definidos em `shared/production_profiles.py`, porém há divergências entre:
1. O prompt hardcoded de `agents/content_validator/agent.py` (linha 44: `- Less than 1800 words total`).
2. A ausência de respeito a `MOCK_LLM=true` em `agents/content_validator/agent.py` (linha 125).
3. A validação apenas de `min_words` (sem `max_words`) em `shared/production_profiles.py:217-222` (`validate_script_word_count`).
4. A ausência de validação de `scene_count` em `shared/script_validator.py`.

---

## 2. Análise Matemática: Palavras × Duração

**Taxa de referência no repositório:** `agents/content_validator/agent.py:201` usa `word_count / 2.5` (2,5 palavras/segundo = **150 WPM**).

Na prática de narração TTS (`edge-tts` / `generate_voice_master` em `desktop_node.py:187`), com pausas de pontuação e respiros entre cenas (~5–10% de overhead), a taxa efetiva varia entre **135 e 160 WPM** (2,25 a 2,67 palavras/s).

| Perfil | Palavras (doc) | Duração (doc) | Duração a 150 WPM (2,5 w/s) | Duração a 135 WPM (2,25 w/s) | Duração a 160 WPM (2,67 w/s) | Cenas | Parecer |
|---|---:|---:|---:|---:|---:|---:|---|
| `PILOT_5MIN` | 600–850 | 270–330 s | 240–340 s | 267–378 s | 225–319 s | 12–18 | **Coerente.** Centro (725 palavras) = 290 s. |
| `STANDARD_10MIN` | 1.200–1.600 | 480–720 s | 480–640 s | 533–711 s | 450–600 s | 30–45 | **Coerente.** Centro (1.400 palavras) = 560 s. |
| `DOCUMENTARY_20MIN` | 2.200–4.000 | 900–1.800 s | 880–1.600 s | 978–1.778 s | 825–1.500 s | 60–90 | **Coerente.** Centro (3.100 palavras) = 1.240 s. |
| `SHORTS` | 100–200 | 20–59 s | 40–80 s | 44–89 s | 37–75 s | 3–8 | **Atenção:** 200 palavras a 150 WPM = 80 s (> 59 s). |

### Recomendação Técnica por Perfil

- **`PILOT_5MIN`:** Manter 600–850 palavras e 270–330 s.
- **`STANDARD_10MIN`:** Manter 1.200–1.600 palavras e 480–720 s.
- **`DOCUMENTARY_20MIN`:** Manter 2.200–4.000 palavras e 900–1.800 s.
- **`SHORTS`:** A restrição de **59 segundos** é limite efetivo de produção (YouTube Shorts). Recomenda-se:
  - Manter `max_words = 200` apenas se houver `qc_agent` bloqueando áudio/vídeo > 59 s, **ou** recomendar ao Copywriter alvo de **110–145 palavras** (~44–58 s a 150 WPM) para evitar reprovações em cascata no QC.
  - Em `shared/production_profiles.py`, `validate_script_word_count()` deve validar `min_words <= word_count <= max_words` (com tolerância ou erro claro).

---

## 3. Evidências e Mudanças Propostas (T1)

| Arquivo | Símbolo / Linha | Comportamento Atual | Mudança Proposta | Risco |
|---|---|---|---|---|
| `agents/content_validator/agent.py` | `SYSTEM_PROMPT` (L44), `validate_content` (L125) | Exige 1800 palavras no prompt; chama `get_llm_client()` mesmo com `MOCK_LLM=true`. | Parametrizar prompt com `min_words`/`max_words` do perfil (fallback 1800 apenas se `profile is None`); retornar heurística determinística quando `MOCK_LLM=true`. | BAIXO |
| `shared/production_profiles.py` | `validate_script_word_count` (L217-222) | Valida apenas `word_count < config.min_words`. | Validar também `word_count > config.max_words`. | BAIXO |
| `shared/script_validator.py` | `validate_script` (L354) | Não valida `scene_count` contra o perfil. | Validar `min_scenes <= len(scenes) <= max_scenes` quando perfil informado. | BAIXO |
| `tests/test_pilot_profile_and_contracts.py` | `TestValidatorsIntegration` | Falha em `test_content_validator_with_pilot_profile`. | Garantir execução 100% offline com `MOCK_LLM=true` cobrindo os 4 perfis. | BAIXO |
