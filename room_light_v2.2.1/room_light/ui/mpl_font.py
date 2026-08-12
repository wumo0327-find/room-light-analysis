"""
ui/mpl_font.py  —  Configure matplotlib to use a CJK-capable font.
Call setup_font() once at startup before any matplotlib draw.
"""
import matplotlib
import matplotlib.font_manager as fm
from pathlib import Path

_PREFERRED = [
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Noto Serif CJK SC",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Microsoft YaHei",
]


def setup_font() -> str:
    """Pick the best available CJK font and configure matplotlib."""
    available = {f.name for f in fm.fontManager.ttflist}

    chosen = "DejaVu Sans"
    for name in _PREFERRED:
        if name in available:
            chosen = name
            break

    matplotlib.rcParams["font.family"]       = chosen
    matplotlib.rcParams["font.sans-serif"]   = [chosen, "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    return chosen
