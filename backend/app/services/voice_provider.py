"""Voice generation provider abstraction."""
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


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

        used_voice = voice or self.tts_voice
        url = (
            f"{self.endpoint}/openai/deployments/{self.tts_deployment}"
            f"/audio/speech?api-version={self.api_version}"
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"model": self.tts_deployment, "input": text, "voice": used_voice},
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
    def __init__(self, api_key: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"):
        self.api_key = api_key
        self.voice_id = voice_id

    async def generate_speech(self, text: str, output_path: Path, voice: str = "") -> Path:
        import httpx

        vid = voice or self.voice_id
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            )
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
        return ElevenLabsVoiceProvider(
            api_key=kwargs.get("elevenlabs_api_key", ""),
            voice_id=kwargs.get("elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM"),
        )
    elif provider_name == "azure_speech":
        return AzureSpeechVoiceProvider(
            api_key=kwargs.get("azure_speech_api_key") or kwargs.get("api_key", ""),
            region=kwargs.get("azure_speech_region", "eastus"),
            voice=kwargs.get("azure_speech_voice", "en-US-JennyNeural"),
        )
    elif provider_name == "voicebox":
        return VoiceboxVoiceProvider(
            url=kwargs.get("voicebox_url", "http://localhost:17493"),
            engine=kwargs.get("voicebox_engine", "kokoro"),
            profile_id=kwargs.get("voicebox_profile_id", ""),
        )
    elif provider_name == "kokoro":
        return KokoroVoiceProvider(
            model_dir=kwargs.get("kokoro_model_dir", "/app/data/kokoro"),
            voice=kwargs.get("kokoro_voice", "af_sky"),
        )
    else:
        raise ValueError(f"Unknown voice provider: {provider_name}")

