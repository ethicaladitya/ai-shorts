"""Voice generation provider abstraction."""
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Named Maya voice profiles ─────────────────────────────────────────────────
# Use as provider name "maya:aditya" or "maya:of_model" anywhere a provider
# string is accepted. The description is passed as the `voice` argument.
MAYA_VOICE_PROFILES: dict[str, str] = {
    "aditya": (
        "Indian-American male in his mid-20s, Aditya Shah. "
        "Confident, articulate, natural tech-creator delivery. "
        "Clear diction, moderate-to-fast pace, warm baritone with genuine enthusiasm. "
        "Sounds like a knowledgeable friend talking directly to camera."
    ),
    "of_model": (
        "Young American woman in her early 20s. Warm, intimate, slightly breathy voice "
        "with playful, teasing intonation. Confident and expressive, speaks with a "
        "natural flirty cadence — engaging and suggestive without being over the top."
    ),
}


class VoiceProvider(ABC):
    @abstractmethod
    async def generate_speech(self, text: str, output_path: Path, voice: str = "alloy") -> Path:
        pass


class AzureOpenAIVoiceProvider(VoiceProvider):
    """Azure OpenAI TTS — uses a configurable deployment (e.g. gpt-4o-mini-tts)."""

    def __init__(self, endpoint: str, api_key: str, api_version: str,
                 tts_deployment: str = "gpt-4o-mini-tts", tts_voice: str = "alloy",
                 tts_endpoint: str = "", tts_api_version: str = "", tts_api_key: str = ""):
        # TTS may live on a different Azure resource endpoint than the chat model
        self.endpoint = (tts_endpoint or endpoint).rstrip("/")
        self.api_key = tts_api_key or api_key  # prefer dedicated TTS key if set
        self.api_version = tts_api_version or api_version
        self.tts_deployment = tts_deployment or "gpt-4o-mini-tts"
        self.tts_voice = tts_voice or "alloy"

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import httpx
        import re

        used_voice = voice or self.tts_voice
        url = (
            f"{self.endpoint}/openai/deployments/{self.tts_deployment}"
            f"/audio/speech?api-version={self.api_version}"
        )

        # Clean text before sending — the preprocessor can produce garbled output
        # that causes the TTS to hang (excessive ellipsis, lone letters like "a... m...")
        clean = text
        clean = re.sub(r'(\.\.\.\s*){2,}', '... ', clean)   # collapse repeated ...
        clean = re.sub(r'\b([a-z])\.\.\.\s+([a-z])\.\.\.', r'\1.\2.', clean)  # fix a... m... -> a.m.
        clean = re.sub(r'\s+', ' ', clean).strip()
        clean = clean[:800]  # max ~200 words; TTS handles this comfortably

        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"model": self.tts_deployment, "input": clean, "voice": used_voice},
            )
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path


class MAIVoiceProvider(VoiceProvider):
    """MAI-Voice-1 — Microsoft AI Voice via Azure AI Foundry."""

    def __init__(self, endpoint: str, api_key: str, model_name: str = "MAI-Voice-1"):
        # endpoint should be the full Azure AI Foundry inference endpoint, e.g.
        # https://<resource>.services.ai.azure.com or a dedicated endpoint URL
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name or "MAI-Voice-1"

    async def generate_speech(self, text: str, output_path: Path, voice: str = "alloy") -> Path:
        import httpx

        # Azure AI Foundry TTS path
        url = f"{self.endpoint}/models/{self.model_name}/audio/speech?api-version=2025-05-15-preview"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"model": self.model_name, "input": text, "voice": voice},
            )
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path


class OpenAIVoiceProvider(VoiceProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate_speech(self, text: str, output_path: Path, voice: str = "alloy") -> Path:
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": "tts-1", "input": text, "voice": voice},
            )
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path


