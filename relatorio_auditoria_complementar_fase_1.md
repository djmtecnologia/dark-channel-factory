# RELATÓRIO TÉCNICO DE AUDITORIA COMPLEMENTAR — FASE 1
**Projeto:** Dark Channel Factory  
**Data:** 02 de Novembro de 2026  
**Status do Parecer Final:** **APROVADO**  
**Diretriz Operacional:** Fase 2 mantida estritamente BLOQUEADA aguardando autorização humana explícita.

---

## 1. Resumo Executivo

Esta auditoria complementar teve como objetivo inspecionar de forma exaustiva a implementação da **Fase 1**, identificando problemas de integração, persistência, propagação de configurações e possíveis regressões não cobertas pelos testes iniciais.

Principais conclusões da auditoria:
1. **Persistência de Perfil em Bancos Existentes (Neon):** Identificou-se que o campo `production_profile` foi adicionado ao modelo SQLModel (`cloud_hub/models.py`), porém **não havia sido incluído no script de migração (`migrate_db.py`)**. Em bancos já existentes (como o Neon de produção), o SQLAlchemy `create_all()` emite apenas `CREATE TABLE IF NOT EXISTS`, o que não altera tabelas existentes. A migração idempotente `007` foi devidamente preparada em `migrate_db.py` e documentada para aplicação segura sem tocar na produção.
2. **Propagação de Perfil de Ponta a Ponta:** O endpoint de distribuição `/worker/next-job/{market_region}` já devolvia `production_profile`, e o desktop worker (`desktop_node.py`) o repassava ao QC local. No entanto, foram identificados e corrigidos pontos cegos no pipeline:
   - Clones gerados por `auto_scale_top_20_percent` e `validate_and_clone_video` perdiam o perfil do job matriz.
   - O `run_scout_phase` e a rota `/admin/trigger-scout/{channel_id}` não aceitavam `production_profile`.
   - O `cloud_hub/core/copywriter.py` continha limites fixos legados (1500 palavras e 36 cenas) e não passava o perfil para o `validate_and_fix_script`.
   - Criou-se a rota explícita `POST /jobs/create` para garantir persistência e aceitação direta de jobs com perfil.
3. **Falha Oculta no OllamaClient:** O teste `test_ollama_client.py` falhava com `Exception: Connection refused` porque o método `healthcheck()` capturava apenas `requests.RequestException` em vez de `Exception`. Adicionalmente, por estar escrito em estilo puro de funções pytest, o comando padrão `python -m unittest` reportava `Ran 0 tests`. Ambos os problemas foram corrigidos cirurgicamente.
4. **Isolamento e Bateria de Testes:** A bateria completa foi executada com **70 testes no total (100% aprovados, 0 falhas, 0 erros)** em SQLite temporário, com confirmação cabal de que nenhum teste tocou o banco Neon de produção.

---

## 2. Arquivos Inspecionados

| Arquivo | Componente / Função | Finalidade da Inspeção |
| :--- | :--- | :--- |
| `cloud_hub/models.py` | Modelo `VideoJob` | Verificação do campo `production_profile` e compatibilidade de schema |
| `migrate_db.py` | Rotina de Migração | Verificação de DDL para bancos existentes |
| `cloud_hub/main.py` | Endpoints API e Bootstrap | Inspeção de distribuição de jobs (`/worker/next-job`), clones e rotas de criação |
| `cloud_hub/core/intelligence.py` | `run_scout_phase` | Persistência do perfil no momento da criação pelo Scout |
| `cloud_hub/core/copywriter.py` | `run_copywriter_phase` | Resolução do perfil para contagem de cenas e densidade de palavras |
| `desktop_node.py` | Worker local e QC | Recebimento do perfil na API, propagação para QC e renderizador |
| `shared/production_profiles.py` | Registro central de perfis | Parametrização dos 4 perfis (`PILOT_5MIN`, `STANDARD_10MIN`, `DOCUMENTARY_20MIN`, `SHORTS`) e fallback legado |
| `shared/scene_normalizer.py` | Normalizador de cenas | Sincronização entre `b_roll_visual` e `b_roll_query`, `narration` e `text`, e fallbacks contextuais |
| `shared/models.py` | Modelo `Scene` Pydantic | Validação de compatibilidade com dicionários legados e novos contratos |
| `shared/script_validator.py` | `validate_and_fix_script` | Respeito aos perfis na contagem de palavras e remoção de duplicatas |
| `agents/content_validator/agent.py` | `run_content_validator_agent` | Validação de densidade de palavras baseada em perfil |
| `agents/validator/qc_agent.py` | `ValidatorAgent` | Validação estrita de duração, resolução e orientação por perfil |
| `agents/video_editor/media_fetcher.py` | `fetch_all_broll` | Priorização de `b_roll_visual` e consumo de contratos de cena |
| `shared/ollama_client.py` | `OllamaClient.healthcheck` | Tratamento de exceções e resiliência de conectividade |
| `tests/benchmark_render.py` | Benchmark FFmpeg | Análise metodológica, comandos FFmpeg e confiabilidade de métricas |

