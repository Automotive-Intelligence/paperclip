import io
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from tools.zernio import publish_content_piece_to_zernio, upload_media_to_zernio


BRAND_THEME = {
    "callingdigital": {
        "name": "Calling Digital",
        "bg": (13, 32, 22),
        "accent": (57, 211, 140),
        "text": (240, 255, 248),
    },
    "autointelligence": {
        "name": "Automotive Intelligence",
        "bg": (10, 24, 42),
        "accent": (71, 161, 255),
        "text": (236, 246, 255),
    },
    "aiphoneguy": {
        "name": "The AI Phone Guy",
        "bg": (36, 17, 5),
        "accent": (242, 153, 74),
        "text": (255, 246, 234),
    },
}


STYLE_PRESETS = {
    "clean": {
        "title_size": 52,
        "body_size": 34,
        "accent_height": 150,
    },
    "bold": {
        "title_size": 58,
        "body_size": 38,
        "accent_height": 170,
    },
    "minimal": {
        "title_size": 48,
        "body_size": 30,
        "accent_height": 120,
    },
}


CREATIVE_DIRECTOR_SKILLS = {
    "callingdigital": {
        "default_style": "bold",
        "default_palette": "#0D2016,#39D38C,#F0FFF8",
        "tagline": "Growth systems for serious owners",
        "platform_styles": {
            "instagram": "bold",
            "facebook": "clean",
            "linkedin": "minimal",
        },
    },
    "autointelligence": {
        "default_style": "clean",
        "default_palette": "#0A182A,#47A1FF,#ECF6FF",
        "tagline": "Dealership AI that drives appointments",
        "platform_styles": {
            "facebook": "clean",
            "instagram": "bold",
            "linkedin": "minimal",
        },
    },
    "aiphoneguy": {
        "default_style": "bold",
        "default_palette": "#241105,#F2994A,#FFF6EA",
        "tagline": "Never miss a call that should convert",
        "platform_styles": {
            "instagram": "bold",
            "facebook": "clean",
            "linkedin": "clean",
        },
    },
}


LOGO_CANDIDATES = {
    "callingdigital": [
        "assets/logos/callingdigital.png",
        "assets/logos/callingdigital.webp",
        "assets/logos/callingdigital.jpg",
    ],
    "autointelligence": [
        "assets/logos/autointelligence.png",
        "assets/logos/autointelligence.webp",
        "assets/logos/autointelligence.jpg",
    ],
    "aiphoneguy": [
        "assets/logos/aiphoneguy.png",
        "assets/logos/aiphoneguy.webp",
        "assets/logos/aiphoneguy.jpg",
        "assets/logos/theaiphoneguy.png",
        "assets/logos/theaiphoneguy.webp",
        "assets/logos/theaiphoneguy.jpg",
    ],
}


DIRECTIVE_PATTERN = re.compile(
    r"\[\[\s*(image_headline|image_subhead|image_style|image_palette|image_logo)\s*:\s*(.*?)\s*\]\]",
    re.IGNORECASE,
)


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "post"


def _best_text(piece: Dict[str, Any]) -> str:
    return (
        piece.get("content")
        or piece.get("body")
        or piece.get("title")
        or "Your next move starts today."
    )


def _extract_creative_directives(text: str) -> Dict[str, str]:
    directives: Dict[str, str] = {}
    if not text:
        return directives

    for key, value in DIRECTIVE_PATTERN.findall(text):
        directives[key.lower().strip()] = value.strip()

    return directives


def _strip_creative_directives(text: str) -> str:
    if not text:
        return ""
    cleaned = DIRECTIVE_PATTERN.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _parse_hex_color(value: str, fallback: tuple) -> tuple:
    raw = (value or "").strip().lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return fallback


def _palette_with_override(theme: Dict[str, Any], palette_value: str) -> Dict[str, Any]:
    if not palette_value:
        return theme

    parts = [p.strip() for p in palette_value.split(",") if p.strip()]
    if len(parts) < 3:
        return theme

    return {
        "name": theme["name"],
        "bg": _parse_hex_color(parts[0], theme["bg"]),
        "accent": _parse_hex_color(parts[1], theme["accent"]),
        "text": _parse_hex_color(parts[2], theme["text"]),
    }


