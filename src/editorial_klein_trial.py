"""One-shot FLUX.2 Klein image edit trial followed by restrained local colour grading.

This is opt-in and not part of the production editorial pipeline yet.
No API tokens or image bytes are printed to stdout.
"""
import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import requests

MODEL = "@cf/black-forest-labs/flux-2-klein-4b"

def klein_edit(source: Path, dest: Path, instruction=None):
    if os.environ.get("ZEN_IMAGE_ALLOW_PAID", "false").lower() not in ("false", "0", "no"):
        raise RuntimeError("Paid usage prohibited")
    if os.environ.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED") != "true":
        raise RuntimeError("Workers Free plan confirmation is required")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
        raise RuntimeError("Missing Cloudflare secrets")
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((480, 480))
        buf = BytesIO()
        image.save(buf, "PNG")
    prompt = (
        "Edit the reference image 0 into a believable cinematic photograph, keeping the SAME "
        "dark-haired woman in a dark forest-green coat, the same snow-covered night forest, "
        "the standing doorframe and its existing composition. IMPORTANT: REMOVE the wooden "
        "bench or chair that blocks the bottom of the doorway; leave a clearly OPEN, passable "
        "entrance with uninterrupted snowy ground behind the woman. Correct the doorframe and "
        "floor geometry, remove implausible objects and AI artifacts. Increase photorealism with "
        "natural textured snow, detailed bark, physically plausible moonlight, subtle haze, "
        "muted deep blue shadows and gentle realistic amber flashlight light. Preserve anatomy "
        "and realistic human proportions. Film still realism rather than digital art; "
        "no text, no lettering, no logos, no excessive sharpening."
    )
    if instruction is not None:
        if not isinstance(instruction, str) or not 10 <= len(instruction) <= 3500:
            raise ValueError("Invalid editing instruction")
        prompt = instruction
    url = ("https://api.cloudflare.com/client/v4/accounts/" + account
           + "/ai/run/" + MODEL)
    response = requests.post(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        files={"input_image_0": ("reference.png", buf.getvalue(), "image/png")},
        data={"prompt": prompt, "width": "1024", "height": "1024"},
        timeout=150,
    )
    if response.status_code != 200:
        raise RuntimeError("Klein API returned HTTP " + str(response.status_code))
    if len(response.content) > 32_000_000:
        raise RuntimeError("Image response too large")
    try:
        result = response.json()
        if result.get("success") is not True:
            raise RuntimeError("Klein API request failed")
        data = base64.b64decode(result["result"]["image"], validate=True)
        with Image.open(BytesIO(data)) as im:
            im.load()
            im.convert("RGB").save(dest, quality=96)
    except (KeyError, ValueError, TypeError) as exc:
        raise RuntimeError("Klein response was malformed") from None
    print("KLEIN_EDIT_SUCCESS=true")
    print("KLEIN_IMAGE_PATH=" + str(dest))
    return dest

def atmosphere_polish(source: Path, dest: Path):
    """Subtle filmic grade, localized contrast, restrained bloom and grain."""
    with Image.open(source) as pil:
        rgb = np.asarray(pil.convert("RGB"), dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.18, tileGridSize=(8, 8))
    balanced = cv2.addWeighted(l, 0.70, clahe.apply(l), 0.30, 0)
    arr = cv2.cvtColor(cv2.merge((balanced, a, b)), cv2.COLOR_LAB2RGB).astype(np.float32)
    height, width = arr.shape[:2]
    luminance = (0.2126*arr[:,:,0] + 0.7152*arr[:,:,1] + 0.0722*arr[:,:,2]) / 255
    shadow = np.clip((0.53 - luminance) / 0.53, 0, 1)[..., None]
    arr += shadow * np.array([-1.1, 0.4, 2.6],dtype=np.float32)
    highlights = np.clip((luminance - 0.68) / 0.32, 0, 1)[..., None]
    glowbase = cv2.GaussianBlur(arr, (0,0), sigmaX=9)
    arr = arr*(1 - highlights*0.075) + glowbase*highlights*0.075
    yy,xx=np.mgrid[0:height,0:width]
    radius=((xx-width/2)/(width*.78))**2+((yy-height/2)/(height*.78))**2
    vignette=(1-0.065*np.clip(radius,0,1))[...,None]
    arr*=vignette
    rng=np.random.default_rng(20261121)
    arr+=rng.normal(0,0.65,(height,width,1))
    Image.fromarray(np.clip(arr,0,255).astype(np.uint8),"RGB").save(dest,quality=95)
    print("ATMOSPHERE_POLISH_SUCCESS=true")
    print("POLISH_IMAGE_PATH=" + str(dest))
    return dest

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args=ap.parse_args()
    args.output_dir.mkdir(exist_ok=True, parents=True)
    edited=klein_edit(args.input,args.output_dir/"02-klein.jpg")
    atmosphere_polish(edited,args.output_dir/"03-klein-polished.jpg")
    with Image.open(args.input) as im:
        im.convert("RGB").save(args.output_dir/"01-original.jpg",quality=95)
