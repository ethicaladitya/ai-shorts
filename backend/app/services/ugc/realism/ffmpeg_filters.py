"""FFmpeg filter chain builders for UGC post-processing realism."""
from __future__ import annotations


def ugc_video_filter(width: int = 1080, height: int = 1920) -> str:
    """
    Return a vf filter string that applies:
    - Subtle camera shake via geq luminance offset
    - Mild film grain via noise filter
    - Slight vignette

    Applied as a second FFmpeg pass after base render.
    Intensity is kept low — the goal is imperfection, not distraction.
    """
    shake = (
        "geq="
        "lum='lum(X+sin(2*PI*T*0.31)*1.4,Y+cos(2*PI*T*0.19)*1.1)':"
        "cb='cb(X,Y)':"
        "cr='cr(X,Y)'"
    )
    grain = "noise=alls=6:allf=t+u"
    vignette = "vignette=angle=PI/5:mode=backward"

    return f"{shake},{grain},{vignette}"


def saturation_boost_filter(saturation: float = 1.08) -> str:
    """Mild saturation boost — makes colours feel slightly more vibrant without looking graded."""
    return f"eq=saturation={saturation}"


def speed_filter(speed: float = 0.95) -> str:
    """Audio speed adjustment for more casual delivery feel (applied to audio stream)."""
    return f"atempo={speed}"


def full_ugc_video_filter(width: int = 1080, height: int = 1920) -> str:
    """Combined video filter: saturation + shake + grain + vignette."""
    sat = saturation_boost_filter()
    ugc = ugc_video_filter(width, height)
    return f"{sat},{ugc}"
