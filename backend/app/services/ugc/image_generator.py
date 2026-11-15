"""UGC image generation — A1111 and ComfyUI dual provider with character consistency."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path

import httpx

from app.config import settings
from app.services.ugc.realism.image_prompts import (
    build_ugc_negative,
    build_ugc_positive,
    build_ugc_positive_from_spec,
    get_scene_contexts,
)
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)


async def unload_a1111_model() -> None:
    """Request A1111 to unload the current checkpoint from memory to free VRAM."""
    if not settings.sd_base_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{settings.sd_base_url}/sdapi/v1/unload-checkpoint")
            if resp.status_code == 200:
                logger.info("Successfully requested A1111 to unload checkpoint")
            else:
                logger.warning("A1111 unload checkpoint returned status %d: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Failed to request A1111 model unload: %s", e)


# ── A1111 provider ─────────────────────────────────────────────────────────────

async def _a1111_txt2img(positive: str, negative: str, width: int, height: int, seed: int = -1) -> bytes:
    """Call A1111 /sdapi/v1/txt2img — reuses the existing SD base URL."""
    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "steps": 30,          # 30 > 25: better detail without major slowdown
        "cfg_scale": 6.5,     # 6.5: more natural/less AI-rigid than 7.0
        "width": width,
        "height": height,
        "seed": seed,
        "sampler_name": "DPM++ 2M Karras",
        "restore_faces": True,
        "tiling": False,
        "enable_hr": False,   # no hires fix — we're at full 1080x1920 already
        "send_images": True,
        "save_images": False,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{settings.sd_base_url}/sdapi/v1/txt2img", json=payload)
        resp.raise_for_status()
        return base64.b64decode(resp.json()["images"][0])


# ── ComfyUI provider ────────────────────────────────────────────────────────────

def _load_comfyui_workflow() -> dict:
    """Load workflow JSON — custom path via env or bundled default."""
    if settings.comfyui_workflow_path:
        p = Path(settings.comfyui_workflow_path)
        if p.exists():
            return json.loads(p.read_text())
        logger.warning("COMFYUI_WORKFLOW_PATH %s not found — using bundled default", p)
    return _default_comfyui_workflow()


def _default_comfyui_workflow() -> dict:
    """Minimal txt2img ComfyUI workflow (no IP-Adapter — user should supply custom workflow for consistency)."""
    return {
        "3": {"inputs": {"seed": 42, "steps": 25, "cfg": 7.0, "sampler_name": "dpmpp_2m",
                         "scheduler": "karras", "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]},
              "class_type": "KSampler"},
        "4": {"inputs": {"ckpt_name": "v1-5-pruned-emaonly.ckpt"}, "class_type": "CheckpointLoaderSimple"},
        "5": {"inputs": {"width": 768, "height": 1024, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "6": {"inputs": {"text": "POSITIVE", "clip": ["4", 1]}, "class_type": "CLIPTextEncode"},
        "7": {"inputs": {"text": "NEGATIVE", "clip": ["4", 1]}, "class_type": "CLIPTextEncode"},
        "8": {"inputs": {"samples": ["3", 0], "vae": ["4", 2]}, "class_type": "VAEDecode"},
        "9": {"inputs": {"filename_prefix": "ugc", "images": ["8", 0]}, "class_type": "SaveImage"},
    }


async def _comfyui_generate(positive: str, negative: str, width: int, height: int, seed: int = -1) -> bytes:
    """Submit workflow to ComfyUI, poll until done, return PNG bytes."""
    import random
    workflow = _load_comfyui_workflow()

    # Inject prompts and dimensions into known node IDs (works with bundled default)
    for node_id, node in workflow.items():
        inputs = node.get("inputs", {})
        if node.get("class_type") == "CLIPTextEncode":
            if inputs.get("text") == "POSITIVE":
                inputs["text"] = positive
            elif inputs.get("text") == "NEGATIVE":
                inputs["text"] = negative
        if node.get("class_type") == "EmptyLatentImage":
            inputs["width"] = width
            inputs["height"] = height
        if node.get("class_type") == "KSampler":
            inputs["seed"] = seed if seed != -1 else random.randint(0, 2**31)

    client_id = uuid.uuid4().hex
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{settings.comfyui_base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
        )
        resp.raise_for_status()
        prompt_id: str = resp.json()["prompt_id"]

        # Poll history until complete
        for _ in range(120):
            await asyncio.sleep(3)
            hist = await client.get(f"{settings.comfyui_base_url}/history/{prompt_id}")
            hist.raise_for_status()
            data = hist.json()
            if prompt_id in data:
                outputs = data[prompt_id].get("outputs", {})
                for node_out in outputs.values():
                    images = node_out.get("images", [])
                    if images:
                        img_info = images[0]
                        img_resp = await client.get(
                            f"{settings.comfyui_base_url}/view",
                            params={"filename": img_info["filename"], "subfolder": img_info.get("subfolder", ""),
                                    "type": img_info.get("type", "output")},
                        )
                        img_resp.raise_for_status()
                        return img_resp.content

        raise TimeoutError(f"ComfyUI prompt {prompt_id} did not complete within 360s")


# ── Public API ──────────────────────────────────────────────────────────────────

@async_retry(max_attempts=2, base_delay=5.0)
async def generate_scene_images(
    persona: dict,
    style: str = "ugc",
    platform: str = "tiktok",
    output_dir: Path = None,
    count: int = 4,
    provider: str = "a1111",
) -> list[Path]:
    """Generate `count` UGC scene images with persona character consistency.

    Character consistency strategy:
    - A1111: persona trigger_word + fixed seed from persona YAML (seed stored in image_gen.seed)
    - ComfyUI: same, plus custom workflow can include IP-Adapter node with persona reference image
    """
    width, height = 768, 1024  # 3:4 portrait — cropped to 9:16 in video assembler

    # Fixed seed from persona for consistency; -1 = random
    seed: int = persona.get("image_gen", {}).get("seed", -1)

    scenes = get_scene_contexts(style, count)
    paths: list[Path] = []

    for i, context in enumerate(scenes):
        positive = build_ugc_positive(persona, context)
        negative = build_ugc_negative(persona)

        # Vary seed slightly per scene while keeping character consistent
        scene_seed = seed + i if seed != -1 else -1

        try:
            png_bytes: bytes | None = None

            if provider == "comfyui":
                try:
                    png_bytes = await _comfyui_generate(positive, negative, width, height, scene_seed)
                except Exception as comfy_exc:
                    logger.warning("ComfyUI failed (%s) — falling back to azure_gpt_image", comfy_exc)

            elif provider == "azure_gpt_image":
                pass  # falls through to azure below

            else:
                # a1111 — try it, fall back to azure_gpt_image if not reachable
                try:
                    png_bytes = await _a1111_txt2img(positive, negative, width, height, scene_seed)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as a1111_exc:
                    logger.warning("A1111 not reachable (%s) — falling back to azure_gpt_image", a1111_exc)

            # Azure GPT-Image-2 fallback (or primary when provider == "azure_gpt_image")
            if png_bytes is None:
                try:
                    from persona_system.image_engine.generator import _azure_dalle
                    # Request 1024x1536 portrait — closer to 9:16 than square, less cropping
                    png_bytes = await _azure_dalle(positive, width=1024, height=1536)
                except Exception as az_exc:
                    logger.error("Azure GPT-Image-2 also failed: %s", az_exc)
                    raise

            out_path = output_dir / f"scene_{i:02d}_{uuid.uuid4().hex[:6]}.png"
            out_path.write_bytes(png_bytes)
            paths.append(out_path)
            logger.info("Generated scene image %d/%d → %s", i + 1, count, out_path.name)

        except Exception as exc:
            logger.error("Scene image %d/%d failed: %s", i + 1, count, exc)

        await asyncio.sleep(0.3)

    if not paths:
        raise RuntimeError(f"All {count} image generations failed")

    if provider not in ("comfyui", "azure_gpt_image"):
        await unload_a1111_model()

    return paths


@async_retry(max_attempts=2, base_delay=5.0)
async def generate_shot_images(
    persona: dict,
    shots: list,  # list[ShotSpec]
    output_dir: Path,
    provider: str = "a1111",
) -> list[Path]:
    """Generate one image per ShotSpec with shot-specific framing/environment.

    This is the scene-planner-aware version of generate_scene_images.
    Each shot gets a unique prompt that encodes its framing, environment,
    emotion, and camera note — producing genuinely varied images.
    """
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    width, height = 768, 1024  # 3:4 → cropped to 9:16 in assembler
    base_seed: int = persona.get("image_gen", {}).get("seed", -1)
    paths: list[Path] = []

    for shot in shots:
        positive = build_ugc_positive_from_spec(persona, shot)
        negative = build_ugc_negative(persona)
        scene_seed = base_seed + shot.index if base_seed != -1 else -1

        png_bytes: bytes | None = None
        try:
            if provider == "comfyui":
                try:
                    png_bytes = await _comfyui_generate(positive, negative, width, height, scene_seed)
                except Exception as exc:
                    logger.warning("ComfyUI shot %d failed (%s) — fallback", shot.index, exc)

            elif provider != "azure_gpt_image":
                try:
                    png_bytes = await _a1111_txt2img(positive, negative, width, height, scene_seed)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                    logger.warning("A1111 shot %d not reachable (%s) — fallback", shot.index, exc)

            if png_bytes is None:
                from persona_system.image_engine.generator import _azure_dalle
                # Request portrait 1024x1536 — GPT-Image-2 closest to 9:16, less cropping
                png_bytes = await _azure_dalle(positive, width=1024, height=1536)

            out_path = output_dir / f"shot_{shot.index:02d}_{uuid.uuid4().hex[:6]}.png"
            out_path.write_bytes(png_bytes)
            paths.append(out_path)
            logger.info("Shot image %d/%d (%s) → %s", shot.index + 1, len(shots), shot.shot_type, out_path.name)

        except Exception as exc:
            logger.error("Shot image %d failed: %s", shot.index, exc)
            # Use prior image as fallback if we have one
            if paths:
                paths.append(paths[-1])
            # else: skip — assembler will handle missing images

        await asyncio.sleep(0.2)

    if not paths:
        raise RuntimeError("All shot image generations failed")

    if provider not in ("comfyui", "azure_gpt_image"):
        await unload_a1111_model()

    return paths
