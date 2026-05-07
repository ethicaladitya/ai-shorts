# Persona System

Virtual creator automation pipeline — runs on Mac Mini M1/M2.
Integrates with the existing `ai-shorts` backend as a modular adapter.

## Quick Start

```bash
# From project root (ai-shorts/)
bash persona_system/scripts/setup.sh
source persona_system/.venv/bin/activate

# Start dashboard (http://localhost:8100)
python -m persona_system.dashboard.api

# Run content pipeline (one-shot)
python -m persona_system.scripts.run_pipeline --persona default --pillar "late night energy"

# Publish due posts
python -m persona_system.automation.publisher

# Install cron jobs
bash persona_system/scripts/install_cron.sh
```

## Modules

| Module | Purpose |
|---|---|
| `persona_engine/` | Load persona YAML, apply tone/style |
| `content_engine/` | Batch caption + script generation, scoring |
| `image_engine/` | SD image gen (A1111), face preprocessing, LoRA |
| `video_engine_adapter/` | Wraps existing ai-shorts pipeline |
| `voice_engine/` | Voicebox local TTS, emotion presets |
| `scheduler/` | Calendar, randomised slots, queue |
| `dm_engine/` | Classify DMs, 3-variant replies, human approval |
| `analytics_engine/` | Track metrics, score posts, AI feedback loop |
| `automation/` | Instagram Graph API + OnlyFans Playwright |
| `dashboard/` | FastAPI + HTML UI |

## Pipeline Flow

```
Content Engine → Score → Image Engine → Voice Engine → Video Adapter → DB → Scheduler → Publisher
                                                                                  ↑
                                                             Analytics feedback loop
```

## Environment Variables

Add these to your root `.env` (alongside existing ai-shorts vars):

```env
# Persona
ACTIVE_PERSONA=default

# Local LLM (Ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# Stable Diffusion (A1111 local)
SD_BASE_URL=http://localhost:7860
SD_LORA_TRIGGER=nova_v1

# Voicebox (reuses existing setting)
VOICEBOX_URL=http://localhost:17493

# Instagram Graph API
INSTAGRAM_ACCESS_TOKEN=your_token
INSTAGRAM_USER_ID=your_user_id

# OnlyFans (browser automation)
ONLYFANS_EMAIL=your@email.com
ONLYFANS_PASSWORD=yourpassword

# Optional: OpenAI fallback (when Ollama is down)
OPENAI_API_KEY=sk-...

# Scoring thresholds (0.0-1.0)
CONTENT_SCORE_MIN=0.65
```

## LoRA Dataset

```bash
# Place 30-100 curated face images in:
persona_system/lora_dataset/images/

# Run auto-captioner + generate kohya configs:
python persona_system/lora_dataset/builder.py ./persona_system/lora_dataset/images nova_v1

# Then train with kohya:
accelerate launch train_network.py --config_file ./persona_system/lora_dataset/config/train.toml
```

## Cron Jobs

| Schedule | Job |
|---|---|
| Every 5 min | Publish due posts |
| Daily 3am | Full content generation pipeline |
| Every 6 hrs | Fetch analytics for published posts |

## Cost Strategy

- **Captions/scripts** → local Qwen via Ollama (free)
- **Refinement** → OpenAI gpt-4o-mini only when needed
- **Images** → local Stable Diffusion A1111 (free)
- **Voice** → local Voicebox/Kokoro (free)
- **Videos** → loop videos local (free); talking videos via existing ai-shorts (D-ID/Replicate only when scored high enough)
- **Publishing** → Instagram Graph API (free); OnlyFans Playwright (free)

## Adding a New Persona

```bash
cp persona_system/config/personas/default.yaml persona_system/config/personas/luna.yaml
# Edit luna.yaml with new character details
python -m persona_system.scripts.run_pipeline --persona luna
```
