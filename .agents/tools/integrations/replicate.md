# Replicate — AI Image & Video Generation

## Overview

Replicate provides API access to open-source AI models for image generation (FLUX), video generation (Kling, MiniMax), and image editing. Single API key covers all models.

**Primary use**: Generate branded social media images, short-form video clips, and carousel slides.

## Authentication

- **API Token**: `REPLICATE_API_TOKEN` environment variable
- **Get token**: Sign up at replicate.com → Account Settings → API Tokens
- **Base URL**: `https://api.replicate.com/v1`

## Models Used

| Model | Use Case | Cost | Speed |
|-------|----------|------|-------|
| `black-forest-labs/flux-schnell` | Fast image gen (social posts) | ~$0.003/image | ~2s |
| `black-forest-labs/flux-1.1-pro` | High-quality image gen | ~$0.04/image | ~10s |
| `black-forest-labs/flux-fill-pro` | Image editing/inpainting | ~$0.04/image | ~10s |
| `minimax/video-01` | Fast video gen (5s clips) | ~$0.03/video | ~30s |
| `kwaivgi/kling-v2.0-master` | High-quality video (5-10s) | ~$0.05/video | ~60s |

## Paperclip Integration

### Tool Modules

- `tools/image_gen.py` — Text-to-image, image editing, prompt building
- `tools/video_gen.py` — Text-to-video, image-to-video
- `tools/carousel_builder.py` — Multi-slide carousel generation

### Pipeline Integration

The social pipeline (`services/social_pipeline.py`) automatically uses AI generation when `REPLICATE_API_TOKEN` is set. Priority order:

1. **Carousel** — if `carousel_slides` directive or field is present
2. **AI Image** — FLUX generation via Replicate
3. **PIL Image** — branded template fallback (always available)
4. **Video** — if `video_prompt` directive is present (generated after image)

### Creative Directives

Agents embed directives in content text to control media generation:

```
[[image_prompt: A modern office with AI dashboards on screens]]
[[video_prompt: Gentle zoom into the dashboard, data animations]]
[[carousel_slides: [{"headline": "Step 1", "body": "..."}, {"headline": "Step 2", "body": "..."}]]]
[[image_style: bold]]
[[image_palette: #0D2016,#39D38C,#F0FFF8]]
```

### Key Functions

```python
# Image generation
from tools.image_gen import generate_image, generate_image_bytes, build_image_prompt, edit_image

# Video generation
from tools.video_gen import generate_video, generate_video_from_image

# Carousel
from tools.carousel_builder import build_carousel, build_and_upload_carousel
```

## Rate Limits

- **Concurrent predictions**: 10 (default, can request increase)
- **No hard rate limit** on API calls — limited by concurrent prediction slots
- **Prediction timeout**: Images ~60s, Videos ~5min

## Common Operations

### Generate a social media image
```python
from tools.image_gen import generate_image_bytes
image_bytes = generate_image_bytes(
    prompt="Professional team meeting in modern office",
    business_key="callingdigital",
    platform="instagram",
)
```

### Generate a video from an image
```python
from tools.video_gen import generate_video_from_image
result = generate_video_from_image(
    image_url="https://cdn.example.com/image.png",
    prompt="Subtle zoom in with light particle effects",
    business_key="autointelligence",
    platform="tiktok",
)
video_url = result["url"]
```

### Build and upload a carousel
```python
from tools.carousel_builder import build_and_upload_carousel
urls = build_and_upload_carousel(
    slides=[
        {"headline": "Why AI?", "body": "Automate the mundane."},
        {"headline": "Save Time", "body": "2 hours/day back."},
        {"headline": "Get Started", "body": "Book a demo today."},
    ],
    business_key="aiphoneguy",
    platform="instagram",
    cta="Book Your Demo →",
)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `REPLICATE_API_TOKEN` | Yes | API token from replicate.com |

Set in Railway dashboard under your service's environment variables.
