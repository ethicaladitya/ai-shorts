# backend/persona_system/voice_engine/preprocessor.py

import re
import random

FILLERS = ["um,", "uh,", "idk,", "wait,", "like,", "I mean,", "actually,"]
INTIMATE_FILLERS = ["baby,", "hey,", "listen,", "look,"]

class VoicePreprocessor:
    def __init__(self, persona=None):
        self.persona = persona

    def process(self, text: str, profile: dict, emotion: str = "neutral") -> str:
        # 1. Lowercase for natural flow
        text = text.lower().strip()

        # 2. Emotional Sentence Structure
        if emotion == "shy":
            text = self._add_stutters(text)
        elif emotion == "slightly_chaotic":
            text = text.replace(".", "!").replace(",", "...")
        elif emotion == "whisper":
            text = self._add_whisper_style(text)
            # Whisper already injects pauses — skip the random pause pass
            # to avoid doubling up and creating garbled ellipsis chains.
            text = re.sub(r'\s+', ' ', text).strip()
            return text

        # 3. Inject Pauses based on profile (skipped for whisper above)
        pause_freq = profile.get("pause_frequency", 0.15)
        text = self._inject_pauses(text, pause_freq)

        # 4. Inject Fillers
        randomness = profile.get("randomness", 0.3)
        text = self._inject_fillers(text, randomness)

        # 5. Cleanup extra spaces
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _inject_pauses(self, text: str, freq: float) -> str:
        words = text.split()
        result = []
        for word in words:
            result.append(word)
            if (word.endswith(",") or word.endswith(".")) and random.random() < freq:
                result.append("...")
            elif random.random() < freq * 0.5:
                result.append("...")
        return " ".join(result)

    def _inject_fillers(self, text: str, rate: float) -> str:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []
        for i, sent in enumerate(sentences):
            if i > 0 and random.random() < rate:
                filler = random.choice(FILLERS)
                result.append(f"{filler} {sent}")
            else:
                result.append(sent)
        return " ".join(result)

    def _add_stutters(self, text: str) -> str:
        words = text.split()
        if not words: return text
        idx = random.randint(0, len(words)-1)
        word = words[idx]
        if len(word) > 3 and not word.startswith("http"):
            words[idx] = f"{word[0]}-{word}"
        return " ".join(words)

    def _add_whisper_style(self, text: str) -> str:
        # Intimate, soft flow — use pauses only.
        # Do NOT inject parenthetical markers like (softly) — TTS engines
        # will speak them literally or hang trying to parse them.
        text = text.replace(".", "... ").replace(",", "... ")
        # Trim double ellipsis runs
        text = re.sub(r'\.\.\.\s+\.\.\.', '...', text)
        return text