class ElevenLabsVoiceProvider(VoiceProvider):
    # Local file to cache the cloned voice_id so we only clone once
    _CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "elevenlabs_voice_cache.txt"
    _cloned_voice_id: str | None = None   # in-process cache

    def __init__(self, api_key: str, voice_id: str = "j05EIz3iI3JmBTWC3CsA",
                 sample_url: str = ""):
        self.api_key = api_key
        self.voice_id = voice_id
        self.sample_url = sample_url  # URL of the reference MP3 to clone if needed

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_cached_voice_id(self) -> str | None:
        """Return a previously cloned voice_id from disk cache."""
        if ElevenLabsVoiceProvider._cloned_voice_id:
            return ElevenLabsVoiceProvider._cloned_voice_id
        if self._CACHE_FILE.exists():
            vid = self._CACHE_FILE.read_text().strip()
            if vid:
                ElevenLabsVoiceProvider._cloned_voice_id = vid
                return vid
        return None

    def _save_cached_voice_id(self, voice_id: str) -> None:
        self._CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._CACHE_FILE.write_text(voice_id)
        ElevenLabsVoiceProvider._cloned_voice_id = voice_id

    async def _clone_voice_from_sample(self, client) -> str:
        """
        Download the reference MP3 and use ElevenLabs Instant Voice Cloning
        to add the voice to this account. Returns the new voice_id.
        """
        import logging, tempfile, os
        logger = logging.getLogger(__name__)

        sample_url = self.sample_url or (
            "https://storage.googleapis.com/eleven-public-prod/database/user/"
            "1sQEubbF2LayrwfzcqizACaG7MC3/voices/j05EIz3iI3JmBTWC3CsA/"
            "UB0gk19IQjWeV1Zsk3X7.mp3"
        )
        logger.info("Downloading voice sample for cloning: %s", sample_url)
        dl = await client.get(sample_url, timeout=30.0)
        dl.raise_for_status()

        # Write to a temp file
        tmp = Path(tempfile.mktemp(suffix=".mp3"))
        tmp.write_bytes(dl.content)

        logger.info("Cloning voice via ElevenLabs Instant Voice Cloning...")
        try:
            with open(tmp, "rb") as f:
                clone_resp = await client.post(
                    "https://api.elevenlabs.io/v1/voices/add",
                    headers={"xi-api-key": self.api_key},
                    data={"name": "Nova Persona Voice"},
                    files={"files": ("sample.mp3", f, "audio/mpeg")},
                    timeout=60.0,
                )
            clone_resp.raise_for_status()
            new_id = clone_resp.json()["voice_id"]
            logger.info("Voice cloned successfully. New voice_id: %s", new_id)
            self._save_cached_voice_id(new_id)
            return new_id
        finally:
            tmp.unlink(missing_ok=True)

    async def _resolve_voice_id(self, client) -> str:
        """
        Return a working voice_id:
        1. Check disk cache (from a previous successful clone).
        2. Try the configured voice_id — if it returns 200, use it.
        3. Otherwise clone from sample and return the new id.
        """
        import logging
        logger = logging.getLogger(__name__)

        cached = self._load_cached_voice_id()
        if cached:
            return cached

        # Quick probe to see if the voice_id is in this account
        probe = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json={"text": "test", "model_id": "eleven_multilingual_v2",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=15.0,
        )
        if probe.status_code == 200:
            return self.voice_id

        logger.warning(
            "ElevenLabs voice %s not in account (HTTP %s: %s) — cloning from sample",
            self.voice_id, probe.status_code, probe.text[:200],
        )
        return await self._clone_voice_from_sample(client)

    # ── Public interface ──────────────────────────────────────────────────────

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import httpx, re, logging
        logger = logging.getLogger(__name__)

        # Clean text — same safeguards as the Azure provider
        clean = re.sub(r'(\.\.\.\s*){2,}', '... ', text)
        clean = re.sub(r'\s+', ' ', clean).strip()
        clean = clean[:800]

        async with httpx.AsyncClient(timeout=60.0) as client:
            vid = voice or await self._resolve_voice_id(client)
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "text": clean,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.45, "similarity_boost": 0.80,
                                       "style": 0.35, "use_speaker_boost": True},
                },
            )
            if not resp.is_success:
                logger.error("ElevenLabs TTS failed (HTTP %s): %s", resp.status_code, resp.text[:300])
                resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path


