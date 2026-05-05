"""D-ID Talking Head (V2 Avatars) API client.

Authentication:
  D-ID uses HTTP Basic auth. The API key from studio.d-id.com is in
  "username:password" format.  Pass it as DID_API_KEY and it will be
  base64-encoded into the Authorization header automatically.

Endpoints used:
  POST /images   – upload portrait image, returns hosted URL
  POST /audios   – upload MP3 audio,     returns hosted URL
  POST /talks    – create talk (async job), returns talk ID
  GET  /talks/{id} – poll status → result_url when done
"""
import asyncio
import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

D_ID_BASE = "https://api.d-id.com"


def _auth_header(api_key: str) -> str:
    """Build Basic auth header from D-ID API key (username:password from studio)."""
    encoded = base64.b64encode(api_key.encode()).decode()
    return f"Basic {encoded}"


def _headers(api_key: str) -> dict:
    return {
        "Authorization": _auth_header(api_key),
        "Accept": "application/json",
    }


async def upload_image(image_path: Path, api_key: str) -> str:
    """Upload a portrait image to D-ID. Returns the hosted image URL."""
    ext = image_path.suffix.lower().lstrip(".")
    mime_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/jpeg")
    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(image_path, "rb") as f:
            resp = await client.post(
                f"{D_ID_BASE}/images",
                headers={"Authorization": _auth_header(api_key)},
                files={"image": (image_path.name, f, mime)},
            )
        resp.raise_for_status()
        data = resp.json()
    url = data.get("url")
    if not url:
        raise ValueError(f"D-ID image upload: no URL in response — {data}")
    logger.info(f"D-ID image uploaded: {url}")
    return url


async def upload_audio(audio_path: Path, api_key: str) -> str:
    """Upload an MP3 audio file to D-ID. Returns the hosted audio URL."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                f"{D_ID_BASE}/audios",
                headers={"Authorization": _auth_header(api_key)},
                files={"audio": (audio_path.name, f, "audio/mpeg")},
            )
        resp.raise_for_status()
        data = resp.json()
    url = data.get("url")
    if not url:
        raise ValueError(f"D-ID audio upload: no URL in response — {data}")
    logger.info(f"D-ID audio uploaded: {url}")
    return url


async def create_talk(image_url: str, audio_url: str, api_key: str) -> str:
    """Create a D-ID /talks request. Returns the talk ID."""
    payload = {
        "source_url": image_url,
        "script": {
            "type": "audio",
            "audio_url": audio_url,
        },
        "config": {
            "fluent": True,
            "result_format": "mp4",
        },
    }
    h = {**_headers(api_key), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{D_ID_BASE}/talks", headers=h, json=payload)
        resp.raise_for_status()
        data = resp.json()
    talk_id = data.get("id")
    if not talk_id:
        raise ValueError(f"D-ID create_talk: no ID in response — {data}")
    logger.info(f"D-ID talk created: {talk_id}")
    return talk_id


async def wait_for_talk(
    talk_id: str,
    api_key: str,
    timeout: float = 360.0,
    poll_interval: float = 5.0,
) -> str:
    """Poll GET /talks/{id} until status=done. Returns result_url."""
    h = _headers(api_key)
    elapsed = 0.0
    while elapsed < timeout:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{D_ID_BASE}/talks/{talk_id}", headers=h)
            resp.raise_for_status()
            data = resp.json()
        status = data.get("status", "")
        logger.info(f"D-ID talk {talk_id}: {status} ({elapsed:.0f}s)")
        if status == "done":
            url = data.get("result_url")
            if not url:
                raise ValueError("D-ID talk done but result_url is missing")
            return url
        if status == "error":
            desc = data.get("error", {}).get("description", "unknown error")
            raise RuntimeError(f"D-ID animation failed: {desc}")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"D-ID talk {talk_id} did not complete within {timeout}s")


async def download_video(url: str, dest: Path) -> Path:
    """Stream-download the D-ID result video to a local file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
    logger.info(f"D-ID video downloaded → {dest}")
    return dest
