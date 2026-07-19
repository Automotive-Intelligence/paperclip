from unittest import mock

from services import blog_image


def test_success_dict_normalizes_to_ok():
    fake = {"urls": ["https://fal.media/x.png"], "model": "flash", "aspect_ratio": "16:9",
            "prompt_used": "..."}
    with mock.patch.object(blog_image, "generate_nano_banana_image", return_value=fake):
        out = blog_image.blog_image(prompt="a cinematic diagram", business_key="autointelligence",
                                    aspect_ratio="16:9")
    assert out["ok"] is True
    assert out["urls"] == ["https://fal.media/x.png"]
    assert out["model"] == "flash"
    assert out["aspect_ratio"] == "16:9"


def test_error_string_normalizes_to_not_ok():
    with mock.patch.object(blog_image, "generate_nano_banana_image",
                           return_value="FAL_KEY not configured. Set it in Doppler (paperclip/prd)."):
        out = blog_image.blog_image(prompt="x", business_key="autointelligence")
    assert out["ok"] is False
    assert "FAL_KEY" in out["error"]
    assert out.get("urls", []) == []


def test_empty_prompt_is_rejected_before_calling_fal():
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return {"urls": ["x"]}

    with mock.patch.object(blog_image, "generate_nano_banana_image", side_effect=_spy):
        out = blog_image.blog_image(prompt="   ", business_key="autointelligence")
    assert out["ok"] is False
    assert "prompt" in out["error"].lower()
    assert called["n"] == 0  # never calls fal on empty prompt


def test_passthrough_of_optional_args():
    seen = {}

    def _capture(prompt, **kwargs):
        seen.update(kwargs)
        seen["prompt"] = prompt
        return {"urls": ["y"]}

    with mock.patch.object(blog_image, "generate_nano_banana_image", side_effect=_capture):
        blog_image.blog_image(prompt="hero", business_key="autointelligence", aspect_ratio="1:1",
                              pro=True, reference_image_urls=["https://ref/1.png"])
    assert seen["business_key"] == "autointelligence"
    assert seen["aspect_ratio"] == "1:1"
    assert seen["pro"] is True
    assert seen["reference_image_urls"] == ["https://ref/1.png"]
