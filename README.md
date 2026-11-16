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
18:     ├─► Copywriter Agent       → Roteiro + SEO + Afiliados (Ollama Local)
19:     │       │
20:     │   ┌───┴───┐
21:     │   ▼       ▼
22:     ├─► Video Editor           ├─► Designer Agent
23:     │   (edge-tts + FFmpeg)    │   (Hugging Face / Pillow)
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
| `HF_TOKEN` | ⚡ Recomendado (Thumbnails) | [Hugging Face](https://huggingface.co/settings/tokens) |
| `YOUTUBE_CLIENT_SECRETS_FILE` | ✅ Sim | [Google Cloud Console](https://console.cloud.google.com) |
| `PEXELS_API_KEY` | ✅ Sim (vídeos) | [Pexels API](https://www.pexels.com/api/) |
| `YOUTUBE_API_KEY` | ℹ️ Opcional | [Google Cloud Console](https://console.cloud.google.com) |

### IA local com Ollama (Windows, CPU-only)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_ollama.ps1
ollama list
curl.exe http://localhost:11434/api/tags
```

O Copywriter cria um roteiro matriz e depois localiza o conteúdo para `en-US`,
`de-DE`, `es-ES` e `fr-FR`. Os quatro jobs conservam IDs, ordem e buscas visuais das cenas;
o Video Editor baixa um único conjunto de B-rolls e gera quatro áudios, legendas
e MP4s independentes.

Para thumbnails, crie um token com permissão **Inference Providers** na Hugging
Face e preencha `HF_TOKEN` no `.env`. Sem token ou cota, o fallback Pillow ainda
gera as três variantes localmente.

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

## 📁 Estrutura do Projeto (Produção)

```
dark-channel-factory/
├── 🚀 factory_service.py        # Serviço Central 24/7 (Agendamento, IA e Analytics)
├── 🛠️ desktop_node.py          # Worker de Produção (Renderização e Upload Playwright)
├── 🌐 cloud_hub/                # Dashboard SaaS e Gestão Multi-Canal
│   └── main.py                  # Entry point da interface web e API
├── 🤖 agents/                   # Inteligência dos Agentes
│   ├── designer/                # Geração de Thumbnails (Hugging Face / Pillow)
│   ├── video_editor/            # Motor de edição (FFmpeg + edge-tts)
│   ├── copywriter/              # Roteirização e SEO (Ollama Local)
│   └── scout_miner/             # Mineração de tendências e nichos
├── 📋 orchestrator/             # Lógica de Orquestração e Filas
├── 🗄️ database/                 # Camada de Dados (SQLModel / Postgres)
├── 📂 shared/                   # Código compartilhado e Utilities
├── 📂 output/                   # Assets gerados (Vídeos, Thumbs, Logs)
│
├── ⚡ Executáveis de Manutenção:
│   ├── fix_drafts_and_publish.py # Recupera e publica rascunhos pendentes
│   ├── regenerar_thumbs.py       # Refaz thumbnails de vídeos existentes
│   ├── health_check.py           # Diagnóstico completo do sistema
│   ├── migrate_db.py             # Migrações de banco de dados
│   └── force_videos.py           # Força disparo imediato de produção
│
└── ⌨️ Launchers (Windows):
    ├── RUN_FACTORY.bat          # Painel de Controle Principal
    ├── SETUP.bat                # Instalador e configurador inicial
    └── FIX_DRAFTS_AND_PUBLISH.bat
```

---

## 🛠️ Arquivos Executados em Produção

| Arquivo | Papel no Ecossistema | Frequência |
|:--- |:--- |:--- |
| `factory_service.py` | **Cérebro**. Gerencia o calendário, chama as LLMs, decide o que será viral. | 24/7 (Loop infinito) |
| `desktop_node.py` | **Músculo**. Renderiza o vídeo final e faz o upload via Playwright no browser. | 24/7 (Aguardando Jobs) |
| `cloud_hub/main.py` | **Interface**. Painel administrativo para monitorar jobs e configurar canais. | 24/7 |
| `viral_adaptive_agent.py` | **Otimizador**. Analisa performance e ajusta os próximos vídeos. | Por Job |
| `auto_healer_bot.py` | **Auto-Correção**. Tenta resolver erros comuns automaticamente. | Sob demanda |


---

## 🤖 Agentes

### Agent 1: Scout & Miner
- **Input:** Configuração do canal
- **Processo:** pytrends + YouTube Data API → Ollama análise local
- **Output:** `NicheData` com tópico, keywords, ângulo, score
- **Custo:** Gratuito (pytrends + Ollama local)

### Agent 2: Copywriter
- **Input:** `NicheData`
- **Processo:** Ollama local gera roteiro completo (Hook→CTA), seleciona afiliados
- **Output:** `ScriptData` com 12 cenas, título, descrição, tags
- **Custo:** Gratuito (Local LLM)

### Agent 3: Video Editor
- **Input:** `ScriptData`
- **Processo:** edge-tts (grátis) → Pexels B-roll → FFmpeg montagem
- **Output:** `.mp4` 1080p com legendas Hormozi
- **Custo:** Gratuito (edge-tts + Pexels free tier)

### Agent 4: Designer
- **Input:** `ScriptData` (thumbnail brief)
- **Processo:** Hugging Face Inference API / Pillow local gera imagens
- **Output:** 3x thumbnails `.png` (A/B/C)
- **Custo:** Gratuito

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