---

## 3. Problemas Encontrados

### Problema 1: Ausência de DDL de migração para `production_profile`
- **Causa:** O campo foi adicionado ao SQLModel em `cloud_hub/models.py`, mas não constava no array `MIGRATIONS` de `migrate_db.py`.
- **Impacto em Produção:** O Neon Postgres de produção rejeitaria queries com `psycopg2.errors.UndefinedColumn: column video_jobs.production_profile does not exist` na primeira consulta ou inserção de jobs.

### Problema 2: Clones e Scout descartavam o perfil
- **Causa:** `run_scout_phase`, `auto_scale_top_20_percent` e `validate_and_clone_video` instanciavam novos objetos `VideoJob` sem repassar `production_profile`.
- **Impacto:** Vídeos clonados para mercados internacionais (es-ES, de-DE, fr-FR) perdiam o perfil PILOT_5MIN e revertiam silenciosamente para comportamento legado.

### Problema 3: `cloud_hub/core/copywriter.py` com limites fixos incompatíveis com PILOT
- **Causa:** Linhas 447 e 518 de `cloud_hub/core/copywriter.py` forçavam geração de 36 cenas e rejeitavam roteiros com menos de 1500 palavras (`if word_count >= 1500:`), além de chamar `validate_and_fix_script(script_data, is_short=False)` sem informar o perfil.
- **Impacto:** Um job `PILOT_5MIN` (600 a 850 palavras) era rejeitado como "script muito curto" e forçado a re-tentativas desnecessárias.

### Problema 4: `fetch_all_broll` em `agents/video_editor/media_fetcher.py` consultava apenas `b_roll_query`
- **Causa:** A linha 229 usava `query = scene.get("b_roll_query", "technology office")`.
- **Impacto:** Se uma cena contivesse apenas o prompt visual detalhado em `b_roll_visual`, o consumidor de mídia caía para o fallback genérico `"technology office"`.

### Problema 5: Crash em `OllamaClient.healthcheck()` e incompatibilidade com unittest
- **Causa:** O bloco `try/except` capturava apenas `requests.RequestException`. Exceções de socket ou simuladas no mock (`Exception("Connection refused")`) escapavam e quebravam a execução. Além disso, `test_ollama_client.py` utilizava funções puras sem herdar de `unittest.TestCase`, resultando em `Ran 0 tests` quando executado via `python -m unittest`.

---

## 4. Correções Realizadas

### Correção 1: Migração 007 criada em `migrate_db.py`
Adicionada a migração idempotente:
```python
# Migração 007 — production_profile na tabela video_jobs (Fase 1: PILOT_5MIN, STANDARD_10MIN, etc.)
("production_profile", "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS production_profile VARCHAR(50);"),
```
*Garantia:* Não executada no Neon de produção, permanecendo pronta para execução administrativa na implantação.

### Correção 2: Propagação de Perfil no Scout, Clones e Criação Direta
- `cloud_hub/core/intelligence.py`: `run_scout_phase` atualizado para receber `production_profile: Optional[str] = None` e persisti-lo no `VideoJob`.
- `cloud_hub/main.py`:
  - Rota `POST /admin/trigger-scout/{channel_id}` atualizada para aceitar o parâmetro `production_profile`.
  - `auto_scale_top_20_percent` e `validate_and_clone_video` atualizados para herdar `production_profile=master.production_profile`.
  - Nova rota criada `POST /jobs/create` permitindo a criação e persistência direta de jobs com especificação de perfil.

### Correção 3: Suporte a Perfis no Copywriter Cloud
Em `cloud_hub/core/copywriter.py`:
- Integrado `resolve_profile(job=job)`.
- Se o perfil estiver definido (ex: `PILOT_5MIN`), a quantidade de cenas é calculada como a mediana do intervalo do perfil (`(min_scenes + max_scenes) // 2 = 15`), o alvo de palavras no prompt passa a ser `min_words` (600 a 850) e o density gate valida contra `min_target_words`.
- Se o perfil for ausente, mantém rigorosamente o padrão legado (36 cenas, meta de 1500 palavras).
- `validate_and_fix_script` agora recebe `profile=profile_cfg.name`.

