"""
tools/carousel_builder.py — Multi-slide carousel generator for social media.
Generates branded image carousels for Instagram, LinkedIn, and Facebook.
Combines AI image generation with PIL-based text overlays for consistent branding.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.image_gen import (
    build_image_prompt,
    generate_image_bytes,
    image_gen_ready,
)
from tools.zernio import upload_media_to_zernio

# Carousel dimensions by platform.
CAROUSEL_DIMENSIONS = {
    "instagram": (1080, 1080),
    "linkedin": (1080, 1080),
    "facebook": (1080, 1080),
    "default": (1080, 1080),
}

# Max slides per platform.
MAX_SLIDES = {
    "instagram": 10,
    "linkedin": 20,
    "facebook": 10,
    "default": 10,
}


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "slide"


def _overlay_text_on_image(
    image_bytes: bytes,
    headline: str,
    body: str = "",
    slide_number: int = 0,
    total_slides: int = 0,
    business_key: str = "",
    cta: str = "",
) -> bytes:
    """
    Overlay branded text on a generated image.

    Uses PIL to add headline, body text, slide counter, and CTA
    on top of an AI-generated background image.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size

    # Create a semi-transparent overlay for text readability.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Dark gradient at bottom for text.
    gradient_top = int(height * 0.35)
    for y in range(gradient_top, height):
        alpha = int(180 * ((y - gradient_top) / (height - gradient_top)))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Load fonts.
    def _load_font(size):
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    margin = 60
    text_y = int(height * 0.55)

    # Slide counter (e.g., "3 / 7").
    if slide_number and total_slides:
        counter_font = _load_font(24)
        counter_text = f"{slide_number} / {total_slides}"
        draw.text(
            (width - margin - 80, margin),
            counter_text,
            font=counter_font,
            fill=(255, 255, 255, 200),
        )

    # Headline.
    if headline:
        headline_font = _load_font(48)
        # Word wrap headline.
        words = headline.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            if len(test) <= 28:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))

        for line in lines[:3]:
            draw.text((margin, text_y), line, font=headline_font, fill=(255, 255, 255))
            text_y += 60

    # Body text.
    if body:
        text_y += 10
        body_font = _load_font(28)
        words = body.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            if len(test) <= 42:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))

        for line in lines[:4]:
            draw.text((margin, text_y), line, font=body_font, fill=(230, 230, 230))
            text_y += 40

    # CTA button on last slide.
    if cta:
        cta_font = _load_font(30)
        cta_y = height - margin - 60
        draw.rounded_rectangle(
            (margin, cta_y, width - margin, cta_y + 55),
            radius=12,
            fill=(255, 255, 255),
        )
        draw.text(
            (margin + 20, cta_y + 10),
            cta[:50],
            font=cta_font,
            fill=(20, 20, 20),
        )

    # Convert back to RGB for PNG export.
    final = img.convert("RGB")
    out = io.BytesIO()
    final.save(out, format="PNG", quality=95)
    return out.getvalue()


def build_carousel(
    slides: List[Dict[str, Any]],
    business_key: str,
    platform: str = "instagram",
    cta: str = "",
) -> List[bytes]:
    """
    Generate a complete carousel of branded images.

    Each slide dict should contain:
        - headline (str): Main text for the slide.
        - body (str, optional): Supporting text.
        - image_prompt (str, optional): Custom prompt for the background image.
          If omitted, one is auto-generated from headline + body.

    Args:
        slides: List of slide dicts with headline/body/image_prompt.
        business_key: Brand key for consistent styling.
        platform: Target platform for dimensions.
        cta: Call-to-action text for the final slide.

    Returns:
        List of PNG image bytes, one per slide.
    """
    max_count = MAX_SLIDES.get(platform, MAX_SLIDES["default"])
    slides = slides[:max_count]
    total = len(slides)
    result_images = []

    use_ai = image_gen_ready()

    for idx, slide in enumerate(slides, 1):
        headline = (slide.get("headline") or "").strip()
        body = (slide.get("body") or "").strip()
        custom_prompt = (slide.get("image_prompt") or "").strip()
        is_last = idx == total

        if use_ai:
            # Generate AI background image.
            prompt = custom_prompt or build_image_prompt(
                headline=headline,
                subhead=body,
                business_key=business_key,
                content_type="carousel_slide",
                platform=platform,
            )
            try:
                bg_bytes = generate_image_bytes(
                    prompt=prompt,
                    business_key=business_key,
                    platform=platform,
                    aspect_ratio="1:1",
                )
            except Exception as e:
                logging.warning(
                    "[Carousel] AI image gen failed for slide %d, falling back to solid: %s",
                    idx,
                    e,
                )
                bg_bytes = _solid_background(business_key, platform)
        else:
            bg_bytes = _solid_background(business_key, platform)

        # Overlay text on background.
        slide_bytes = _overlay_text_on_image(
            image_bytes=bg_bytes,
            headline=headline,
            body=body,
            slide_number=idx,
            total_slides=total,
            business_key=business_key,
            cta=cta if is_last else "",
        )
        result_images.append(slide_bytes)

        logging.info("[Carousel] Built slide %d/%d for %s", idx, total, business_key)

    return result_images


def _solid_background(business_key: str, platform: str = "default") -> bytes:
    """Generate a solid brand-colored background as fallback."""
    from PIL import Image

    brand_colors = {
        "callingdigital": (13, 32, 22),
        "autointelligence": (10, 24, 42),
        "aiphoneguy": (36, 17, 5),
    }
    color = brand_colors.get(business_key, (20, 20, 30))
    dims = CAROUSEL_DIMENSIONS.get(platform, CAROUSEL_DIMENSIONS["default"])

    img = Image.new("RGB", dims, color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def build_and_upload_carousel(
    slides: List[Dict[str, Any]],
    business_key: str,
    platform: str = "instagram",
    cta: str = "",
) -> List[str]:
    """
    Generate carousel images and upload them all to Zernio CDN.

    This is the high-level function the social pipeline calls.

    Args:
        slides: List of slide dicts with headline/body/image_prompt.
        business_key: Brand key.
        platform: Target platform.
        cta: CTA text for final slide.

    Returns:
        List of public CDN URLs for carousel images (ready for Zernio mediaItems).
    """
    image_list = build_carousel(
        slides=slides,
        business_key=business_key,
        platform=platform,
        cta=cta,
    )

    uploaded_urls = []
    for idx, img_bytes in enumerate(image_list, 1):
        slug = _slugify(slides[idx - 1].get("headline", ""))[:30]
        filename = (
            f"{business_key}-carousel-{slug}-{idx}-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.png"
        )
        url = upload_media_to_zernio(
            file_bytes=img_bytes,
            filename=filename,
            mime_type="image/png",
        )
        uploaded_urls.append(url)
        logging.info("[Carousel] Uploaded slide %d/%d: %s", idx, len(image_list), url)

    return uploaded_urls