class AzureSpeechVoiceProvider(VoiceProvider):
    """Azure Cognitive Services Neural TTS (same resource as Azure OpenAI)."""

    def __init__(self, api_key: str, region: str = "eastus", voice: str = "en-US-AriaNeural"):
        self.api_key = api_key
        self.region = region
        self.voice = voice or "en-US-AriaNeural"

    @staticmethod
    def _build_ssml(text: str, voice: str) -> str:
        """Build expressive SSML from plain script text."""
        import re

        # Escape XML chars
        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        # Voices that support mstts:express-as styles
        STYLE_MAP = {
            "en-US-AriaNeural":   "excited",
            "en-US-DavisNeural":  "excited",
            "en-US-JennyNeural":  "newscast",
            "en-US-GuyNeural":    "newscast",
            "en-US-SaraNeural":   "cheerful",
            "en-US-TonyNeural":   "excited",
        }
        style = STYLE_MAP.get(voice)

        # Split into sentences, preserving punctuation
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        parts = []
        for i, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue

            # Detect sentence type
            is_question  = sent.endswith("?")
            is_exclaim   = sent.endswith("!")
            is_hook      = i == 0  # first sentence = hook, punch harder
            has_number   = bool(re.search(r'\b\d+[\d,.%x]*\b', sent))

            # Per-sentence prosody
            if is_hook:
                rate, pitch = "fast", "+5%"
            elif is_exclaim:
                rate, pitch = "fast", "+8%"
            elif is_question:
                rate, pitch = "medium", "+3%"
            elif has_number:
                rate, pitch = "medium", "default"
            else:
                rate, pitch = "medium", "default"

            # Emphasise ALL-CAPS words and numbers
            def _process_words(s: str) -> str:
                tokens = []
                for word in s.split():
                    clean = re.sub(r'[^A-Za-z0-9]', '', word)
                    if clean.isupper() and len(clean) > 1:
                        tokens.append(f'<emphasis level="strong">{_esc(word)}</emphasis>')
                    elif re.fullmatch(r'\d[\d,.%x]*', clean):
                        tokens.append(f'<emphasis level="moderate">{_esc(word)}</emphasis>')
                    else:
                        tokens.append(_esc(word))
                return " ".join(tokens)

            body = _process_words(sent)

            # Wrap in prosody
            inner = f'<prosody rate="{rate}" pitch="{pitch}">{body}</prosody>'

            # Pause after each sentence (longer after hooks)
            pause = '<break time="400ms"/>' if is_hook else '<break time="200ms"/>'

            parts.append(inner + pause)

        content = "\n".join(parts)

        if style:
            voice_body = (
                f'<mstts:express-as style="{style}" styledegree="1.5">'
                f'{content}'
                f'</mstts:express-as>'
            )
        else:
            voice_body = content

        return (
            '<speak version="1.0" xml:lang="en-US" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="http://www.w3.org/2001/mstts">'
            f'<voice name="{voice}">{voice_body}</voice>'
            '</speak>'
        )

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import httpx

        used_voice = voice or self.voice
        ssml = self._build_ssml(text, used_voice)
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": self.api_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3",
                },
                content=ssml.encode("utf-8"),
            )
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.content)
            return output_path


