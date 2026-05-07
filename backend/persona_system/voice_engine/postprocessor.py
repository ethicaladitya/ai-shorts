# backend/persona_system/voice_engine/postprocessor.py

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class AudioPostProcessor:
    @staticmethod
    def process(input_path: Path, output_path: Path, profile: dict):
        """
        Applies EQ, compression, and warmth filters via ffmpeg.
        """
        eq = profile.get("eq", {})
        bass = eq.get("bass", "0dB")
        treble = eq.get("treble", "0dB")
        
        # Build ffmpeg filter chain
        # 1. Equalizer (bass/treble)
        # 2. Compand (Compression for consistent warm volume)
        # 3. Volume normalization
        filters = [
            f"equalizer=f=100:t=q:w=1:g={bass.replace('dB','')}",
            f"equalizer=f=3000:t=q:w=1:g={treble.replace('dB','')}",
            "compand=0.3|0.3:0.1|0.1:-90/-60|-60/-40|-40/-30|-20/-20:6:0:-90:0.2",
            "volume=1.2"
        ]
        filter_str = ",".join(filters)
        
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-af", filter_str,
            str(output_path)
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return output_path
        except Exception as e:
            logger.error(f"Post-processing failed: {e}")
            # Fallback: just copy the original if post-processing fails
            shutil.copy(input_path, output_path)
            return output_path

import shutil # Needed for fallback
