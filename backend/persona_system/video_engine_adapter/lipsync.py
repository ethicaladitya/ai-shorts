"""
persona_system/video_engine_adapter/lipsync.py

Applies lipsync to a video clip given an audio track.
Supports three providers, selectable via settings:
  - "latentsync" → ByteDance LatentSync (local, free, best quality)
  - "hedra"      → Hedra API (~$0.05/video, fast, high quality)
  - "sadtalker"  → SadTalker via Replicate (legacy fallback)

Usage:
    from persona_system.video_engine_adapter.lipsync import apply_lipsync
    result = await apply_lipsync(video_path, audio_path, output_path)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
from pathlib import Path

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

FFMPEG = "ffmpeg"


# ─────────────────────────────────────────────────────────────────────────────
# LatentSync (local) provider
# ─────────────────────────────────────────────────────────────────────────────
async def _latentsync_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Run LatentSync locally via its inference script.
    Requires LatentSync repo cloned and checkpoint downloaded.
    Works on Apple Silicon (MPS) — expect ~5-10 min per 30s video.
    """
    ls_dir = Path(settings.latentsync_dir) if settings.latentsync_dir else None

    if not ls_dir or not ls_dir.exists():
        raise RuntimeError(
            "LatentSync directory not configured. Set LATENTSYNC_DIR=/path/to/LatentSync in .env"
        )

    checkpoint = ls_dir / "checkpoints" / settings.latentsync_checkpoint
    whisper_ckpt = ls_dir / "checkpoints" / settings.latentsync_whisper_ckpt

    if not checkpoint.exists():
        raise RuntimeError(
            f"LatentSync checkpoint not found: {checkpoint}. "
            "Download from https://huggingface.co/ByteDance/LatentSync"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-m", "scripts.inference",
        "--unet_config_path", "configs/unet/second_stage.yaml",
        "--inference_ckpt_path", str(checkpoint),
        "--whisper_ckpt_path", str(whisper_ckpt),
        "--video_path", str(video_path.resolve()),
        "--audio_path", str(audio_path.resolve()),
        "--video_out_path", str(output_path.resolve()),
        "--guidance_scale", "2.5",
        "--video_fps", "25",
        "--seed", "42",
    ]

    logger.info("Running LatentSync: %s", " ".join(cmd[:5]) + " ...")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ls_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_snippet = stderr.decode()[-2000:]
        raise RuntimeError(f"LatentSync failed (rc={proc.returncode}):\n{err_snippet}")

    if not output_path.exists():
        raise RuntimeError(f"LatentSync ran but output not found: {output_path}")

    logger.info("LatentSync complete → %s", output_path.name)
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Hedra API provider
# ─────────────────────────────────────────────────────────────────────────────
async def hedra_from_portrait(
    portrait_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Generate a talking head video from a still portrait image + audio via Hedra.
    No source video required — this is the primary lipsync path.
    """
    return await _hedra_apply_portrait(portrait_path, audio_path, output_path)


async def _hedra_apply_portrait(
    portrait_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """Core Hedra implementation that accepts a portrait image directly."""
    if not settings.hedra_api_key:
        raise RuntimeError(
            "HEDRA_API_KEY not set in .env — sign up at hedra.com (free tier available)"
        )

    headers = {"X-API-KEY": settings.hedra_api_key}
    base = "https://mercury.dev.dream-ai.com/api"

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Upload audio
        with audio_path.open("rb") as f:
            audio_resp = await client.post(
                f"{base}/v1/audio",
                files={"file": (audio_path.name, f, "audio/mpeg")},
                headers=headers,
            )
        audio_resp.raise_for_status()
        audio_url = audio_resp.json().get("url", "")

        # 2. Upload portrait image
        with portrait_path.open("rb") as f:
            img_resp = await client.post(
                f"{base}/v1/portrait",
                files={"file": (portrait_path.name, f, "image/jpeg")},
                headers=headers,
            )
        img_resp.raise_for_status()
        portrait_url = img_resp.json().get("url", "")

        # 3. Create character video job
        job_resp = await client.post(
            f"{base}/v1/characters",
            json={
                "text": "",
                "voice_url": audio_url,
                "avatar_image": portrait_url,
                "aspect_ratio": settings.hedra_aspect_ratio,
            },
            headers={**headers, "Content-Type": "application/json"},
        )
        job_resp.raise_for_status()
        job = job_resp.json()
        job_id = job.get("jobId") or job.get("id")

    if not job_id:
        raise RuntimeError(f"Hedra: no job ID in response: {job}")

    logger.info("Hedra job submitted: %s", job_id)

    for _ in range(240):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            st = await client.get(f"{base}/v1/projects/{job_id}", headers=headers)
            st.raise_for_status()
            status_data = st.json()

        status = status_data.get("status", "")
        if status == "Completed":
            video_url = status_data.get("videoUrl", "")
            if not video_url:
                raise RuntimeError("Hedra completed but no videoUrl")
            async with httpx.AsyncClient(timeout=300.0) as client:
                dl = await client.get(video_url)
                dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("Hedra clip downloaded → %s", output_path.name)
            return output_path
        elif status in ("Failed", "Error"):
            raise RuntimeError(f"Hedra job failed: {status_data.get('message', 'unknown')}")

        logger.debug("Hedra job %s: %s", job_id, status)

    raise TimeoutError(f"Hedra job {job_id} did not complete in 20 minutes")


async def _hedra_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Apply lipsync via Hedra Character-2 API.
    Uses the first frame of the video as the portrait image.
    """
    if not settings.hedra_api_key:
        raise RuntimeError("HEDRA_API_KEY not set in .env")

    # Extract first frame as portrait image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        portrait_path = Path(tmp.name)

    extract_cmd = [
        FFMPEG, "-y", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(portrait_path)
    ]
    subprocess.run(extract_cmd, check=True, capture_output=True)
    result = await _hedra_apply_portrait(portrait_path, audio_path, output_path)
    portrait_path.unlink(missing_ok=True)
    return result


async def _hedra_apply_legacy(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """Legacy Hedra implementation — kept for reference. Use _hedra_apply instead."""
    headers = {"X-API-KEY": settings.hedra_api_key}
    base = "https://mercury.dev.dream-ai.com/api"

    # Extract first frame as portrait image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        portrait_path = Path(tmp.name)

    extract_cmd = [
        FFMPEG, "-y", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(portrait_path)
    ]
    subprocess.run(extract_cmd, check=True, capture_output=True)

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Upload audio
        with audio_path.open("rb") as f:
            audio_resp = await client.post(
                f"{base}/v1/audio",
                files={"file": (audio_path.name, f, "audio/mpeg")},
                headers=headers,
            )
        audio_resp.raise_for_status()
        audio_url = audio_resp.json().get("url", "")

        # 2. Upload portrait image
        with portrait_path.open("rb") as f:
            img_resp = await client.post(
                f"{base}/v1/portrait",
                files={"file": (portrait_path.name, f, "image/jpeg")},
                headers=headers,
            )
        img_resp.raise_for_status()
        portrait_url = img_resp.json().get("url", "")

        # 3. Create character video job
        job_resp = await client.post(
            f"{base}/v1/characters",
            json={
                "text": "",           # empty = audio-driven only
                "voice_url": audio_url,
                "avatar_image": portrait_url,
                "aspect_ratio": settings.hedra_aspect_ratio,
            },
            headers={**headers, "Content-Type": "application/json"},
        )
        job_resp.raise_for_status()
        job = job_resp.json()
        job_id = job.get("jobId") or job.get("id")

    portrait_path.unlink(missing_ok=True)

    if not job_id:
        raise RuntimeError(f"Hedra: no job ID in response: {job}")

    logger.info("Hedra job submitted: %s", job_id)

    # 4. Poll
    for _ in range(240):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            st = await client.get(f"{base}/v1/projects/{job_id}", headers=headers)
            st.raise_for_status()
            status_data = st.json()

        status = status_data.get("status", "")
        if status == "Completed":
            video_url = status_data.get("videoUrl", "")
            if not video_url:
                raise RuntimeError("Hedra completed but no videoUrl")
            async with httpx.AsyncClient(timeout=300.0) as client:
                dl = await client.get(video_url)
                dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("Hedra clip downloaded → %s", output_path.name)
            return output_path
        elif status in ("Failed", "Error"):
            raise RuntimeError(f"Hedra job failed: {status_data.get('message', 'unknown')}")

        logger.debug("Hedra job %s: %s", job_id, status)

    raise TimeoutError(f"Hedra job {job_id} did not complete in 20 minutes")


# ─────────────────────────────────────────────────────────────────────────────
# Replicate LatentSync — cheap (~$0.01/video), high quality
# ─────────────────────────────────────────────────────────────────────────────
async def _replicate_latentsync_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    LatentSync via Replicate API. ~$0.01 per 30s video.
    Model: zsxkib/latentsync (ByteDance LatentSync, best free-tier lipsync).
    Sign up: replicate.com → get API key → set REPLICATE_API_KEY in .env.
    """
    if not settings.replicate_api_key:
        raise RuntimeError(
            "REPLICATE_API_KEY not set. Sign up at replicate.com (pay-as-you-go, ~$0.01/video)"
        )

    auth_headers = {
        "Authorization": f"Bearer {settings.replicate_api_key}",
        "Content-Type": "application/json",
    }

    with video_path.open("rb") as f:
        video_b64 = f"data:video/mp4;base64,{base64.b64encode(f.read()).decode()}"
    with audio_path.open("rb") as f:
        audio_b64 = f"data:audio/mpeg;base64,{base64.b64encode(f.read()).decode()}"

    # Use the model predictions endpoint (no pinned version — always uses latest)
    async with httpx.AsyncClient(timeout=120.0) as client:
        pred = await client.post(
            "https://api.replicate.com/v1/models/zsxkib/latentsync/predictions",
            json={
                "input": {
                    "video": video_b64,
                    "audio": audio_b64,
                    "guidance_scale": 1.5,
                    "video_fps": 25,
                    "inference_steps": 20,
                    "seed": 42,
                },
            },
            headers=auth_headers,
        )
        pred.raise_for_status()
        pred_data = pred.json()
        pred_id = pred_data.get("id")

    if not pred_id:
        raise RuntimeError(f"Replicate LatentSync: no prediction ID in response: {pred_data}")

    logger.info("Replicate LatentSync submitted: %s", pred_id)

    for _ in range(180):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            status_resp = await client.get(
                f"https://api.replicate.com/v1/predictions/{pred_id}",
                headers={"Authorization": f"Bearer {settings.replicate_api_key}"},
            )
            status_resp.raise_for_status()
            data = status_resp.json()

        status = data.get("status", "")
        if status == "succeeded":
            out = data.get("output")
            video_url = out if isinstance(out, str) else (out[0] if out else None)
            if not video_url:
                raise RuntimeError("Replicate LatentSync: no output URL")
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                dl = await client.get(video_url)
                dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("Replicate LatentSync complete → %s", output_path.name)
            return output_path
        elif status == "failed":
            raise RuntimeError(f"Replicate LatentSync failed: {data.get('error', 'unknown')}")
        logger.debug("Replicate LatentSync %s: %s", pred_id, status)

    raise TimeoutError(f"Replicate LatentSync {pred_id} timed out after 15 minutes")


# ─────────────────────────────────────────────────────────────────────────────
# sync.so — free tier available, simple REST API
# ─────────────────────────────────────────────────────────────────────────────
async def _syncso_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Lipsync via sync.so API. Free tier available; pay-as-you-go after that (~$0.01/video).
    Set SYNCSO_API_KEY in .env after signing up at sync.so.

    sync.so v2 API accepts a JSON body with base64 video+audio data URIs.
    """
    import os
    # Try settings first, then env var directly
    try:
        from app.config import settings as app_settings
        api_key = getattr(app_settings, "syncso_api_key", "") or os.environ.get("SYNCSO_API_KEY", "")
    except Exception:
        api_key = os.environ.get("SYNCSO_API_KEY", "")

    if not api_key:
        raise RuntimeError("SYNCSO_API_KEY not set. Sign up at sync.so (free tier available)")

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    base = "https://api.sync.so/v2"

    with video_path.open("rb") as vf:
        video_b64 = f"data:video/mp4;base64,{base64.b64encode(vf.read()).decode()}"
    with audio_path.open("rb") as af:
        audio_b64 = f"data:audio/mpeg;base64,{base64.b64encode(af.read()).decode()}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base}/generate",
            headers=headers,
            json={
                "model": "sync-1.6.0",
                "input": [
                    {"type": "video", "data": video_b64},
                    {"type": "audio", "data": audio_b64},
                ],
                "options": {"output_format": "mp4"},
            },
        )
        resp.raise_for_status()
        job = resp.json()
        job_id = job.get("id")

    if not job_id:
        raise RuntimeError(f"sync.so returned no job ID: {job}")

    logger.info("sync.so job submitted: %s", job_id)

    for _ in range(180):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            st = await client.get(f"{base}/generate/{job_id}", headers=headers)
            st.raise_for_status()
            data = st.json()

        status = data.get("status", "")
        if status == "completed":
            video_url = data.get("outputUrl") or data.get("output_url") or data.get("video_url", "")
            if not video_url:
                raise RuntimeError(f"sync.so completed but no output URL in response: {data}")
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                dl = await client.get(video_url)
                dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("sync.so lipsync complete → %s", output_path.name)
            return output_path
        elif status in ("failed", "error"):
            raise RuntimeError(f"sync.so job failed: {data.get('error', data.get('message', 'unknown'))}")
        logger.debug("sync.so job %s: %s", job_id, status)

    raise TimeoutError(f"sync.so job {job_id} timed out")


# ─────────────────────────────────────────────────────────────────────────────
# SadTalker (Replicate) — legacy fallback
# ─────────────────────────────────────────────────────────────────────────────
async def _sadtalker_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """SadTalker via Replicate API. Uses first frame as portrait."""
    if not settings.replicate_api_key:
        raise RuntimeError("REPLICATE_API_KEY not set in .env")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        portrait_path = Path(tmp.name)

    subprocess.run(
        [FFMPEG, "-y", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(portrait_path)],
        check=True, capture_output=True,
    )

    headers = {
        "Authorization": f"Token {settings.replicate_api_key}",
        "Content-Type": "application/json",
    }

    with portrait_path.open("rb") as f:
        img_b64 = f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode()}"
    with audio_path.open("rb") as f:
        aud_b64 = f"data:audio/mpeg;base64,{base64.b64encode(f.read()).decode()}"
    portrait_path.unlink(missing_ok=True)

    async with httpx.AsyncClient(timeout=60.0) as client:
        pred = await client.post(
            "https://api.replicate.com/v1/predictions",
            json={
                "version": "3aa3dac9353cc4d6bd62a8f95957bd844003b401ca4e4a9b33baa574c549d376",
                "input": {
                    "source_image": img_b64,
                    "driven_audio": aud_b64,
                    "preprocess": "full",
                    "still_mode": False,
                    "enhancer": "gfpgan",
                },
            },
            headers=headers,
        )
        pred.raise_for_status()
        pred_id = pred.json()["id"]

    for _ in range(180):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            status = await client.get(
                f"https://api.replicate.com/v1/predictions/{pred_id}",
                headers=headers,
            )
            status.raise_for_status()
            data = status.json()

        if data["status"] == "succeeded":
            video_url = data["output"]
            async with httpx.AsyncClient(timeout=300.0) as client:
                dl = await client.get(video_url)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            return output_path
        elif data["status"] == "failed":
            raise RuntimeError(f"SadTalker failed: {data.get('error', 'unknown')}")

    raise TimeoutError(f"SadTalker prediction {pred_id} timed out")


