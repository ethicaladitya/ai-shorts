# backend/persona_system/voice_engine/scorer.py

import os
import subprocess
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class AudioScorer:
    @staticmethod
    def score(audio_path: Path, text: str) -> float:
        """
        Scores the audio based on duration consistency and technical quality.
        Returns a score from 0.0 to 1.0.
        """
        try:
            # 1. Get audio duration
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(audio_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
            
            # 2. Heuristic: Words per second
            # Natural speech is roughly 2-3 words per second.
            words = text.split()
            if not words: return 0.0
            
            wps = len(words) / duration
            
            # Score based on 'natural' WPS range (1.5 to 4.0)
            if 1.8 <= wps <= 3.5:
                score = 0.9
            elif 1.0 <= wps <= 5.0:
                score = 0.6
            else:
                score = 0.2
                
            # 3. File size check (ensure it's not silent/empty)
            if audio_path.stat().st_size < 1000: # Less than 1KB is likely failed
                score *= 0.1
                
            return score
        except Exception as e:
            logger.error(f"Scoring failed: {e}")
            return 0.0
