"""Cloudflare FLUX.2 Klein 4B reference-image refinement with real 16:9 output.

This is opt-in until free quota, visual fidelity and licensing pass review.
No optional OpenCV or Pillow atmosphere effects are used.
"""
import base64
import binascii
from io import BytesIO
import os
import re

from PIL import Image
from editorial_cloudflare import CloudflarePending
from editorial_visual_style import expected_size


MODEL = "@cf/black-forest-labs/flux-2-klein-4b"


def refine_image(original, prompt, *, env=None, post=None, style_reference=None):
    env = os.environ if env is None else env
    if env.get("ZEN_IMAGE_ALLOW_PAID", "false").lower() not in {"false", "0", "no"}:
        raise CloudflarePending("Paid mode is forbidden")
    if env.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED") != "true":
        raise CloudflarePending("Workers Free plan confirmation is required")
    account, token = env.get("CLOUDFLARE_ACCOUNT_ID", ""), env.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account) or not token:
        raise CloudflarePending("Missing Cloudflare credentials")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 3500:
        raise ValueError("Invalid final image edit prompt")
    if not isinstance(original, bytes) or len(original) > 15_000_000:
        raise ValueError("Invalid original image")
    try:
        with Image.open(BytesIO(original)) as img:
            if img.width * img.height > 16_000_000:
                raise ValueError("Oversized reference image")
            img.load()
            img = img.convert("RGB")
            img.thumbnail((480, 480))
            ref = BytesIO()
            img.save(ref, "PNG")
    except (ValueError, OSError) as exc:
        raise CloudflarePending("Reference image is not readable") from None
    files = {"input_image_0": ("reference.png", ref.getvalue(), "image/png")}
    if style_reference is not None:
        if not isinstance(style_reference, bytes) or len(style_reference) > 15_000_000:
            raise ValueError("Invalid optional style reference")
        try:
            with Image.open(BytesIO(style_reference)) as style:
                if style.width * style.height > 16_000_000:
                    raise ValueError("Oversized style reference")
                style.load()
                style = style.convert("RGB")
                style.thumbnail((480, 480))
                out = BytesIO()
                style.save(out, "PNG")
            files["input_image_1"] = ("style.png", out.getvalue(), "image/png")
        except (ValueError, OSError):
            raise CloudflarePending("Style reference image unreadable") from None
    if post is None:
        from editorial_cloud_budget import bounded_post
        post = bounded_post(env, 32_000_000)
    width, height = expected_size()
    url = "https://api.cloudflare.com/client/v4/accounts/" + account + "/ai/run/" + MODEL
    try:
        response = post(url, headers={"Authorization": "Bearer " + token},
                        files=files,
                        data={"prompt": prompt, "width": str(width), "height": str(height)},
                        timeout=150)
        if response.status_code != 200:
            raise CloudflarePending("Klein image refinement failed (HTTP " + str(response.status_code) + ")")
        if len(response.content) > 32_000_000:
            raise CloudflarePending("Klein response too large")
        payload = response.json()
        if payload.get("success") is not True:
            raise CloudflarePending("Klein image refinement rejected")
        data = base64.b64decode(payload["result"]["image"], validate=True)
        with Image.open(BytesIO(data)) as final:
            if final.size != (width, height):
                raise CloudflarePending("Klein returned the wrong image dimensions")
            final.load()
    except CloudflarePending:
        raise
    except (ValueError, KeyError, TypeError, OSError, binascii.Error):
        raise CloudflarePending("Malformed Klein image response") from None
    except Exception as exc:
        # requests errors include non-HTTP transport issues; never expose headers/tokens.
        raise CloudflarePending("Klein image service unavailable") from None
    return data, {"model": MODEL, "final_width": width, "final_height": height,
                  "visual_style": "zen-legends-visual-profile-v1",
                  "reference_image": True, "style_reference_used": style_reference is not None}
