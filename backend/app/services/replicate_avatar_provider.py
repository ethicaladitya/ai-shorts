"""Replicate-hosted SadTalker avatar provider.

Generates a talking-head video from a portrait image + audio using the
SadTalker model hosted on Replicate (https://replicate.com/cjwbw/sadtalker).

Free tier: Replicate gives $5 of free credits (~72 runs) on signup.
No watermark. No local GPU needed.

Usage in .env:
    AVATAR_PROVIDER=replicate
    REPLICATE_API_KEY=r8_xxxxxxxxxxxxxxxxxxxx

Optional tweaks:
    REPLICATE_SADTALKER_VERSION=a519cc0cfebaaeade068b23899165a11ec76aaa1d2b313d40d214f204ec957a3
"""
import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Pinned model version — update when a newer version is published
DEFAULT_VERSION = "a519cc0cfebaaeade068b23899165a11ec76aaa1d2b313d40d214f204ec957a3"
REPLICATE_API = "https://api.replicate.com/v1"


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # ask Replicate to block until done (up to 60s)
    }


async def _upload_file_as_data_uri(path: Path) -> str:
    """Read file and encode as a data URI (Replicate accepts these as inputs)."""
    import base64
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else (
        "image/png" if path.suffix.lower() == ".png" else
        "audio/mpeg" if path.suffix.lower() == ".mp3" else
        "audio/wav"
    )
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"


async def generate_avatar_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    api_key: str,
    model_version: str = DEFAULT_VERSION,
    still_mode: bool = True,
    use_enhancer: bool = False,
    preprocess: str = "crop",
    timeout: float = 600.0,
) -> Path:
    """
    Submit a SadTalker prediction to Replicate and download the result.

    Args:
        image_path:    Portrait image (.jpg / .png).
        audio_path:    Voice audio (.mp3 / .wav).
        output_path:   Where to save the resulting .mp4.
        api_key:       Replicate API key (r8_...).
        model_version: SadTalker model version hash.
        still_mode:    Fewer head movements (recommended for talking head content).
        use_enhancer:  Run GFPGAN face enhancer (better quality, slower).
        preprocess:    "crop" | "resize" | "full".
        timeout:       Max seconds to wait for the job.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image_uri = await _upload_file_as_data_uri(image_path)
    audio_uri = await _upload_file_as_data_uri(audio_path)

    payload = {
        "version": model_version,
        "input": {
            "source_image": image_uri,
            "driven_audio": audio_uri,
            "preprocess": preprocess,
            "still_mode": still_mode,
            "use_enhancer": use_enhancer,
            "use_eyeblink": True,
            "pose_style": 0,
            "facerender": "facevid2vid",
            "expression_scale": 1.0,
            "size_of_image": 256,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{REPLICATE_API}/predictions",
            headers=_headers(api_key),
            json=payload,
        )
        resp.raise_for_status()
        prediction = resp.json()

    pred_id = prediction.get("id")
    if not pred_id:
        raise ValueError(f"Replicate: no prediction ID in response — {prediction}")

    logger.info(f"Replicate SadTalker prediction {pred_id} submitted")

    # If the server already finished (Prefer: wait), grab it now
    if prediction.get("status") == "succeeded":
        video_url = prediction["output"]
        logger.info(f"Replicate prediction {pred_id} completed immediately")
        return await _download(video_url, output_path)

    # Otherwise poll until done
    poll_interval = 5.0
    elapsed = 0.0
    poll_url = prediction.get("urls", {}).get("get") or f"{REPLICATE_API}/predictions/{pred_id}"

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(poll_url, headers=_headers(api_key))
            resp.raise_for_status()
            prediction = resp.json()

        status = prediction.get("status", "")
        logger.info(f"Replicate {pred_id}: {status} ({elapsed:.0f}s elapsed)")

        if status == "succeeded":
            video_url = prediction["output"]
            return await _download(video_url, output_path)
        elif status in ("failed", "canceled"):
            err = prediction.get("error") or "unknown error"
            raise RuntimeError(f"Replicate SadTalker failed: {err}")

    raise TimeoutError(f"Replicate prediction {pred_id} did not complete within {timeout}s")


async def _download(url: str, dest: Path) -> Path:
    """Stream-download the Replicate output video."""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
    logger.info(f"Replicate video downloaded → {dest}")
    return dest
