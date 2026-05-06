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
    get_scene_contexts,
)
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)


# ── A1111 provider ─────────────────────────────────────────────────────────────

async def _a1111_txt2img(positive: str, negative: str, width: int, height: int, seed: int = -1) -> bytes:
    """Call A1111 /sdapi/v1/txt2img — reuses the existing SD base URL."""
    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "steps": 25,
        "cfg_scale": 7.0,
        "width": width,
        "height": height,
        "seed": seed,
        "sampler_name": "DPM++ 2M Karras",
        "restore_faces": True,
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
            if provider == "comfyui":
                png_bytes = await _comfyui_generate(positive, negative, width, height, scene_seed)
            else:
                # a1111 or any SD-compatible provider — also handles azure_gpt_image fallback
                if provider == "azure_gpt_image":
                    from persona_system.image_engine.generator import _azure_dalle
                    png_bytes = await _azure_dalle(positive, width=1024, height=1024)
                else:
                    png_bytes = await _a1111_txt2img(positive, negative, width, height, scene_seed)

            out_path = output_dir / f"scene_{i:02d}_{uuid.uuid4().hex[:6]}.png"
            out_path.write_bytes(png_bytes)
            paths.append(out_path)
            logger.info("Generated scene image %d/%d → %s", i + 1, count, out_path.name)

        except Exception as exc:
            logger.error("Scene image %d/%d failed: %s", i + 1, count, exc)

        await asyncio.sleep(0.3)

    if not paths:
        raise RuntimeError(f"All {count} image generations failed")

    return paths
