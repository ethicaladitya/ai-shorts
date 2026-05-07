# AI Shorts System

AI-powered short-form video automation. Generates scripts, voice, subtitles, and renders videos — all from a single topic.

**Live:** https://video.adityashah.blog

---
## Documentation

- [Product guide](docs/product.md) - user workflows, screens, statuses, and operator caveats
- [Developer guide](docs/developer.md) - local setup, architecture, deployment, and env var runbooks

If you are changing configuration, especially provider keys or endpoints, start with the [developer guide](docs/developer.md#changing-environment-variables).

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   HTMX UI   │────▶│  FastAPI API  │────▶│  Azure OpenAI │
│  (Tailwind)  │◀────│  (Python)    │◀────│  (GPT-4o)    │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
              ┌─────▼─────┐ ┌─────▼─────┐
              │  FFmpeg    │ │  Whisper   │
              │  (Render)  │ │  (Subs)   │
              └───────────┘ └───────────┘
```

## Quick Start

### Deploy (One Command)

```bash
./deploy.sh nodejs          # SSH alias
./deploy.sh aditya@1.2.3.4  # Direct SSH
```

### Uninstall

```bash
./uninstall.sh nodejs
```

## Features

- **Dashboard** — Overview of all videos, statuses, and stats
- **Create Video** — Topic input, WordPress URL, or manual content
- **Hook Generator** — 5 AI-generated hooks per video (question, stat, contrarian, story, direct)
- **Script Editor** — Edit, regenerate sections, format for voice
- **Knowledge Panel** — Persistent tone, examples, audience, CTA preferences
- **Render Queue** — Real-time job monitoring with logs
- **AI Settings** — Configure Azure OpenAI, test connections
- **Full Pipeline** — Content → Hooks → Script → Voice → Subtitles → Video

## Pipeline

```
1. Fetch content (WordPress API or manual input)
2. Generate 5 hooks (AI)
3. Select best hook
4. Generate full script (HOOK → PATTERN BREAK → VALUE → CTA)
5. Format for voice delivery
6. Generate voice (Azure OpenAI TTS / ElevenLabs)
7. Generate subtitles (Whisper)
8. Render video (FFmpeg)
9. Store output
10. Trigger n8n webhook (optional)
```

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI (Python 3.12) |
| Frontend | HTMX + Tailwind CSS |
| AI | Azure OpenAI (GPT-4o) |
| Voice | Azure OpenAI TTS / ElevenLabs |
| Video | FFmpeg |
| Subtitles | Whisper |
| Automation | n8n (Docker) |
| Database | SQLite |
| Infra | Docker Compose |

## Environment Variables

Copy `.env.example` to `.env` and fill in the providers you plan to use.

Core Azure setup looks like this:

```
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_API_VERSION=2024-08-01-preview
AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

The full configuration model, provider-specific variables, and the runbook for updating env vars live in [docs/developer.md](docs/developer.md).

## Ports

| Service | Default Port |
|---------|-------------|
| UI | 8787 |
| API | 8788 |
| n8n | 8789 |

Ports auto-increment if occupied.

## Project Structure

```
ai-shorts/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app
│   │   ├── config.py            # Settings
│   │   ├── database.py          # SQLAlchemy
│   │   ├── models.py            # Database models
│   │   ├── routers/             # API routes
│   │   │   ├── dashboard.py
│   │   │   ├── videos.py
│   │   │   ├── scripts.py
│   │   │   ├── knowledge.py
│   │   │   ├── render.py
│   │   │   └── settings.py
│   │   ├── services/            # Business logic
│   │   │   ├── ai_provider.py   # AI abstraction
│   │   │   ├── voice_provider.py
│   │   │   ├── content_fetcher.py
│   │   │   ├── script_generator.py
│   │   │   ├── video_renderer.py
│   │   │   ├── subtitle_generator.py
│   │   │   └── pipeline.py      # Full orchestrator
│   │   ├── prompts/             # AI prompt templates
│   │   │   ├── hooks.py
│   │   │   ├── scripts.py
│   │   │   └── formatter.py
│   │   └── templates/           # HTMX/Jinja2 templates
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
├── deploy.sh                    # One-command deploy
├── uninstall.sh
├── nginx/                       # Reference nginx config
├── n8n/workflows/               # Sample n8n workflows
├── .env.example
└── README.md
```

## Safety

- Isolated in `/opt/ai-shorts-system`
- Docker project: `ai_shorts_system`
- Docker network: `ai_shorts_network`
- Does NOT touch existing containers
- Does NOT overwrite nginx configs of other sites
- Does NOT bind to ports 80/443 directly
- Idempotent: safe to re-run
- CPU + memory limits on all containers

## Logs

```bash
# All containers (if running in Docker)
ssh nodejs 'cd /opt/ai-shorts-system && docker compose -p ai_shorts_system logs -f'

# Backend only (local)
tail -f backend/data/app.log

# View errors only
grep "ERROR" backend/data/app.log
```

## Troubleshooting & Debugging

If the pipeline stalls or the dashboard shows "Timeout waiting for engine to start", follow these steps:

### 1. Check for DNS / Connection Errors
The engine might be failing to reach Azure. You can verify this by checking for `nodename nor servname provided` in the logs.
If you see this, check your `.env` endpoints.

**Common Fix**: If your `AZURE_OPENAI_TTS_ENDPOINT` is failing, comment it out. The system will automatically fall back to using your primary `AZURE_OPENAI_ENDPOINT` for voice generation.

### 2. Verify API Keys
Ensure your `AZURE_OPENAI_API_KEY` is active and has permissions for both chat (GPT-4o) and TTS deployments.

### 3. Clear Stale Tasks
If the system hangs, restart the backend to clear memory and hung processes:
```bash
./restart.sh
```

### 4. JavaScript Errors
If buttons aren't clicking, refresh the page with **Cmd + R**. I have added safety checks to prevent crashes when clicking empty tabs.
