"""FFmpeg filter chain builders for UGC post-processing realism."""
from __future__ import annotations

import random


def ugc_video_filter(width: int = 1080, height: int = 1920, seed: int = 0) -> str:
    """
    Return a vf filter string that applies:
    - Dynamic film grain (seed-varied so it shifts per clip)
    - Subtle camera micro-shake via geq luminance offset
    - Chromatic aberration via rgbashift (iPhone-style fringing)
    - Slight vignette

    Intensity is kept low — imperfection without distraction.
    """
    # Vary shake frequency slightly per video so clips don't look identical
    rng = random.Random(seed)
    fx = round(0.28 + rng.random() * 0.06, 3)
    fy = round(0.17 + rng.random() * 0.05, 3)
    ax = round(1.2 + rng.random() * 0.4, 1)
    ay = round(0.9 + rng.random() * 0.3, 1)

    # Micro camera shake (geq luma shift — keeps chroma anchored for natural look)
    shake = (
        f"geq="
        f"lum='lum(X+sin(2*PI*T*{fx})*{ax},Y+cos(2*PI*T*{fy})*{ay})':"
        f"cb='cb(X,Y)':"
        f"cr='cr(X,Y)'"
    )

    # Dynamic grain — noise seed shifts per second so it's not a repeating loop
    grain = "noise=alls=8:allf=t+u+0"

    # Chromatic aberration — shift R channel +1px right, B channel -1px left (iPhone fringing)
    ca = "rgbashift=rh=0:rv=0:gh=0:gv=0:bh=-1:bv=0"

    # Vignette — slightly randomised angle for organic feel
    vig_angle = round(0.60 + rng.random() * 0.05, 3)
    vignette = f"vignette=angle={vig_angle}:mode=backward"

    return f"{shake},{grain},{ca},{vignette}"


def saturation_boost_filter(saturation: float = 1.06) -> str:
    """Mild saturation + slight warm colour cast (iPhone photos skew warm)."""
    return f"eq=saturation={saturation}:gamma_r=1.02:gamma_b=0.98"


def loudnorm_filter() -> str:
    """EBU R128 loudness normalisation — keeps voice levels consistent across clips."""
    return "loudnorm=I=-16:TP=-1.5:LRA=11"


def full_ugc_video_filter(width: int = 1080, height: int = 1920, seed: int = 0) -> str:
    """Combined video filter: saturation + shake + grain + CA + vignette."""
    sat = saturation_boost_filter()
    ugc = ugc_video_filter(width, height, seed)
    return f"{sat},{ugc}"


def ken_burns_params(index: int, seed: int = 0) -> dict:
    """Return varied Ken-Burns zoom parameters for a given scene index.

    Alternates between zoom-in and zoom-out, with randomised start x/y offset
    so adjacent scenes don't feel mechanical.
    """
    rng = random.Random(seed + index * 17)
    zoom_in = index % 2 == 0  # alternate direction
    speed = round(0.00015 + rng.random() * 0.0002, 5)  # 0.00015–0.00035 (slower = smoother)

    if zoom_in:
        zoom_expr = f"min(zoom+{speed},1.12)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        # Zoom out — start slightly zoomed, pull back
        zoom_expr = f"max(zoom-{speed},1.0)"
        # Anchor to slightly off-center to add variety
        x_off = rng.uniform(-0.05, 0.05)
        y_off = rng.uniform(-0.05, 0.05)
        x_expr = f"iw*({0.5 + x_off}-(1/zoom)*0.5)"
        y_expr = f"ih*({0.5 + y_off}-(1/zoom)*0.5)"

    return {
        "zoom_expr": zoom_expr,
        "x_expr": x_expr,
        "y_expr": y_expr,
        "speed": speed,
        "zoom_in": zoom_in,
    }
