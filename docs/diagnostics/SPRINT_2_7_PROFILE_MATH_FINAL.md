# SPRINT 2.7 — MATEMÁTICA DEFINITIVA DOS PERFIS (ETAPA 0.4)

**Status:** Especificação Técnica Final (Somente Leitura / Documental)  
**Escopo:** Tema 1 — `shared/production_profiles.py`, `shared/script_validator.py`, `agents/content_validator/agent.py`, `agents/validator/qc_agent.py`, `agents/video_editor/tts_engine.py`.

---

## 1. EVIDÊNCIAS COMPROVADAS NO CÓDIGO

- **[COMPROVADO]** `shared/production_profiles.py` define 4 perfis imutáveis em `PROFILES`:
  - `PILOT_5MIN`: `270.0–330.0s` | `600–850` palavras | `12–18` cenas | `1280x720`
  - `STANDARD_10MIN`: `480.0–720.0s` | `1200–1600` palavras | `30–45` cenas | `1280x720`
  - `DOCUMENTARY_20MIN`: `900.0–1800.0s` | `2200–4000` palavras | `60–90` cenas | `1280x720`
  - `SHORTS`: `20.0–59.0s` | `100–200` palavras | `3–8` cenas | `720x1280`
- **[COMPROVADO]** Não existe taxa universal de palavras por minuto (WPM) configurada no código. Em `agents/content_validator/agent.py` (linha 21), há apenas um comentário legado: `MIN_WORD_COUNT = 1800 # Mínimo para 15 minutos de vídeo a ~120 wpm`.
- **[COMPROVADO]** Inspeção de `agents/video_editor/tts_engine.py`:
  - **Velocidade padrão:** `rate: str = "+0%"` em `_build_ssml_for_edge`, `_generate_tts_async`, `generate_scene_audio` e `generate_full_narration`.
  - **Modificações de velocidade e cadência:** Tags dramáticas alteram dinamicamente `rate` para `-12%` (com `pitch="-4Hz"`, `volume="+15%"`) ou `+18%`.
  - **Pausas SSML:** `[PAUSA_LONGA]` → `1200ms` | `[PAUSA]` → `500ms` | `[SUSPIRO]` → `700ms` | `. ` → `450ms` | `? ` → `550ms` | `! ` → `550ms` | `...` → `650ms` | `, ` → `180ms`.
  - **Disponibilidade de medição pré-montagem:** `generate_full_narration()` retorna a lista `audio_paths` dos arquivos gerados por cena antes da montagem final do vídeo, permitindo medição futura da duração real do áudio antes da renderização. Atualmente, a medição ocorre apenas pós-renderização em `agents/validator/qc_agent.py` (linhas 106–129) via `ffprobe` (`format=duration`) chamando `validate_duration(duration_s=duration, ...)`.
- **[COMPROVADO]** `validate_script_word_count` em `shared/production_profiles.py` (linhas 217–222) valida apenas o limite inferior (`word_count < config.min_words`), não bloqueando excesso de palavras (`max_words`).

---

## 2. CENÁRIOS MATEMÁTICOS DE ANÁLISE (HIPÓTESES)

> **[HIPÓTESE]** O overhead de **+12%** para pausas SSML sobre o tempo puro de fala é uma **hipótese de trabalho**, não um fato comprovado, até que exista medição empírica com amostras reais do TTS.

Adotam-se **3 cenários explícitos de análise**:
- **Cenário A (Lento / Dramático):** `120 WPM` + 12% pausas → **~107 WPM efetivos** (`0,5607 s/palavra`).
- **Cenário B (Moderado / Padrão):** `145 WPM` + 12% pausas → **~129 WPM efetivos** (`0,4651 s/palavra`).
- **Cenário C (Rápido / Dinâmico):** `170 WPM` + 12% pausas → **~152 WPM efetivos** (`0,3947 s/palavra`).

### 2.1. Faixas Teóricas Compatíveis por Cenário (em Palavras)

