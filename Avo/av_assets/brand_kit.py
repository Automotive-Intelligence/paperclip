"""Load the brand video kit. No build script may hardcode a font, color or logo.

`kit("aiphoneguy")` returns a dict with every path already expanded and every
asset EXISTENCE-CHECKED, so a missing font fails loudly at build time instead of
silently falling back to a system face. The silent fallback is exactly how an
AIPG short shipped in Helvetica with a teal accent and no logo.
"""
import pathlib

import yaml

_CFG = pathlib.Path.home() / "paperclip/config/brand_video_kit.yaml"


def _expand(p):
    return pathlib.Path(p).expanduser().resolve()


def kit(brand: str) -> dict:
    cfg = yaml.safe_load(_CFG.read_text())
    if brand not in cfg["brands"]:
        raise KeyError(f"{brand} not in brand_video_kit.yaml; add it there, not in code")
    b = dict(cfg["defaults"])
    b.update(cfg["brands"][brand])

    missing = []
    for group in ("fonts", "logos"):
        resolved = {}
        for k, v in (b.get(group) or {}).items():
            path = _expand(v)
            if not path.exists():
                missing.append(f"{group}.{k} -> {path}")
            resolved[k] = path
        b[group] = resolved
    if missing:
        raise FileNotFoundError(
            "brand kit asset(s) missing, refusing to build with fallbacks:\n  "
            + "\n  ".join(missing))
    return b


def font(kit_dict: dict, role: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(kit_dict["fonts"][role]), size)


def rgb(hex_str: str):
    h = hex_str.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