def _resolve_logo_path(business_key: str) -> Optional[Path]:
    env_key = f"SOCIAL_LOGO_{business_key.upper()}"
    env_value = (os.getenv(env_key) or "").strip()
    if env_value:
        p = Path(env_value).expanduser()
        if p.exists() and p.is_file():
            return p

    for candidate in LOGO_CANDIDATES.get(business_key, []):
        p = Path(candidate)
        if p.exists() and p.is_file():
            return p
    return None


def _auto_headline(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return "Your next move starts now"
    sentence = re.split(r"[.!?]", cleaned)[0].strip()
    words = sentence.split(" ")
    return " ".join(words[:9]).strip().rstrip(":")


def _creative_director_plan(
    piece: Dict[str, Any],
    business_key: str,
    platform: str,
    extracted_directives: Dict[str, str],
) -> Dict[str, Any]:
    skill = CREATIVE_DIRECTOR_SKILLS.get(business_key, CREATIVE_DIRECTOR_SKILLS["callingdigital"])
    source_text = _best_text(piece)

    auto_directives = {
        "image_style": skill.get("platform_styles", {}).get(platform, skill.get("default_style", "clean")),
        "image_palette": skill.get("default_palette", ""),
        "image_headline": _auto_headline(source_text),
        "image_subhead": skill.get("tagline", ""),
        "image_logo": "on",
    }

    # Manual directives win over Creative Director defaults.
    auto_directives.update(extracted_directives)

    return {
        "directives": auto_directives,
        "skill_profile": skill,
    }


def _load_font(size: int):
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _build_image_bytes(
    text: str,
    business_key: str,
    directives: Optional[Dict[str, str]] = None,
    cta_text: str = "",
) -> bytes:
    from PIL import Image, ImageDraw

    directives = directives or {}
    base_theme = BRAND_THEME.get(business_key, BRAND_THEME["callingdigital"])
    theme = _palette_with_override(base_theme, directives.get("image_palette", ""))
    style_name = directives.get("image_style", "clean").strip().lower()
    style = STYLE_PRESETS.get(style_name, STYLE_PRESETS["clean"])

    width, height = 1200, 675
    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)

    # Accent panel creates visual structure and brand color consistency.
    draw.rounded_rectangle((40, 40, width - 40, 40 + style["accent_height"]), radius=24, fill=theme["accent"])

    brand_font = _load_font(42)
    title_font = _load_font(style["title_size"])
    body_font = _load_font(style["body_size"])

    draw.text((70, 76), theme["name"], font=brand_font, fill=(18, 18, 18))

    headline = directives.get("image_headline", "").strip()
    subhead = directives.get("image_subhead", "").strip()
    working_text = "\n".join(part for part in [headline, subhead, text] if part).strip()

    words = re.sub(r"\s+", " ", working_text).strip().split(" ")
    lines = []
    current = []
    for word in words:
        test_line = " ".join(current + [word]).strip()
        if len(test_line) <= 42:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    lines = lines[:5]
    y = 220
    for index, line in enumerate(lines):
        font = title_font if index == 0 else body_font
        draw.text((70, y), line, font=font, fill=theme["text"])
        y += 78 if index == 0 else 60

    footer = cta_text.strip() or "Built by Paperclip AI Ops"
    draw.rounded_rectangle((70, height - 120, width - 70, height - 70), radius=16, fill=(255, 255, 255))
    draw.text((90, height - 108), footer[:70], font=_load_font(26), fill=(32, 32, 32))

    logo_toggle = (directives.get("image_logo") or "on").strip().lower()
    if logo_toggle not in {"off", "false", "0", "no"}:
        logo_path = _resolve_logo_path(business_key)
        if logo_path:
            try:
                logo = Image.open(logo_path).convert("RGBA")
                max_w, max_h = 200, 80
                ratio = min(max_w / logo.width, max_h / logo.height, 1.0)
                logo = logo.resize((int(logo.width * ratio), int(logo.height * ratio)))
                image_rgba = image.convert("RGBA")
                x = width - logo.width - 70
                y = height - logo.height - 145
                image_rgba.alpha_composite(logo, (x, y))
                image = image_rgba.convert("RGB")
            except Exception as e:
                logging.warning("[SocialPipeline] Logo overlay skipped for %s: %s", business_key, e)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _publish_with_callable(
    publisher: Callable[..., Dict[str, Any]],
    piece: Dict[str, Any],
    profile_id: str,
    publish_now: bool,
) -> Dict[str, Any]:
    return publisher(
        piece=piece,
        profile_id=profile_id,
        publish_now=publish_now,
    )