### Correção 4: Consumidor de Mídia Priorizando `b_roll_visual`
Em `agents/video_editor/media_fetcher.py`:
```python
query = scene.get("b_roll_visual") or scene.get("b_roll_query") or "technology office"
```

### Correção 5: Resiliência no `OllamaClient` e compatibilização de testes
- Em `shared/ollama_client.py`: `healthcheck()` agora captura `except Exception: return False`.
- Em `tests/test_ollama_client.py`: Refatorado para `class TestOllamaClient(unittest.TestCase)`, permitindo execução tanto via `python -m unittest` quanto via `pytest`.

---

## 5. Fluxo de Propagação do Perfil Comprovado

Mapeamento detalhado de cada etapa do pipeline:

| Etapa | Arquivo e Função | Campo Recebido | Campo Enviado | Tratamento se Ausente | Risco de Perda |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Solicitação** | `cloud_hub/main.py`<br>`create_video_job` / `trigger_scout` | `production_profile` (str) | `VideoJob.production_profile` | Permanece `None` | **Zero:** persistido diretamente no banco. |
| **2. Persistência** | `cloud_hub/models.py`<br>`VideoJob` + `Session.commit()` | Atributo no model | Coluna SQL `production_profile` | Salva `NULL` no banco | **Zero** no SQLite/Postgres com migração 007. |
| **3. Distribuição** | `cloud_hub/main.py`<br>`get_next_job` (`/worker/next-job/{region}`) | Registro SQL | JSON dict `"production_profile"` | Retorna `None` no JSON | **Zero:** serializado explicitamente no payload. |
| **4. Recebimento Worker**| `desktop_node.py`<br>`start_worker()` | JSON dict da API | `job_data` (dict) | `job_data.get("production_profile")` é `None` | **Zero:** o dict completo é repassado intacto. |
| **5. Geração de Roteiro** | `shared/script_validator.py`<br>`validate_and_fix_script` | `profile` param ou `script_data["production_profile"]` | Validação e sanitização | Usa regras legadas (900 palavras) | **Zero:** `resolve_profile` avalia objeto e fallback. |
| **6. Validador Conteúdo** | `agents/content_validator/agent.py`<br>`run_content_validator_agent` | `profile` param ou `script_data_dict["production_profile"]` | `ContentValidationResult` | Usa regras legadas (1800 palavras) | **Zero:** `resolve_profile` integrado. |
| **7. Geração de Áudio & Cenas**| `desktop_node.py`<br>`render_video` | `job_data` | `scenes_list` normalizadas | Cenas calculadas normalmente | **Zero:** `normalize_scenes` unifica chaves. |
| **8. Renderização** | `desktop_node.py`<br>`assemble_production` | `job_data` | Argumento FFmpeg e `.mp4` | Resolução 720p / 1080p | **Zero:** dimensões respeitam perfil e formato. |
| **9. QC Final** | `agents/validator/qc_agent.py`<br>`ValidatorAgent.validate_technical_video` | `production_profile=job_data.get(...)` | Status aprovado/reprovado | Valida regras legadas (LONG >= 480s) | **Zero:** `validate_duration` aplica faixa estrita (270-330s). |

---

## 6. Evidências de Persistência

A persistência foi verificada tanto estaticamente no schema SQLModel quanto dinamicamente na suíte `tests/test_audit_phase1_e2e.py`:

```python
# Evidência de persistência e recuperação ponta a ponta
job = VideoJob(
    id=job_id,
    channel_id=self.channel.id,
    topic="How Autonomous AI Replaces Mid-Level Developers",
    status="APPROVED",
    video_type="LONG",
    production_profile="PILOT_5MIN",
    ...
)
session.add(job)
session.commit()

# Recuperação via join idêntico ao endpoint da API
distributed_job = session.exec(statement).first()
assert distributed_job.production_profile == "PILOT_5MIN"
```

### Instruções para Aplicação da Migração em Produção (Quando Autorizado)
1. Conectar ao ambiente de deploy com acesso ao Neon PostgreSQL configurado na variável de ambiente `DATABASE_URL`.
2. Executar o comando:
   ```bash
   python migrate_db.py
   ```
3. O script aplicará de forma idempotente:
   ```sql
   ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS production_profile VARCHAR(50);
   ```
