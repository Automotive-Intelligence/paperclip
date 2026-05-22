# Generation layer

Best-of-breed image and video generation, sitting **upstream of Zernio**.
Zernio's native image output is weak. We generate on-brand media here with
strong models, then hand the finished URL to Zernio for distribution only
(post / schedule / analytics / replies).

A generator never posts anything. It returns media URLs. Distribution and
the human approval gate are downstream.

## How to swap models

Models are chosen by environment variable, not a code edit.

| Layer | Env var | Default | Registered providers |
|-------|---------|---------|----------------------|
| Image | `IMAGE_GEN_PROVIDER` | `nano_banana` | `nano_banana`, `flux` |
| Video | `VIDEO_GEN_PROVIDER` | `kling` | `kling`, `seedance_pro`, `veo`* |

\* `veo` is a placeholder slot — selecting it returns a clean "not wired"
error until a Veo runner is added.

To swap: set the env var in Railway and redeploy. No code change.

```
IMAGE_GEN_PROVIDER=flux         # use Replicate FLUX instead of Nano Banana
VIDEO_GEN_PROVIDER=seedance_pro # use fal.ai Seedance Pro instead of Kling
```

## How it works

`base.py` defines the abstract `Generator`: one `generate()` signature, a
provider registry, and env-var provider selection. `image_gen.py` and
`video_gen.py` are concrete generators. Each one's `_registry()` maps a
provider name to a runner callable.

Runners **delegate** to the repo's existing, proven integrations
(`tools/image_gen.py`, `tools/video_gen.py`, `tools/kie_ai.py`,
`tools/fal_ai.py`) rather than reimplementing API calls. The generation
layer is the swappable, uniform interface on top of them.

```python
from services.generators import get_image_generator, get_video_generator

img = get_image_generator()                 # provider from IMAGE_GEN_PROVIDER
res = img.generate("hardcover journal on linen, soft light", platform="instagram")
if res.ok:
    print(res.urls)

vid = get_video_generator()                 # provider from VIDEO_GEN_PROVIDER
res = vid.generate(
    "slow push-in on the journal",
    platform="instagram_reel",
    source_image_url="https://.../journal.png",   # image-to-video off our asset
)
```

`source_image_url` is how we generate off our own assets: the first frame
for image-to-video, and (provider permitting) an edit base for images.

## How to add a new provider

1. Write a runner: a callable taking `prompt, business_key, platform,
   aspect_ratio, source_image_url, **kwargs` and returning a
   `GenerationResult`. Delegate to a `tools/` integration where possible.
2. Add it to the concrete generator's `_registry()` dict under a provider
   name.
3. Add a credentials check for it in that generator's `ready()`.
4. Document the new provider name in the table above.

Switching among registered providers is env-only. Adding a new one is
these four steps — isolated to one file, no caller changes.