def prepare_social_piece_with_creative_director(
    piece: Dict[str, Any],
    business_key: str,
) -> Dict[str, Any]:
    """
    Apply Creative Director planning + optional media generation to a social piece.

    This is provider-agnostic and can be used by Zernio, GHL, or other publishers.
    """
    pipeline_piece = dict(piece)
    platform = (pipeline_piece.get("platform") or "").strip().lower()
    has_media = bool(pipeline_piece.get("media_url") or pipeline_piece.get("image_url"))

    original_text = (pipeline_piece.get("content") or pipeline_piece.get("body") or "").strip()
    user_directives = _extract_creative_directives(original_text)
    creative_plan = _creative_director_plan(
        piece=pipeline_piece,
        business_key=business_key,
        platform=platform,
        extracted_directives=user_directives,
    )
    directives = creative_plan["directives"]

    cleaned_text = _strip_creative_directives(original_text)
    if cleaned_text and pipeline_piece.get("content"):
        pipeline_piece["content"] = cleaned_text
    if cleaned_text and pipeline_piece.get("body"):
        pipeline_piece["body"] = cleaned_text

    generated_media = False
    media_url = pipeline_piece.get("media_url") or pipeline_piece.get("image_url")

    if not has_media:
        try:
            text_for_image = _best_text(pipeline_piece)
            image_bytes = _build_image_bytes(
                text_for_image,
                business_key,
                directives=directives,
                cta_text=pipeline_piece.get("cta", ""),
            )
            slug = _slugify(text_for_image)[:48]
            filename = (
                f"{business_key}-{platform or 'social'}-{slug}-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.png"
            )
            media_url = upload_media_to_zernio(
                file_bytes=image_bytes,
                filename=filename,
                mime_type="image/png",
            )
            pipeline_piece["media_url"] = media_url
            generated_media = True
            logging.info("[SocialPipeline] Generated and uploaded media for content_id=%s", piece.get("id"))
        except Exception as e:
            logging.warning(
                "[SocialPipeline] Media generation/upload failed for content_id=%s: %s",
                piece.get("id"),
                e,
            )

    return {
        "piece": pipeline_piece,
        "media_url": media_url,
        "generated_media": generated_media,
        "platform": platform,
        "creative_director": {
            "style": directives.get("image_style", "clean"),
            "headline": directives.get("image_headline", ""),
            "subhead": directives.get("image_subhead", ""),
            "logo_enabled": (directives.get("image_logo") or "on").strip().lower() not in {"off", "false", "0", "no"},
        },
    }


def run_zernio_social_pipeline(
    piece: Dict[str, Any],
    business_key: str,
    profile_id: str,
    publish_now: bool = True,
    publisher: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    F1-style social pipeline: creative -> upload -> publish -> telemetry.

    Returns pipeline metadata + final post response.
    """
    prep = prepare_social_piece_with_creative_director(piece=piece, business_key=business_key)
    pipeline_piece = prep["piece"]

    publish_fn = publisher or publish_content_piece_to_zernio
    post_result = _publish_with_callable(
        publisher=publish_fn,
        piece=pipeline_piece,
        profile_id=profile_id,
        publish_now=publish_now,
    )

    return {
        "post": post_result,
        "media_url": prep.get("media_url"),
        "generated_media": prep.get("generated_media", False),
        "platform": prep.get("platform", ""),
        "creative_director": prep.get("creative_director", {}),
    }
