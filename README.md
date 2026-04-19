# 🎬 Dark Channel Factory

> **YouTube Autonomous Video Pipeline** — Sistema de microsserviços event-driven para criação, produção e publicação autônoma de vídeos no YouTube no nicho **AI Tools & Business Automation** (CPM $25-40).

---

## 🏛️ Arquitetura

```
APScheduler (Ter/Qui/Sáb 14:00 UTC)
    │
    ▼
[Orchestrator] ──► Redis Queue
    │
    ├─► Scout & Miner Agent    → Minera nicho + trending (pytrends + YouTube API)
    │       │
    │       ▼
    ├─► Copywriter Agent       → Roteiro + SEO + Afiliados (Gemini 2.5 Flash)
    │       │
    │   ┌───┴───┐
    │   ▼       ▼
    ├─► Video Editor           ├─► Designer Agent
    │   (edge-tts + FFmpeg)    │   (DALL-E 3 Thumbnails)
    │       │                  │
    │       └──────┬───────────┘
    │              ▼
    └─► Manager & Analyst Agent
            │
            ├─► Upload YouTube API v3
            ├─► Comentário Fixado (Afiliados)
            ├─► Monitoramento Analytics (48h)
            │
            ├─► CTR < 4% → Trocar thumbnail (A→B→C)
            └─► Retenção > 45% → Disparar Deep Dive
```

---

## 🚀 Início Rápido

### 1. Pré-requisitos

```bash
# Python 3.11+
python --version

# FFmpeg (OBRIGATÓRIO para edição de vídeo)
# Windows: winget install FFmpeg
# ou baixe em: https://ffmpeg.org/download.html
ffmpeg -version

# Docker (Para Redis)
docker --version
```

### 2. Instalação

```bash
cd dark-channel-factory

# Instalar dependências
pip install -r requirements.txt

# Instalar browsers Playwright (para setup do canal)
playwright install chromium
```

### 3. Configuração

```bash
# Copiar template de variáveis
cp .env.example .env

# Editar .env com suas chaves:
notepad .env  # Windows
```

**Chaves necessárias no `.env`:**

