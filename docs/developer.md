# AI Shorts Developer Guide

## System Overview

AI Shorts is a FastAPI application with server-rendered HTMX pages, a SQLite database, background jobs for video and avatar generation, and a Docker Compose deployment wrapper. The backend owns the product UI, API routes, prompt orchestration, AI provider calls, media generation, and render queue state.

## Repo Map

| Path | Responsibility |
| --- | --- |
| `backend/app/main.py` | FastAPI app setup, middleware, router registration, startup hooks |
| `backend/app/config.py` | Typed settings loaded from environment variables and `.env` |
| `backend/app/routers/` | Product routes for dashboard, videos, knowledge, queue, settings, auth, and avatar jobs |
| `backend/app/services/` | AI providers, content fetching, pipeline orchestration, subtitles, and rendering |
| `backend/app/templates/` | HTMX and Jinja templates |
| `backend/seed_knowledge.py` | Startup seed data for the knowledge base |
| `backend/start.sh` | Local backend launcher with local path overrides |
| `docker-compose.yml` | Optional local multi-service orchestration (n8n, Voicebox) |

## Local Development

### Requirements

- Python 3.12
- FFmpeg on your `PATH`
- A populated root `.env`

### Quick start

```bash
cp .env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt
cd backend
./start.sh
```

### Local endpoints

- App: `http://127.0.0.1:8787`
- API docs: `http://127.0.0.1:8787/api/docs`
- Health: `http://127.0.0.1:8787/health`

### Local runtime behavior

- `backend/start.sh` loads the root `.env` file from the repo root.
- `backend/start.sh` also sets local `DATABASE_URL`, `DATA_DIR`, `OUTPUT_DIR`, `TEMP_DIR`, and `ASSETS_DIR` values before starting Uvicorn.
- Local runs always bind to `127.0.0.1:8787`; `UI_PORT` only affects Docker Compose deployments.

## Running Locally

This app runs entirely on your local machine. There is no remote server deployment.

Start the backend:

```bash
cd backend && ./start.sh
```

To run the optional n8n or Voicebox containers alongside the backend:

```bash
# Standard (backend + n8n only)
docker compose up -d n8n

# With local Voicebox TTS
docker compose --profile voicebox up -d voicebox
```

The backend itself is always run with `start.sh`, not inside Docker.

## Configuration Model

The app has multiple configuration layers. Treat them differently.

| Source | Used by | How to think about it |
| --- | --- | --- |
| Root `.env` | Local runs and FastAPI startup | Source of truth for all configuration |
| `backend/app/config.py` | Pydantic settings model | Canonical list of supported env vars and defaults |
| `backend/start.sh` | Backend launcher | Injects local file paths and launches Uvicorn |
| `backend/app/routers/settings.py` | Running web process | Saves a subset of provider settings to the DB and patches the current process |
| `docker-compose.yml` | Optional containers (n8n, Voicebox) | Handles optional service orchestration alongside the backend |

### Important current behavior

- The Settings UI is useful for live testing, but it is not the durable startup source of truth.
- DB-backed settings are only applied when the settings route loads or saves them; they are not globally loaded during app startup.
- For changes that must survive rebuilds and restarts, update `.env` and restart or redeploy.

## Changing Environment Variables

### Change an existing value for local development

1. Edit the root `.env` file.
2. Stop the running local backend if it is already up.
3. Start it again with `cd backend && ./start.sh`.
4. Verify the change through `/health`, the affected UI flow, or a provider connection test.

### Add a brand-new env var

When you introduce a new configuration variable, update the contract and the startup path together.

1. Add the typed setting and default to `backend/app/config.py`.
2. Add the variable to `.env.example`.
3. If it affects optional services or container wiring, update `docker-compose.yml`.
4. Update this guide if the variable changes system behavior.
5. Restart the backend.

### Example: updating a provider API key

1. Replace the value in the root `.env`.
2. Restart the backend with `cd backend && ./start.sh`.
3. Open `/settings` and run the connection test if the provider supports it.
4. Confirm the next render or avatar job succeeds.

### Example: switching to Voicebox

1. Set `VOICE_PROVIDER=voicebox`.
2. For a local desktop Voicebox instance, use `VOICEBOX_URL=http://localhost:17493`.
3. For Docker Compose deployments, the backend container must use `VOICEBOX_URL=http://voicebox:17493`.
4. Redeploy so Compose can start the `voicebox` profile.

Voicebox runs locally alongside the backend. Always use `VOICEBOX_URL=http://localhost:17493` in `.env` — the Docker-internal URL (`http://voicebox:17493`) is only needed if the backend itself runs inside Compose, which it does not in the current local-only setup.

## Persistence and Data

- SQLite database: `backend/data/ai_shorts.db`
- Video outputs: `backend/data/output/`
- Temporary files: `backend/data/temp/`
- Face image uploads: `backend/data/faces/`
- App logs: `backend/data/app.log`

## Non-Obvious Product Behaviors

- The startup hook reseeds the knowledge table from `backend/seed_knowledge.py` and deletes existing knowledge rows first.
- The Settings UI persists a subset of values in `app_settings`, but the running process only picks them up when the settings route is visited or saved.
- `UI_PORT`, `API_PORT`, and `N8N_PORT` matter for Docker Compose deployments, not for `backend/start.sh`.

## Logs

```bash
tail -f backend/data/app.log
```

## Troubleshooting

### My env change did not apply

- Check whether you restarted the local backend or redeployed the server.
- Check whether the variable is defined in `backend/app/config.py`.
- If the value was changed only in the Settings UI, remember that a restart falls back to `.env`.

### Voicebox is unreachable

- Check `VOICE_PROVIDER` and `VOICEBOX_URL`.
- Use `http://localhost:17493` for local desktop runs.
- Use `http://voicebox:17493` inside Docker Compose.
- Confirm the `voicebox` profile was started during deployment.

### Google login is failing

- Check `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAIL`, and `BASE_URL` in `.env`.
- Confirm the Google OAuth redirect URI in the Google Cloud Console matches `<BASE_URL>/auth/callback`.

### Renders complete but n8n automation does not fire

- Check `N8N_WEBHOOK_URL` in `.env`.
- Check `tail -f backend/data/app.log` for webhook POST errors.