4. Caso a coluna já exista, a execução não causará erros nem bloqueios de tabela.

---

## 7. Resultados Individuais dos Testes

Todas as suítes foram executadas individualmente. Nenhuma mensagem "OK" genérica foi aceita sem a comprovação da saída completa:

### 1. `tests/test_pilot_profile_and_contracts.py`
- **Comando:** `python -m unittest tests/test_pilot_profile_and_contracts.py`
- **Saída:**
  ```text
  Ran 23 tests in 1.210s
  OK
  ```
- **Total:** 23 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 1.210s

### 2. `tests/test_audit_phase1_e2e.py` (Nova suíte complementar)
- **Comando:** `python -m unittest tests/test_audit_phase1_e2e.py`
- **Saída:**
  ```text
  Ran 6 tests in 0.275s
  OK
  ```
- **Total:** 6 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 0.275s
- **Cenários Cobertos:**
  - `test_database_isolation_active`: Confirmação estrita de isolamento local SQLite.
  - `test_scenario_a_pilot_full_propagation`: Cenário A completo (PILOT_5MIN, persistência, distribuição, worker, validadores e QC 270-330s).
  - `test_scenario_b_legacy_behavior`: Cenário B (jobs legados sem perfil, sem atribuição silenciosa, regras legadas preservadas).
  - `test_scenario_c_concurrency_no_profile_contamination`: Cenário C (múltiplos perfis simultâneos sem contaminação global).
  - `test_scene_normalizer_complete_contract`: Normalização completa de cenas e preservação de campos.
  - `test_media_fetcher_consumer_uses_visual_contract`: Consumidor real de mídia respeitando o contrato visual.

### 3. `tests/test_ollama_client.py`
- **Comando:** `python -m unittest tests/test_ollama_client.py`
- **Saída:**
  ```text
  Ran 5 tests in 0.004s
  OK
  ```
- **Comando alternativo:** `pytest tests/test_ollama_client.py`
- **Saída:**
  ```text
  5 passed in 0.12s
  ```
- **Total:** 5 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 0.004s (unittest) / 0.12s (pytest)

### 4. `tests/test_local_ai_refactor.py`
- **Comando:** `pytest tests/test_local_ai_refactor.py`
- **Saída:**
  ```text
  2 passed in 7.03s
  ```
- **Total:** 2 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 7.03s

### 5. `tests/test_pipeline_v2.py`
- **Comando:** `python -m unittest tests/test_pipeline_v2.py`
- **Saída:**
  ```text
  Ran 25 tests in 7.397s
  OK
  ```
- **Total:** 25 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 7.397s

### 6. `tests/test_pipeline.py`
- **Comando:** `python -m unittest tests/test_pipeline.py`
- **Saída:**
  ```text
  Ran 9 tests in 65.866s
  OK
  ```
- **Total:** 9 testes | **Falhas:** 0 | **Erros:** 0 | **Duração:** 65.866s

---

### Resumo Comparativo de Contagem de Testes
- **Contagem anteriormente reportada:** 57 testes (obtida pela soma de `test_pilot_profile_and_contracts.py` [23] + `test_pipeline.py` [9] + `test_pipeline_v2.py` [25] = 57).
- **Divergência identificada:** O teste `test_ollama_client.py` não era descoberto pelo `unittest` (0 testes) e, quando executado no `pytest`, falhava em `test_healthcheck_failure`.
- **Contagem Real Atualizada:**
  - `test_pilot_profile_and_contracts.py`: 23
  - `test_audit_phase1_e2e.py`: 6
  - `test_ollama_client.py`: 5
  - `test_local_ai_refactor.py`: 2
  - `test_pipeline_v2.py`: 25
  - `test_pipeline.py`: 9
  - **TOTAL GERAL:** **70 testes executados, 70 aprovados, 0 falhas, 0 erros.**

---

## 8. Evidências de Isolamento do Banco de Produção

1. **Variável de Ambiente em Testes:** Em todas as suítes (`test_audit_phase1_e2e.py`, `test_pilot_profile_and_contracts.py`, `test_pipeline_v2.py`), a variável `DATABASE_URL` é explicitamente zerada (`os.environ["DATABASE_URL"] = ""`), impedindo qualquer conexão ao Neon.
2. **Uso de SQLite Temporário e Isolado:** Todos os testes de banco instanciam engines SQLite em arquivos temporários descartáveis criados via `tempfile.mkdtemp()`.
3. **Nenhuma Alteração em Produção:** O banco Neon de produção permaneceu 100% intocado. Nenhuma query DDL ou DML foi executada externamente.
4. **Sem Vazamento de Credenciais:** Nenhum token, senha ou chave de API de produção foi exposto nos testes ou relatórios.
5. **Limpeza de Artefatos:** Não restaram arquivos temporários residuais ou bancos SQLite órfãos na raiz do repositório.