class KokoroVoiceProvider(VoiceProvider):
    """Kokoro-82M local TTS via kokoro-onnx — no extra containers needed.

    On first call, downloads the quantized ONNX model (~80 MB) and the
    voices file from the kokoro-onnx GitHub release into ``model_dir``.
    Subsequent calls load from the local cache.  CPU inference via ONNX
    Runtime — no PyTorch or GPU required.

    The model runs comfortably at realtime speed on CPU for scripts of any
    length used in AI shorts (~150-300 words).
    """

    MODEL_URL   = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
    VOICES_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    MODEL_FILE  = "kokoro-v1.0.int8.onnx"
    VOICES_FILE = "voices-v1.0.bin"

    def __init__(self, model_dir: str = "/app/data/kokoro", voice: str = "af_sky"):
        self._model_dir = Path(model_dir)
        self.voice = voice or "af_sky"
        self._kokoro = None  # lazy init after models are downloaded

    def _model_path(self) -> Path:
        return self._model_dir / self.MODEL_FILE

    def _voices_path(self) -> Path:
        return self._model_dir / self.VOICES_FILE

    async def _ensure_models(self) -> None:
        if self._model_path().exists() and self._voices_path().exists():
            return
        import httpx
        self._model_dir.mkdir(parents=True, exist_ok=True)
        for url, path in [
            (self.MODEL_URL,  self._model_path()),
            (self.VOICES_URL, self._voices_path()),
        ]:
            if path.exists():
                continue
            logger.info("Downloading Kokoro model file: %s", path.name)
            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
            logger.info("Saved %s (%.1f MB)", path.name, path.stat().st_size / 1e6)

    def _get_kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro  # type: ignore
            self._kokoro = Kokoro(str(self._model_path()), str(self._voices_path()))
        return self._kokoro

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import asyncio
        import subprocess
        import tempfile

        import soundfile as sf  # type: ignore

        await self._ensure_models()
        used_voice = voice or self.voice
        kokoro = self._get_kokoro()

        # CPU inference — run in thread so the event loop stays free
        samples, sample_rate = await asyncio.to_thread(
            kokoro.create, text, used_voice
        )

        # Write WAV to temp file then convert to MP3 via ffmpeg
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await asyncio.to_thread(sf.write, tmp_path, samples, sample_rate)
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_path,
                    "-codec:a", "libmp3lame", "-q:a", "2",
                    str(output_path),
                ],
                check=True,
                timeout=60,
                capture_output=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return output_path


class VoiceboxVoiceProvider(VoiceProvider):
    """Voicebox local TTS — calls the REST API running at voicebox_url.

    Uses the /generate/stream endpoint which blocks until audio is ready and
    returns raw WAV bytes.  FFmpeg (present in the backend container) converts
    the WAV to MP3 so the rest of the pipeline sees a standard audio file.

    On first call the provider auto-discovers (or creates) a Kokoro preset
    profile named "ai-shorts-voice" so no manual Voicebox UI setup is needed.
    """

    PROFILE_NAME = "ai-shorts-voice"
    PRESET_VOICE_ID = "af_sky"  # energetic female American-English Kokoro voice

    def __init__(self, url: str, engine: str = "kokoro", profile_id: str = ""):
        self.url = url.rstrip("/")
        self.engine = engine
        self._cached_profile_id: str | None = profile_id.strip() or None

    async def _get_or_create_profile(self) -> str:
        if self._cached_profile_id:
            return self._cached_profile_id

        import asyncio
        import httpx

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{self.url}/profiles")
                    resp.raise_for_status()
                    profiles = resp.json()
                    for p in (profiles if isinstance(profiles, list) else []):
                        if p.get("name") == self.PROFILE_NAME:
                            self._cached_profile_id = p["id"]
                            logger.info("Voicebox: reusing profile %s", self._cached_profile_id)
                            return self._cached_profile_id
                    # Profile not found — create a Kokoro preset profile
                    resp = await client.post(
                        f"{self.url}/profiles",
                        json={
                            "name": self.PROFILE_NAME,
                            "language": "en",
                            "voice_type": "preset",
                            "preset_engine": "kokoro",
                            "preset_voice_id": self.PRESET_VOICE_ID,
                            "default_engine": "kokoro",
                        },
                    )
                    resp.raise_for_status()
                    self._cached_profile_id = resp.json()["id"]
                    logger.info("Voicebox: created profile %s", self._cached_profile_id)
                    return self._cached_profile_id
            except Exception as exc:
                if attempt < 2:
                    logger.warning("Voicebox not ready (attempt %d/3): %s", attempt + 1, exc)
                    await asyncio.sleep(5)
                else:
                    raise RuntimeError(f"Voicebox unreachable at {self.url}: {exc}") from exc

        raise RuntimeError("Voicebox profile setup failed")

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import subprocess
        import tempfile

        import httpx

        profile_id = await self._get_or_create_profile()

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self.url}/generate/stream",
                json={
                    "profile_id": profile_id,
                    "text": text,
                    "language": "en",
                    "engine": self.engine,
                    "normalize": True,
                },
            )
            resp.raise_for_status()
            wav_bytes = resp.content

        # Convert WAV → MP3 using ffmpeg (installed in the backend container)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_path,
                    "-codec:a", "libmp3lame", "-q:a", "2",
                    str(output_path),
                ],
                check=True,
                timeout=60,
                capture_output=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return output_path


