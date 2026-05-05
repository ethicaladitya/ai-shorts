# AI Shorts Product Guide

## What AI Shorts Does

AI Shorts is an internal workflow app for turning a topic, a WordPress article, or a short brief into a finished short-form video. The app combines hook generation, script writing, narration, subtitles, stock footage, rendering, and optional talking-head avatar generation behind one web UI.

## Main Outcomes

- Create a short video from a topic with minimal manual production work.
- Generate multiple opening hooks before committing to a script.
- Edit or regenerate parts of the script before rendering.
- Produce voice, subtitles, a final MP4, and a thumbnail.
- Optionally create an avatar-led version from an uploaded face image.
- Optionally notify n8n when a render finishes.

## Primary Workflows

### Standard short video

1. Sign in with Google. Access is restricted to the email configured in `ALLOWED_EMAIL`.
2. Open `/videos/create`.
3. Enter a topic and optionally add a WordPress URL or custom notes.
4. Generate hooks and choose the best opening.
5. Generate the script.
6. Edit the script, regenerate sections if needed, and format it for narration.
7. Start the pipeline.
8. Watch `/queue` for progress and logs.
9. Open the video detail page when the job completes.

### Avatar video

1. Open `/avatar`.
2. Upload a JPEG, PNG, or WebP image up to 10 MB.
3. Enter a topic and optional notes.
4. Let the background job generate the script, voice, animation, and captions.
5. Poll the avatar job page until the final output is ready.

## Key Screens

| Screen | Purpose |
| --- | --- |
| Dashboard | Recent videos, recent jobs, and high-level production counts |
| Create Video | Capture the topic, optional WordPress source, and custom notes |
| Hooks | Generate and choose an opening hook |
| Script Editor | Generate, edit, regenerate, and format the script |
| Queue | Watch render job progress and inspect logs |
| Video Detail | Review generated outputs and job status |
| Knowledge | Store prompt guidance such as examples, tone, CTA preferences, and audience notes |
| Settings | Switch providers, save credentials, and test AI connectivity |
| Avatar | Generate a talking-head video from a face image |

## What Gets Produced

- Script drafts and narration-ready script text
- Voice audio
- Subtitle files
- Final MP4 video output
- Thumbnail images
- Optional webhook payload to n8n after successful completion

## Status Model

### Standard video statuses

| Status | Meaning |
| --- | --- |
| `draft` | Video record exists but no AI work has started |
| `hooks_generated` | Hooks were generated and are ready for selection |
| `script_ready` | The script exists and can be reviewed or rendered |
| `voice_generating` | Narration is being generated |
| `voice_ready` | Audio generation completed |
| `subtitling` | Subtitle generation is in progress |
| `rendering` | Final video render is in progress |
| `complete` | Final output is available |
| `failed` | The job failed and should be inspected in the queue or logs |

### Avatar statuses

| Status | Meaning |
| --- | --- |
| `pending` | Job created but not yet processing |
| `generating_script` | Script generation in progress |
| `generating_voice` | Voice generation in progress |
| `animating` | D-ID animation is being generated |
| `adding_captions` | Captions are being added to the avatar output |
| `complete` | Avatar video is ready |
| `failed` | The avatar job failed |

## Operating Notes

- The Settings page can update provider values in the running app and stores a subset of settings in the database.
- Durable deployment configuration still lives in `.env`. Use `.env` plus restart or redeploy for any change that must survive rebuilds and restarts.
- A successful `/health` response means the app is up. It does not guarantee every external provider is reachable or has quota.
- Successful renders can post to `N8N_WEBHOOK_URL` when that integration is configured.

## Current Caveats

- Google login is effectively single-user today because access is checked against one configured email address.
- Knowledge entries are reseeded from `backend/seed_knowledge.py` on backend startup, so ad hoc edits in the Knowledge screen are not durable across restarts.
- Provider changes made in the Settings screen are useful for live testing, but `.env` remains the deployment source of truth.