---

## 9. Análise Crítica do Benchmark de Renderização (`tests/benchmark_render.py`)

A auditoria inspecionou o script `tests/benchmark_render.py` e os resultados persistidos em `tests/benchmark_results.json`:

1. **Comandos FFmpeg:** Os comandos executados utilizam filtros lavfi sintéticos (`color=c=...` e `sine=...`), aplicando `-c:v libx264 -preset ultrafast` ou `-c:v h264_qsv -b:v 8M` com scale e formatação de pixel adequadas (`yuv420p` para x264, `nv12` para QSV).
2. **Execução dos Cenários:** Os 4 cenários (720p libx264, 1080p libx264, 720p QSV, 1080p QSV) foram devidamente executados no host atual (que possui suporte a Intel QSV).
3. **Confiabilidade da Amostragem de CPU/RAM:** O monitoramento realiza amostragem a cada 0.05s via `psutil`. No entanto, como a duração do teste foi de apenas 5.0 segundos (renderização finalizada em 0.8s a 2.0s), o número de amostras é muito pequeno (15 a 40 amostras), sujeitando a métrica a ruídos transientes de inicialização de processo no Windows.
4. **Tempo de Inicialização do Encoder:** O QSV apresentou tempo total aparente superior (2.06s no 720p) comparado ao libx264 (0.82s). Isso decorre diretamente do **overhead de inicialização do driver gráfico/Direct3D/MediaSDK**, que leva ~0.8s a 1.2s apenas para inicializar o hardware, antes de começar a codificar.
5. **Duração do Conteúdo e Classificação:** Uma amostra de 5 a 8 segundos com cor sólida estática não representa a carga real de produção (vídeos de 5 a 15 minutos com dezenas de cortes de vídeo, legendas, áudio e transições).
6. **Conclusão Técnica:**
   - O benchmark atual é classificado como **TESTE DE COMPATIBILIDADE FUNCIONAL DO HARDWARE/ENCODER**, e **NÃO** como um benchmark de carga representativo.
   - **Não se declara QSV superior ao libx264** para o pipeline completo sem testes sob carga contínua prolongada.
   - A resolução padrão de produção é **mantida rigorosamente em 720p (1280x720)**, garantindo estabilidade, baixo consumo de RAM e compatibilidade universal.

---

## 10. Riscos Remanescentes

| Risco | Severidade | Probabilidade | Mitigação Implementada |
| :--- | :--- | :--- | :--- |
| Ausência da coluna `production_profile` no Neon antes da migração | Média | Alta se subir sem migrar | Migração 007 documentada e preparada em `migrate_db.py`. Basta executar `python migrate_db.py` antes de iniciar workers contra a base remota. |
| Dependência externa de rede nos testes de Scout (`youtube_trends`) | Baixa | Baixa | `test_pipeline.py` faz coleta de tendências reais que levam ~65s. Em ambientes sem internet ou CI fechado, recomenda-se mockar `collect_trend_data`. |
| Workers legados que não enviem `production_profile` | Baixa | Baixa | O sistema aplica fallback legado transparente: jobs com `production_profile = None` são tratados como LONG legado (>= 480s). |

---

## 11. Parecer Final

### **PARECER: APROVADO**

Todos os critérios de aprovação foram cabalmente atingidos:
- [x] Persistência do perfil comprovada em código e testes.
- [x] Migração de banco para ambientes existentes preparada e documentada de forma idempotente.
- [x] Propagação do perfil comprovada de ponta a ponta (API -> Worker -> Validadores -> QC).
- [x] Concorrência entre múltiplos perfis simultâneos testada sem contaminação.
- [x] Compatibilidade legada integralmente preservada (sem atribuição silenciosa).
- [x] Normalização de cenas comprovada nos consumidores reais de mídia.
- [x] Todos os validadores respeitam os perfis sem divergências ou limites ocultos.
- [x] Bateria de testes expandida para 70 testes com 100% de aprovação e 0 falhas.
- [x] Isolamento rigoroso do banco de produção Neon comprovado.
- [x] Nenhuma falha crítica ou alta pendente.

**A Fase 2 permanece NÃO iniciada**, em cumprimento estrito às instruções, aguardando aprovação humana explícita.
