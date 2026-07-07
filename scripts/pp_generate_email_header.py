"""Paper & Purpose — render the email header as PNG.

Companion to assets/pp_email_header.svg. Same composition, rendered to a
raster bitmap that email clients can reliably display.

Why PNG (not the SVG inline): Gmail web blocks data: URIs in <img src> tags
and Outlook does not render SVG at all. The reliable cross-client path for
email images is a hosted PNG referenced by URL. Klaviyo hosts uploaded
images on its own CDN — see pp_upload_email_header.py for the upload step.

Render scale: 2x (1200x400 internal canvas, downsampled to 600x200) so the
result is crisp on retina mail clients while staying inside the email's
declared 600px width.

Fonts: macOS-installed Baskerville stands in for Cormorant Garamond Light
on the wordmark and tagline. Visually close (humanist serif, refined
proportions) and reliably installed.

Usage:
    python scripts/pp_generate_email_header.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
OUT_PATH = ASSETS / "pp_email_header.png"

# Brand palette (hex -> RGB).
BONE_CREAM = (242, 237, 228)         # #F2EDE4 — background
FOREST_OLIVE = (74, 83, 64)          # #4A5340 — tagline
DUSTY_SAGE = (156, 168, 142)         # #9CA88E — botanical
HERITAGE_GOLD = (184, 153, 104)      # #B89968 — botanical accent + rule
ANTIQUE_CHARCOAL = (42, 42, 38)      # #2A2A26 — wordmark

# Render scale (2x for retina, then downsample for final PNG).
SCALE = 2
W, H = 600 * SCALE, 200 * SCALE

# Fonts — macOS standard install paths.
FONT_BASKERVILLE = "/System/Library/Fonts/Supplemental/Baskerville.ttc"


def find_font(path: str, size: int, face_index: int = 0) -> ImageFont.FreeTypeFont:
    """Load a TTC/TTF font with a specific face index."""
    return ImageFont.truetype(path, size=size, index=face_index)


def botanical_sprig(draw: ImageDraw.ImageDraw, ox: int, oy: int, scale: float = 1.0) -> None:
    """Draw the floral sprig (vertical stem + 4 leaves + gold bud) at (ox, oy).

    Origin (ox, oy) is the top of the bud. Drawn relative to that.
    Mirrors the SVG path shapes in assets/pp_email_header.svg as closely as
    Pillow's polygon primitives allow (Pillow lacks bezier curves, so the
    leaves are 3-point teardrops which read similarly at this size).
    """
    sage = DUSTY_SAGE
    gold = HERITAGE_GOLD

    def s(v: float) -> int:
        return int(v * scale)

    # Stem — a vertical line with very slight visual taper (single line is
    # crisp enough at 2x scale).
    draw.line([(ox, oy + s(8)), (ox, oy + s(130))], fill=sage, width=max(2, s(2)))

    # Leaves: each a 3-point polygon (tip + base-near-stem + base-out).
    leaves = [
        # (tip_x, tip_y, base1_x, base1_y, base2_x, base2_y)
        (ox - s(28), oy + s(95), ox, oy + s(105), ox - s(2), oy + s(95)),    # lower-left
        (ox + s(34), oy + s(70), ox, oy + s(80), ox + s(2), oy + s(70)),     # mid-right
        (ox - s(30), oy + s(45), ox, oy + s(55), ox - s(2), oy + s(45)),     # upper-left
        (ox + s(24), oy + s(28), ox, oy + s(35), ox + s(2), oy + s(28)),     # tiny right
    ]
    for tx, ty, b1x, b1y, b2x, b2y in leaves:
        draw.polygon([(tx, ty), (b1x, b1y), (b2x, b2y)], fill=sage)

    # Heritage gold bud at top.
    r = s(7)
    draw.ellipse([(ox - r, oy - r), (ox + r, oy + r)], fill=gold)
    # Inner highlight for depth.
    r2 = s(4)
    inner_y = oy - s(1)
    draw.ellipse([(ox - r2, inner_y - r2), (ox + r2, inner_y + r2)],
                 fill=(220, 200, 165))  # lighter gold


def render() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (W, H), BONE_CREAM)
    draw = ImageDraw.Draw(img)

    # --- Botanical sprig (left) ---
    # SVG anchors sprig at translate(80, 32), bud at top relative (30, 4).
    # So absolute bud center in SVG coords: (110, 36).
    sprig_x = int(110 * SCALE)
    sprig_y = int(36 * SCALE)
    botanical_sprig(draw, sprig_x, sprig_y, scale=SCALE)

    # --- Wordmark "Paper & Purpose" ---
    # Baskerville TTC face 0 = Regular. We use a slightly oversized point
    # then offset to vertically align with the SVG (which sits the wordmark
    # baseline near y=100).
    wm_font = find_font(FONT_BASKERVILLE, size=int(46 * SCALE), face_index=0)
    wm_text = "Paper & Purpose"
    # Measure for centering.
    wm_bbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]
    # Center horizontally at x=360 (SVG anchor), but Pillow has no
    # text-anchor=middle — compute origin.
    wm_cx = int(360 * SCALE)
    wm_x = wm_cx - wm_w // 2
    wm_y = int(80 * SCALE) - wm_bbox[1]
    draw.text((wm_x, wm_y), wm_text, fill=ANTIQUE_CHARCOAL, font=wm_font)

    # --- Tagline "BE TRANSFORMED" ---
    # Letter-spaced small caps. Pillow has no letter-spacing, so we render
    # character-by-character with manual kerning.
    tag_font = find_font(FONT_BASKERVILLE, size=int(13 * SCALE), face_index=0)
    tag_text = "BE TRANSFORMED"
    tag_kern = int(7 * SCALE)  # extra space between chars

    # Pre-measure to center.
    char_widths = [draw.textbbox((0, 0), c, font=tag_font)[2] for c in tag_text]
    total_w = sum(char_widths) + tag_kern * (len(tag_text) - 1)
    tag_cx = int(360 * SCALE)
    tag_x = tag_cx - total_w // 2
    tag_y = int(120 * SCALE)

    cursor = tag_x
    for ch, cw in zip(tag_text, char_widths):
        draw.text((cursor, tag_y), ch, fill=FOREST_OLIVE, font=tag_font)
        cursor += cw + tag_kern

    # --- Heritage Gold rule line ---
    # SVG: x1=60, x2=540, y=172, stroke-width=0.6 opacity=0.65.
    # Pillow has no stroke opacity, so we soften by using a muted gold.
    rule_y = int(172 * SCALE)
    rule_x1 = int(60 * SCALE)
    rule_x2 = int(540 * SCALE)
    # Slightly washed gold for the "0.65 opacity" effect (blend with BG).
    muted_gold = (
        int(HERITAGE_GOLD[0] * 0.65 + BONE_CREAM[0] * 0.35),
        int(HERITAGE_GOLD[1] * 0.65 + BONE_CREAM[1] * 0.35),
        int(HERITAGE_GOLD[2] * 0.65 + BONE_CREAM[2] * 0.35),
    )
    draw.line([(rule_x1, rule_y), (rule_x2, rule_y)], fill=muted_gold, width=max(1, int(SCALE * 0.6)))

    # Downsample to final 600x200 for smooth edges (Lanczos = best for typography).
    final = img.resize((600, 200), Image.LANCZOS)
    final.save(OUT_PATH, "PNG", optimize=True)
    print(f"Wrote {OUT_PATH}  ({OUT_PATH.stat().st_size}b)")


if __name__ == "__main__":
    render()
