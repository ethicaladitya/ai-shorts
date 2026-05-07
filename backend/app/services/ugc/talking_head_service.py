"""Talking head stage — local SadTalker CLI first, slideshow fallback."""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)

# Character consistency note:
# When SadTalker is active: the input portrait is reused for every clip, so
# character consistency is guaranteed by the source image itself (strongest method).
# When slideshow mode is used: consistency comes from using the same generated
# scene images with matching trigger_word + seed in the image generator.


def _find_sadtalker_cli() -> str | None:
    """Locate the SadTalker CLI binary."""
    path = settings.ugc_sadtalker_cli_path
    if path and Path(path).exists():
        return path
    # Try common install locations
    for candidate in ("/opt/homebrew/bin/sadtalker", "/usr/local/bin/sadtalker"):
        if Path(candidate).exists():
            return candidate
    return shutil.which("sadtalker")


@async_retry(max_attempts=2, base_delay=5.0)
async def generate_talking_head(
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
    provider: str = "sadtalker",
) -> Path | None:
    """Generate a talking head video clip.

    Returns path to the output MP4, or None if slideshow fallback was used
    (caller handles the None case by using images + audio directly).
    """
    if provider == "slideshow":
        logger.info("Talking head disabled (provider=slideshow) — using image slideshow")
        return None

    if provider in ("replicate", "did"):
        return await _cloud_talking_head(portrait_path, audio_path, output_dir, provider)

    # Local SadTalker
    cli = _find_sadtalker_cli()
    if not cli:
        logger.warning(
            "SadTalker CLI not found (set SADTALKER_CLI_PATH or install sadtalker). "
            "Falling back to slideshow mode."
        )
        return None

    return await _run_sadtalker(cli, portrait_path, audio_path, output_dir)


async def _run_sadtalker(
    cli_path: str,
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        cli_path,
        "--driven_audio", str(audio_path),
        "--source_image", str(portrait_path),
        "--result_dir", str(output_dir),
        "--still",           # minimal head movement — more realistic for UGC
        "--preprocess", "full",
        "--enhancer", "gfpgan",
    ]
    logger.info("Running SadTalker: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("SadTalker failed (rc=%d): %s", proc.returncode, stderr.decode()[:500])
        return None

    # SadTalker writes to result_dir — find the output MP4
    mp4_files = sorted(output_dir.glob("*.mp4"))
    if not mp4_files:
        logger.warning("SadTalker completed but no MP4 found in %s", output_dir)
        return None

    return mp4_files[-1]


async def _cloud_talking_head(
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
    provider: str,
) -> Path | None:
    """D-ID or Replicate SadTalker as paid fallback."""
    try:
        if provider == "replicate":
            from app.services.replicate_avatar_provider import ReplicateAvatarProvider
            prov = ReplicateAvatarProvider()
            # Replicate provider expects URL or base64; we pass the local path and let it handle upload
            output_url = await prov.generate(
                face_image_path=str(portrait_path),
                audio_path=str(audio_path),
                script="",
            )
            if output_url:
                import httpx
                out_path = output_dir / f"talking_head_{uuid.uuid4().hex[:8]}.mp4"
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.get(output_url)
                    resp.raise_for_status()
                    out_path.write_bytes(resp.content)
                return out_path

        elif provider == "did":
            from app.services.d_id_provider import DIDProvider
            prov = DIDProvider()
            video_url = await prov.create_talk(
                face_image_url=str(portrait_path),
                voice_file=str(audio_path),
                script="",
            )
            if video_url:
                import httpx
                out_path = output_dir / f"talking_head_{uuid.uuid4().hex[:8]}.mp4"
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.get(video_url)
                    resp.raise_for_status()
                    out_path.write_bytes(resp.content)
                return out_path

    except Exception as exc:
        logger.error("Cloud talking head (%s) failed: %s — falling back to slideshow", provider, exc)

    return None
