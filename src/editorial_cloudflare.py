"""Cloudflare Workers AI image generation, gated for explicitly verified Free accounts.

No payment or plan modification is attempted. A paid Cloudflare account MUST NOT
be enabled: the account owner must verify Workers Free before running this adapter.
"""
import base64
import binascii
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = "@cf/black-forest-labs/flux-1-schnell"
MAX_RESPONSE = 32_000_000


class CloudflarePending(RuntimeError):
    """Generation is unavailable and must not silently switch to paid services."""


def generate_scene(prompt, *, env=None, opener=urlopen):
    env = os.environ if env is None else env
    if env.get("ZEN_IMAGE_ALLOW_PAID", "false").lower() not in {"false", "0", "no"}:
        raise CloudflarePending("Paid image mode is forbidden")
    if env.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED") != "true":
        raise CloudflarePending("Verify the Cloudflare Workers Free plan before generating")
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = env.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account) or not token:
        raise CloudflarePending("Cloudflare Account ID or API token is missing")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2048:
        raise ValueError("Cloudflare image prompt must be between 1 and 2048 characters")
    url = "https://api.cloudflare.com/client/v4/accounts/" + account + "/ai/run/" + MODEL
    payload = json.dumps({"prompt": prompt, "steps": 4}).encode("utf-8")
    req = Request(url, data=payload, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, method="POST")
    try:
        with opener(req, timeout=120) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        # Never persist provider response bodies or URLs containing secrets.
        raise CloudflarePending("Cloudflare image request failed (HTTP " + str(exc.code) + ")") from None
    except (URLError, TimeoutError, OSError):
        raise CloudflarePending("Cloudflare image service is unreachable") from None
    if len(raw) > MAX_RESPONSE:
        raise CloudflarePending("Cloudflare image response too large")
    try:
        response = json.loads(raw)
        if response.get("success") is not True:
            raise CloudflarePending("Cloudflare rejected the image request")
        image = response["result"]["image"]
        data = base64.b64decode(image, validate=True)
        if len(data) < 100:
            raise ValueError("Image data is empty")
        return data, {"source": "Cloudflare Workers AI", "artist": "ИИ-иллюстрация",
                      "model": MODEL, "prompt": prompt, "steps": 4}
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise CloudflarePending("Invalid Cloudflare image response") from None