# ─────────────────────────────────────────────────────────────────────────────
# Wav2Lip — free local lipsync (needs one-time install)
# ─────────────────────────────────────────────────────────────────────────────
async def _wav2lip_apply(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """
    Apply lipsync via Wav2Lip (local, free, runs on CPU/MPS).
    Install: git clone https://github.com/Rudrabha/Wav2Lip && pip install -r requirements.txt
    Download checkpoint: wget 'https://iiitaphyd-my.sharepoint.com/...' -O checkpoints/wav2lip_gan.pth
    Set WAV2LIP_DIR=/path/to/Wav2Lip in .env.
    """
    try:
        from app.config import settings as app_settings
        wav2lip_dir = Path(app_settings.wav2lip_dir) if app_settings.wav2lip_dir else None
    except Exception:
        import os
        wav2lip_dir = Path(os.environ.get("WAV2LIP_DIR", "")) if os.environ.get("WAV2LIP_DIR") else None

    if not wav2lip_dir or not wav2lip_dir.exists():
        raise RuntimeError(
            "Wav2Lip not configured. Clone https://github.com/Rudrabha/Wav2Lip and set WAV2LIP_DIR=/path/to/Wav2Lip"
        )

    checkpoint = wav2lip_dir / "checkpoints" / "wav2lip_gan.pth"
    if not checkpoint.exists():
        # Try wav2lip.pth (no GAN version)
        checkpoint = wav2lip_dir / "checkpoints" / "wav2lip.pth"
    if not checkpoint.exists():
        raise RuntimeError(
            f"Wav2Lip checkpoint not found in {wav2lip_dir / 'checkpoints'}. "
            "Download wav2lip_gan.pth from the Wav2Lip releases."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Wav2Lip outputs to a temp file then we convert to proper MP4
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as tmp_avi:
        tmp_avi_path = Path(tmp_avi.name)

    try:
        cmd = [
            "python3", "inference.py",
            "--checkpoint_path", str(checkpoint),
            "--face", str(video_path.resolve()),
            "--audio", str(audio_path.resolve()),
            "--outfile", str(tmp_avi_path),
            "--nosmooth",
            "--resize_factor", "1",
        ]
        logger.info("Running Wav2Lip...")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(wav2lip_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Wav2Lip failed (rc={proc.returncode}): {stderr.decode()[-400:]}")

        # Convert AVI → MP4 and scale to 9:16
        convert_cmd = [
            FFMPEG, "-y", "-i", str(tmp_avi_path),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", str(output_path),
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *convert_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            raise RuntimeError(f"Wav2Lip AVI→MP4 failed: {stderr2.decode()[-300:]}")

        logger.info("Wav2Lip complete → %s", output_path.name)
        return output_path
    finally:
        tmp_avi_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────
async def apply_lipsync(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    provider: str | None = None,
) -> Path:
    """
    Apply lipsync to a video clip using the configured provider.

    Provider options (cheapest first):
      "wav2lip"              — local, free, needs install (WAV2LIP_DIR)
      "replicate_latentsync" — ~$0.01/video via Replicate API (REPLICATE_API_KEY)
      "syncso"               — ~$0.01/video via sync.so API (SYNCSO_API_KEY)
      "hedra"                — ~$0.05/video via Hedra API (HEDRA_API_KEY)
      "sadtalker"            — via Replicate SadTalker model
      "latentsync"           — local ByteDance LatentSync (LATENTSYNC_DIR)

    Args:
        video_path:  Input video (MP4). Face must be visible — can be Ken-Burns portrait clip.
        audio_path:  Voice audio (MP3/WAV) to sync lips to.
        output_path: Output MP4 path.
        provider:    Override settings.lipsync_provider.
    """
    prov = (provider or settings.lipsync_provider).lower()
    logger.info("Applying lipsync via '%s': %s → %s", prov, video_path.name, output_path.name)

    if prov in ("syncso", "sync.so"):
        return await _syncso_apply(video_path, audio_path, output_path)
    elif prov == "replicate_latentsync":
        return await _replicate_latentsync_apply(video_path, audio_path, output_path)
    elif prov == "wav2lip":
        return await _wav2lip_apply(video_path, audio_path, output_path)
    elif prov == "hedra":
        return await _hedra_apply(video_path, audio_path, output_path)
    elif prov == "sadtalker":
        return await _sadtalker_apply(video_path, audio_path, output_path)
    else:
        # latentsync (default / local)
        return await _latentsync_apply(video_path, audio_path, output_path)