| Chave | Obrigatória | Onde obter |
|-------|-------------|------------|
| `GEMINI_API_KEY` | ✅ Sim | [Google AI Studio](https://aistudio.google.com) |
| `YOUTUBE_CLIENT_SECRETS_FILE` | ✅ Sim | [Google Cloud Console](https://console.cloud.google.com) |
| `PEXELS_API_KEY` | ✅ Sim (vídeos) | [Pexels API](https://www.pexels.com/api/) |
| `OPENAI_API_KEY` | ⚡ Fallback LLM + Thumbnails | [OpenAI](https://platform.openai.com) |
| `YOUTUBE_API_KEY` | ℹ️ Opcional | [Google Cloud Console](https://console.cloud.google.com) |

### 4. Autenticação YouTube (UMA VEZ)

```bash
# 1. Configurar no Google Cloud Console:
#    - Criar projeto
#    - Ativar YouTube Data API v3
#    - Ativar YouTube Analytics API
#    - Criar OAuth 2.0 Client ID (Desktop app)
#    - Baixar JSON → renomear para: credentials/client_secrets.json

# 2. Executar setup (abrirá browser):
python setup_auth.py

# 3. Fazer login com derikmoretti@gmail.com e autorizar o app
# Token será salvo em: credentials/youtube_token.json
```

### 5. Configurar Canal (OPCIONAL)

```bash
# Configura nome, descrição, banner e logo via Playwright
# IMPORTANTE: Faça login manual quando o browser abrir
python -c "from channel_setup.channel_configurator import run_channel_setup; run_channel_setup()"
```

### 6. Iniciar Redis

```bash
# Via Docker (recomendado)
docker-compose up -d redis

# Verificar dashboard: http://localhost:9181
```

### 7. Iniciar Workers RQ

```bash
# Abrir terminais separados para cada worker:

# Worker 1: Scout
rq worker scout --url redis://localhost:6379/0

# Worker 2: Copywriter
rq worker copywriter --url redis://localhost:6379/0

# Worker 3: Vídeo (mais pesado)
rq worker video --url redis://localhost:6379/0

# Worker 4: Designer
rq worker designer --url redis://localhost:6379/0

# Worker 5: Upload + Analytics
rq worker upload analytics --url redis://localhost:6379/0
```

### 8. Testar Pipeline

```bash
# Dry-run (sem publicar)
python -m orchestrator.main --now --dry-run

# Teste com MOCK_LLM=true (sem chamar APIs pagas)
MOCK_LLM=true python -m orchestrator.main --now --dry-run

# Run testes unitários
python -m pytest tests/ -v
```

### 9. Ir ao Ar!

```bash
# Inicia orquestrador agendado (Ter/Qui/Sáb 14:00 UTC)
python -m orchestrator.main

# Dashboard de filas: http://localhost:9181
```

---

## 📁 Estrutura do Projeto

```
dark-channel-factory/
├── .env.example                # Template de configuração
├── docker-compose.yml          # Redis + Workers
├── requirements.txt            # Dependências Python
├── setup_auth.py               # Setup OAuth YouTube (rodar 1x)
│
├── orchestrator/
│   ├── main.py                 # Entry point + APScheduler
│   ├── config.py               # Configuração dos canais
│   └── queue_manager.py        # Abstração Redis RQ
│
├── agents/
│   ├── scout_miner/            # Agent 1: Pesquisa nichos
│   ├── copywriter/             # Agent 2: Roteiro + SEO + Afiliados
│   ├── video_editor/           # Agent 3: TTS + B-roll + FFmpeg
│   ├── designer/               # Agent 4: Thumbnails DALL-E 3
│   └── manager_analyst/        # Agent 5: Upload + Analytics + Feedback
│
├── shared/
│   ├── models.py               # Pydantic models
│   ├── llm_client.py           # Gemini → OpenAI cascade
│   ├── logger.py               # Rich logging
│   └── storage.py              # Gerenciamento de arquivos
│
├── channel_setup/              # Setup do canal via Playwright
├── output/                     # Vídeos, thumbnails, áudios gerados
│   ├── videos/
│   ├── thumbnails/
│   ├── audio/
│   └── logs/published_videos.json
│
└── tests/                      # Testes unitários
```

---

## 🤖 Agentes

### Agent 1: Scout & Miner
- **Input:** Configuração do canal
- **Processo:** pytrends + YouTube Data API → Gemini 2.5 Flash análise
- **Output:** `NicheData` com tópico, keywords, ângulo, score
- **Custo:** Gratuito (pytrends + 1 chamada LLM)

### Agent 2: Copywriter
- **Input:** `NicheData`
- **Processo:** Gemini gera roteiro completo (Hook→CTA), seleciona afiliados
- **Output:** `ScriptData` com 12 cenas, título, descrição, tags
- **Custo:** ~1-2 chamadas LLM (6000 tokens)

### Agent 3: Video Editor
- **Input:** `ScriptData`
- **Processo:** edge-tts (grátis) → Pexels B-roll → FFmpeg montagem
- **Output:** `.mp4` 1080p com legendas Hormozi
- **Custo:** Gratuito (edge-tts + Pexels free tier)

### Agent 4: Designer
- **Input:** `ScriptData` (thumbnail brief)
- **Processo:** Gemini gera prompts → DALL-E 3 gera imagens
- **Output:** 3x thumbnails `.png` (A/B/C)
- **Custo:** ~$0.12 (3x DALL-E 3 HD) — Fallback Pillow = grátis

### Agent 5: Manager & Analyst
- **Input:** `VideoData` + `ThumbnailData`
- **Processo:** Upload YouTube API, comentário fixado, agendamento, analytics
- **Output:** `PublishedVideo` com YouTube Video ID
- **Loop 48h:** CTR < 4% → troca thumbnail | Retenção > 45% → deep dive

---

## 💰 Nicho Escolhido: AI Tools & Business Automation

**Por quê este nicho para monetização rápida:**

| Métrica | Valor |
|---------|-------|
| CPM médio (EUA) | $25-40 |
| Categoria YouTube | Science & Technology (28) |
| Afiliados mais lucrativos | SaaS 30-40% recorrente |
| Demanda em 2025/2026 | 📈 Alta crescente |
| Facilidade de produção | ⭐⭐⭐⭐⭐ (AI gera SI próprio) |

**Estratégia de Monetização:**
1. **YouTube Ads** (ativar após 1000 subs + 4000h watch) — CPM alto
2. **SaaS Affiliates** (Jasper, Notion, Make.com) — 20-40% recorrente
3. **ClickBank Courses** — 50% comissão única
4. **Amazon Associates** — 4-8% em livros tech

---

## ⚠️ Compliance

- **AI Content Disclosure:** Ativado automaticamente (`containsSyntheticMedia: true`)
- **Conta YouTube:** Criada e gerenciada manualmente via `derikmoretti@gmail.com`
- **Links de Afiliado:** Disclosure incluído automaticamente no comentário fixado
- **Direitos autorais:** Todo B-roll de fontes royalty-free (Pexels/Pixabay)
- **Música:** Não incluída por padrão (use YouTube Audio Library)

---

## 🔧 Comandos Úteis

```bash
# Testar um agente individualmente
python -c "
from agents.scout_miner.agent import run_scout_agent
from uuid import uuid4
result = run_scout_agent(str(uuid4()), 'ai_tools_us')
print(result.topic)
"

# Ver fila de jobs
rq info --url redis://localhost:6379/0

# Reprocessar jobs com erro
rq requeue --all --url redis://localhost:6379/0

# Ver logs
tail -f output/logs/pipeline.log

# Status dos vídeos publicados
python -c "
from agents.manager_analyst.analytics_monitor import load_published_videos
import json
videos = load_published_videos()
for v in videos:
    print(f'{v[\"title\"][:40]} | ID: {v[\"youtube_video_id\"]} | CTR: {v.get(\"analytics\", {}).get(\"ctr_percent\", 0):.1f}%')
"

# Criar canal em novo idioma (Ex: PT-BR)
# Edite orchestrator/config.py e adicione novo ChannelConfig
# com language='pt-BR' e voice='pt-BR-FranciscaNeural'
```

---

## 📊 Escalabilidade

Para adicionar um canal em novo idioma:
1. Abra `orchestrator/config.py`
2. Descomente o template `ai_tools_br` e configure
3. Crie novo `credentials/youtube_token_NOME.json` via `setup_auth.py`
4. Reinicie o orquestrador

O sistema suporta **N canais simultâneos** limitado apenas pela quota da YouTube API.

---

*Built with ❤️ — Dark Channel Factory v1.0*
