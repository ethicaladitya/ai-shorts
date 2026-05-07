"""
persona_system/lora_dataset/builder.py
LoRA dataset preparation: folder structure, auto-captioning, training config.
"""
from __future__ import annotations
import asyncio, json, logging, shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DATASET_STRUCTURE = """
lora_dataset/
  images/           ← 30-100 curated images (JPG/PNG)
  captions/         ← .txt caption file per image (same stem)
  config/
    dataset.toml    ← kohya dataset config
    train.toml      ← kohya training config
"""


async def auto_caption_image(image_path: Path, trigger_word: str, tagger_url: str = "http://localhost:7860") -> str:
    """Generate a wd14 tag-based caption and prepend the trigger word."""
    import base64, httpx
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{tagger_url}/tagger/v1/interrogate",
                             json={"image": b64, "model": "wd14-convnextv2-v2", "threshold": 0.35})
            r.raise_for_status()
            tags = list(r.json().get("caption", {}).keys())
            return f"{trigger_word}, " + ", ".join(tags[:40])
    except Exception as e:
        logger.warning("Auto-caption failed for %s: %s", image_path.name, e)
        return f"{trigger_word}, 1girl, solo, realistic, looking at viewer"


async def build_dataset(
    source_images_dir: Path,
    dataset_root: Path,
    trigger_word: str = "nova_v1",
    tagger_url: str = "http://localhost:7860",
) -> dict:
    images_out = dataset_root / "images"
    captions_out = dataset_root / "captions"
    images_out.mkdir(parents=True, exist_ok=True)
    captions_out.mkdir(parents=True, exist_ok=True)

    images = list(source_images_dir.glob("*.jpg")) + list(source_images_dir.glob("*.png"))
    logger.info("Processing %d images for LoRA dataset...", len(images))

    captioned = 0
    for img in images:
        dst = images_out / img.name
        shutil.copy2(img, dst)
        caption = await auto_caption_image(dst, trigger_word, tagger_url)
        cap_file = captions_out / f"{img.stem}.txt"
        cap_file.write_text(caption)
        captioned += 1
        logger.info("  %s → %s", img.name, caption[:60] + "...")

    # Write kohya dataset.toml
    dataset_toml = f"""
[general]
shuffle_caption = true
keep_tokens = 1

[[datasets]]
resolution = 512
batch_size = 1

  [[datasets.subsets]]
  image_dir = "{images_out}"
  caption_extension = ".txt"
  num_repeats = 10
"""
    (dataset_root / "config" / "dataset.toml").parent.mkdir(exist_ok=True)
    (dataset_root / "config" / "dataset.toml").write_text(dataset_toml.strip())

    # Write kohya train.toml
    train_toml = f"""
pretrained_model_name_or_path = "runwayml/stable-diffusion-v1-5"
output_dir = "./lora_output"
output_name = "{trigger_word}"
save_model_as = "safetensors"
network_module = "networks.lora"
network_dim = 32
network_alpha = 16
learning_rate = 1e-4
unet_lr = 1e-4
text_encoder_lr = 5e-5
lr_scheduler = "cosine_with_restarts"
lr_warmup_steps = 100
max_train_steps = 1500
train_batch_size = 1
mixed_precision = "fp16"
save_every_n_steps = 250
logging_dir = "./logs"
dataset_config = "./lora_dataset/config/dataset.toml"
"""
    (dataset_root / "config" / "train.toml").write_text(train_toml.strip())

    return {"images": len(images), "captioned": captioned, "trigger_word": trigger_word}


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./lora_dataset/images")
    trigger = sys.argv[2] if len(sys.argv) > 2 else "nova_v1"
    result = asyncio.run(build_dataset(src, Path("./lora_dataset"), trigger))
    print(json.dumps(result, indent=2))