class MayaVoiceProvider(VoiceProvider):
    """Maya1 — 3B-parameter open-source TTS by maya-research (HuggingFace).

    Requires: pip install torch transformers snac soundfile
    Hardware:  16 GB+ VRAM recommended (CUDA/MPS). Falls back to CPU.

    The ``voice`` parameter is a natural-language description such as:
      "A young woman in her late 20s with an American accent, warm, expressive."
    If omitted the configured ``voice_description`` default is used.
    """

    # Special token IDs for the Maya1 prompt format
    _CODE_START = 128257
    _CODE_END   = 128258
    _CODE_OFFSET = 128266
    _SNAC_MIN   = 128266
    _SNAC_MAX   = 156937
    _SOH        = 128259
    _EOH        = 128260
    _SOA        = 128261
    _TEXT_EOT   = 128009

    def __init__(
        self,
        voice_description: str = (
            "A young woman in her late 20s with an American accent. "
            "Warm, expressive, conversational tone with clear diction."
        ),
        device: str = "auto",
        model_id: str = "maya-research/maya1",
    ):
        self.voice_description = voice_description
        self.device = device
        self.model_id = model_id
        self._model = None
        self._tokenizer = None
        self._snac = None

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            # Disable MPS for Maya due to PyTorch Metal Performance Shaders allocation bugs
            # (total bytes of NDArray > 2**32 failed assertions). CPU runs fast enough.
        except Exception:
            pass
        return "cpu"

    def _load_models(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from snac import SNAC
        except ImportError as e:
            raise RuntimeError(
                "Maya TTS requires extra packages. Install with:\n"
                "  pip install torch transformers snac soundfile"
            ) from e

        device = self._resolve_device()
        import platform
        is_arm64_mac = platform.system() == "Darwin" and platform.machine() == "arm64"

        if device == "cuda":
            dtype = torch.bfloat16
        elif device == "mps" or is_arm64_mac:
            # Apple Silicon CPU/MPS supports float16 very efficiently
            dtype = torch.float16
        else:
            dtype = torch.float32

        logger.info("Loading Maya1 model from %s on %s …", self.model_id, device)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, trust_remote_code=True
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=dtype,
            device_map="auto" if device == "cuda" else None,
            trust_remote_code=True,
        )
        if device != "cuda":
            self._model = self._model.to(device)
        self._model.eval()

        logger.info("Loading SNAC decoder …")
        self._snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
        self._snac = self._snac.to(device)
        self._device = device
        logger.info("Maya1 ready on %s", device)

    def unload_models(self):
        """Free memory by deleting model objects and emptying PyTorch cache."""
        import gc
        import torch
        logger.info("Unloading Maya1 and SNAC models to free memory...")
        self._model = None
        self._tokenizer = None
        self._snac = None
        gc.collect()

        # Empty GPU/MPS cache
        device = self._resolve_device()
        if device == "cuda":
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        elif device == "mps":
            try:
                import torch.mps
                torch.mps.empty_cache()
            except (ImportError, AttributeError):
                pass

    def _build_prompt(self, description: str, text: str) -> str:
        tok = self._tokenizer
        soh  = tok.decode([self._SOH])
        eoh  = tok.decode([self._EOH])
        soa  = tok.decode([self._SOA])
        sos  = tok.decode([self._CODE_START])
        eot  = tok.decode([self._TEXT_EOT])
        bos  = tok.bos_token
        formatted = f'<description="{description}"> {text}'
        return soh + bos + formatted + eot + eoh + soa + sos

    def _extract_snac_codes(self, token_ids: list) -> list:
        try:
            eos_idx = token_ids.index(self._CODE_END)
        except ValueError:
            eos_idx = len(token_ids)
        return [t for t in token_ids[:eos_idx] if self._SNAC_MIN <= t <= self._SNAC_MAX]

    def _unpack_snac(self, tokens: list) -> list:
        frames = len(tokens) // 7
        tokens = tokens[:frames * 7]
        l1, l2, l3 = [], [], []
        for i in range(frames):
            s = tokens[i * 7:(i + 1) * 7]
            l1.append((s[0] - self._CODE_OFFSET) % 4096)
            l2.extend([(s[1] - self._CODE_OFFSET) % 4096, (s[4] - self._CODE_OFFSET) % 4096])
            l3.extend([
                (s[2] - self._CODE_OFFSET) % 4096,
                (s[3] - self._CODE_OFFSET) % 4096,
                (s[5] - self._CODE_OFFSET) % 4096,
                (s[6] - self._CODE_OFFSET) % 4096,
            ])
        return [l1, l2, l3]

    def _synthesize(self, text: str, description: str):
        """Run model inference synchronously (call via asyncio.to_thread)."""
        import torch
        import numpy as np

        self._load_models()

        # Limit threads on CPU to prevent locking up the host system
        if self._device == "cpu":
            curr_threads = torch.get_num_threads()
            if curr_threads > 4:
                logger.info("Restricting PyTorch CPU threads from %d to 4 to prevent hangs", curr_threads)
                torch.set_num_threads(4)

        prompt = self._build_prompt(description, text)
        inputs = self._tokenizer(prompt, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with torch.inference_mode():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=2048,
                min_new_tokens=28,
                temperature=0.4,
                top_p=0.9,
                repetition_penalty=1.1,
                do_sample=True,
                eos_token_id=self._CODE_END,
                pad_token_id=self._tokenizer.pad_token_id,
            )

        generated = outputs[0, inputs["input_ids"].shape[1]:].tolist()
        snac_tokens = self._extract_snac_codes(generated)
        if len(snac_tokens) < 7:
            raise RuntimeError("Maya: model generated too few SNAC tokens — check input text")

        levels = self._unpack_snac(snac_tokens)
        codes = [
            torch.tensor(lvl, dtype=torch.long, device=self._device).unsqueeze(0)
            for lvl in levels
        ]
        with torch.inference_mode():
            z_q = self._snac.quantizer.from_codes(codes)
            audio = self._snac.decoder(z_q)[0, 0].cpu().numpy()

        # Trim SNAC warmup artefact
        if len(audio) > 2048:
            audio = audio[2048:]
        return audio.astype(np.float32), 24000

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import asyncio
        import subprocess
        import tempfile
        import re
        import numpy as np

        import soundfile as sf

        try:
            description = voice or self.voice_description

            # Split text into sentences to prevent token truncation and run much faster
            sentences = []
            raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
            for rs in raw_sentences:
                rs = rs.strip()
                if not rs:
                    continue
                # If a sentence is abnormally long, chunk it by clauses (commas, semicolons) to avoid slowdown
                if len(rs.split()) > 35:
                    sub_parts = re.split(r'(?<=[,;])\s+', rs)
                    sentences.extend([p.strip() for p in sub_parts if p.strip()])
                else:
                    sentences.append(rs)

            if not sentences:
                sentences = [text]

            logger.info("Maya starting synthesis for %d sentences", len(sentences))
            all_samples = []
            sample_rate = 24000

            for i, sent in enumerate(sentences):
                logger.info("Maya synthesizing sentence %d/%d: '%s'", i + 1, len(sentences), sent)
                samples, sample_rate = await asyncio.to_thread(self._synthesize, sent, description)
                all_samples.append(samples)

            if len(all_samples) > 1:
                samples = np.concatenate(all_samples)
            else:
                samples = all_samples[0]

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                await asyncio.to_thread(sf.write, tmp_path, samples, sample_rate)
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_path, "-codec:a", "libmp3lame", "-q:a", "2", str(output_path)],
                    check=True, timeout=60, capture_output=True,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            return output_path
        finally:
            self.unload_models()