| Perfil | Duração (s) | Cenário A (107 WPM ef.) | Cenário B (129 WPM ef.) | Cenário C (152 WPM ef.) | Interseção Segura (A ∩ B ∩ C) |
|---|---:|---:|---:|---:|---|
| **PILOT_5MIN** | 270–330s | 481–588 pal. | 580–709 pal. | 684–836 pal. | **Vazia** (máx. de A = 588 < mín. de C = 684) |
| **STANDARD_10MIN** | 480–720s | 856–1.284 pal. | 1.032–1.548 pal. | 1.216–1.824 pal. | **1.216–1.284 pal.** |
| **DOCUMENTARY_20MIN** | 900–1.800s | 1.605–3.210 pal. | 1.935–3.870 pal. | 2.280–4.560 pal. | **2.280–3.210 pal.** |
| **SHORTS** | 20–59s | 36–105 pal. | 43–127 pal. | 51–149 pal. | **51–105 pal.** |

**Conclusão Matemática:** Uma faixa estática de palavras **não garante** conformidade simultânea em todos os cenários de velocidade (especialmente em `PILOT_5MIN`, onde a interseção entre A e C é vazia). Exemplo: `700 palavras` a `107 WPM efetivos` resultam em `~393s`, ultrapassando o teto de `330s`. Portanto, **nenhuma faixa de palavras deve ser classificada como garantia absoluta de duração**.

---

## 3. AVALIAÇÃO ESPECÍFICA DE `SHORTS` (20s–59s)

| Palavras | Cenário A (107 WPM ef.) | Cenário B (129 WPM ef.) | Cenário C (152 WPM ef.) | Status nos 3 Cenários |
|---:|---:|---:|---:|---|
| **55** | 30,8s | 25,6s | 21,7s | Dentro de 20–59s em todos |
| **80** | 44,9s | 37,2s | 31,6s | Dentro de 20–59s em todos |
| **100** | 56,1s | 46,5s | 39,5s | Dentro de 20–59s em todos (margem de 2,9s em A) |
| **110** | **61,7s** | 51,2s | 43,4s | **Estoura 59s no Cenário A** |
| **120** | **67,3s** | 55,8s | 47,4s | **Estoura 59s no Cenário A** |
| **125** | **70,1s** | 58,1s | 49,3s | **Estoura 59s no Cenário A** |

- **Decisão:** `120 palavras` **não é seguro** para fala lenta (`67,3s > 59s`).
- **Meta conservadora para Copywriter (`SHORTS`):** **55–100 palavras** (segura nos 3 cenários, de `21,7s` a `56,1s`).
- **Limite heurístico do Content Validator (`SHORTS`):** Warning acima de `105 palavras`; bloqueio heurístico apenas em desvios extremos (ex: `> 125 palavras`). A validação final obrigatória permanece sendo a **duração real <= 59,0s**.

---

## 4. SEPARAÇÃO OBRIGATÓRIA DE CONCEITOS (PROPOSTA)

A especificação distingue três níveis independentes:

1. **A. Meta de Geração do Copywriter (Orientação):**
   - `PILOT_5MIN`: **550–650 palavras** (alvo central no Cenário B; exige verificação de áudio).
   - `STANDARD_10MIN`: **1.150–1.300 palavras** (cobre B com segurança e minimiza estouro em A).
   - `DOCUMENTARY_20MIN`: **2.300–3.200 palavras** (dentro da interseção segura A ∩ B ∩ C).
   - `SHORTS`: **55–100 palavras** (dentro da interseção segura A ∩ B ∩ C).
2. **B. Limite Heurístico do Content Validator (Filtro Inicial):**
   - Emite `warning` quando `word_count` ultrapassa a meta superior do perfil, e bloqueio heurístico apenas em desvios extremos (ex: `> +20%` acima do teto heurístico), **nunca substituindo a validação de duração real**.
3. **C. Limite Rígido de Qualidade (Autoridade Máxima):**
   - Duração real do áudio TTS gerado (pré-montagem) e duração real do vídeo renderizado via `ffprobe` no `qc_agent.py`, comparadas estritamente contra `min_duration_s` e `max_duration_s` de `PROFILES`.

---

## 5. PLANO FUTURO DE CALIBRAÇÃO EMPÍRICA DO TTS (NÃO EXECUTADO NESTA ETAPA)

Como não há amostras reais suficientes medidas no repositório:
1. Gerar amostras representativas por perfil (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`) usando `tts_engine.py` com pontuação e tags SSML reais.
2. Medir a duração real de cada áudio gerado (`ffprobe` sobre `audio_paths`).
3. Comparar contagem de palavras, quantidade de pausas SSML e duração observada.
4. Calcular o WPM efetivo observado e o overhead real de pausas por perfil.
5. Recalibrar as metas de geração do `Copywriter` e os limites heurísticos do `ContentValidator` com base nos dados medidos.