def get_voice_provider(provider_name: str = "azure_openai", **kwargs) -> VoiceProvider:
    if provider_name == "azure_openai":
        return AzureOpenAIVoiceProvider(
            endpoint=kwargs.get("endpoint", ""),
            api_key=kwargs.get("api_key", ""),
            api_version=kwargs.get("api_version", ""),
            tts_deployment=kwargs.get("tts_deployment", "gpt-4o-mini-tts"),
            tts_voice=kwargs.get("tts_voice", "alloy"),
            tts_endpoint=kwargs.get("tts_endpoint", ""),
            tts_api_version=kwargs.get("tts_api_version", ""),
            tts_api_key=kwargs.get("tts_api_key", ""),
        )
    elif provider_name == "mai_voice":
        return MAIVoiceProvider(
            endpoint=kwargs.get("mai_voice_endpoint", ""),
            api_key=kwargs.get("mai_voice_api_key", ""),
            model_name=kwargs.get("mai_voice_name", "MAI-Voice-1"),
        )
    elif provider_name == "openai":
        return OpenAIVoiceProvider(api_key=kwargs.get("openai_api_key", ""))
    elif provider_name == "elevenlabs":
        from app.config import settings as _s
        return ElevenLabsVoiceProvider(
            api_key=kwargs.get("elevenlabs_api_key", _s.elevenlabs_api_key),
            voice_id=kwargs.get("elevenlabs_voice_id", _s.elevenlabs_voice_id),
        )
    elif provider_name == "azure_speech":
        return AzureSpeechVoiceProvider(
            api_key=kwargs.get("azure_speech_api_key") or kwargs.get("api_key", ""),
            region=kwargs.get("azure_speech_region", "eastus"),
            voice=kwargs.get("azure_speech_voice", "en-US-JennyNeural"),
        )
    elif provider_name == "voicebox":
        from app.config import settings as _s
        return VoiceboxVoiceProvider(
            url=kwargs.get("voicebox_url", _s.voicebox_url),
            engine=kwargs.get("voicebox_engine", _s.voicebox_engine),
            profile_id=kwargs.get("voicebox_profile_id", _s.voicebox_profile_id),
        )
    elif provider_name == "kokoro":
        from app.config import settings as _s
        return KokoroVoiceProvider(
            model_dir=kwargs.get("kokoro_model_dir", _s.kokoro_model_dir),
            voice=kwargs.get("kokoro_voice", _s.kokoro_voice),
        )
    elif provider_name == "maya" or provider_name.startswith("maya:"):
        from app.config import settings as _s
        # Support "maya:profile_name" to select a named voice profile.
        # Settings-page values take priority over the hardcoded defaults so
        # users can customise profiles without touching code.
        if ":" in provider_name:
            profile_key = provider_name.split(":", 1)[1]
            # Check live settings first (patched from DB by the settings router)
            settings_attr = f"maya_profile_{profile_key}"
            description = (
                kwargs.get(f"maya_profile_{profile_key}")
                or getattr(_s, settings_attr, "")
                or MAYA_VOICE_PROFILES.get(profile_key)
                or kwargs.get("maya_voice_description", _s.maya_voice_description)
            )
        else:
            description = kwargs.get("maya_voice_description", _s.maya_voice_description)
        return MayaVoiceProvider(
            voice_description=description,
            device=kwargs.get("maya_device", _s.maya_device),
            model_id=kwargs.get("maya_model_id", _s.maya_model_id),
        )
    else:
        raise ValueError(f"Unknown voice provider: {provider_name}")
