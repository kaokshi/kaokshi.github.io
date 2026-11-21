"""Experimental Cloudflare-hosted Qwen visual reviewer.

A model response is evidence to investigate, never permission to publish.
Free-only, fixed daily request cap supplied by the caller, no paid fallback.
"""
import base64
from hashlib import sha256
from io import BytesIO
import json
import os
import re

from PIL import Image

MODEL = "@cf/qwen/qwen3.8-27b"
REQUIRED = ("same_story", "same_character", "same_location", "same_visual_style",
            "targeted_fix_successful", "no_new_defects")

class VisionPending(RuntimeError):
    pass


def parse_review(raw, original, edited):
    if not isinstance(raw, str):
        raise VisionPending("Missing model response")
    try:
        response = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except (ValueError, TypeError):
        raise VisionPending("Vision model did not return valid JSON") from None
    if not isinstance(response, dict):
        raise VisionPending("Vision output must be an object")
    checks = response.get("checks")
    if not isinstance(checks, dict) or any(type(checks.get(k)) is not bool for k in REQUIRED):
        raise VisionPending("Vision output is incomplete")
    defects = response.get("defects")
    if not isinstance(defects, list) or len(defects) > 12 or any(not isinstance(x,str) or len(x)>250 for x in defects):
        raise VisionPending("Vision defects list is invalid")
    # The model is unvalidated; a positive response is NEVER an approval.
    return {"status": "reject_candidate" if not all(checks.values()) else "needs_independent_validation",
            "checks": checks, "defects": defects, "model": MODEL,
            "original_sha256": sha256(original).hexdigest(),
            "edited_sha256": sha256(edited).hexdigest(),
            "semantic_verified": False, "reviewer_kind": "unvalidated_vision"}


def _image_data(image):
    if not isinstance(image, bytes) or len(image)>10_000_000:
        raise VisionPending("Bad vision image input")
    try:
        with Image.open(BytesIO(image)) as img:
            img.load()
            img.thumbnail((768,768))
            from io import BytesIO as Buffer
            stream=Buffer()
            img.convert("RGB").save(stream, format="JPEG",quality=82)
        return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()
    except (ValueError, OSError):
        raise VisionPending("Unreadable vision input") from None


def review_pair(original, edited, scene_contract, *, env=None, post=None):
    from editorial_scene_contract import validate_contract
    validate_contract(scene_contract)
    env = os.environ if env is None else env
    if env.get("ZEN_IMAGE_ALLOW_PAID","false").lower() not in ("false","0","no"):
        raise VisionPending("Paid mode forbidden")
    if env.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED")!="true":
        raise VisionPending("Workers Free confirmation missing")
    if env.get("ZEN_IMAGE_VISION_PILOT")!="true":
        raise VisionPending("Visual model pilot is disabled")
    account, token=env.get("CLOUDFLARE_ACCOUNT_ID",""),env.get("CLOUDFLARE_API_TOKEN","")
    if not re.fullmatch("[a-fA-F0-9]{32}",account) or not token:
        raise VisionPending("Cloudflare credentials missing")
    if post is None:
        import requests
        post=requests.post
    required=", ".join(REQUIRED)
    instruction=("Examine image 1 ORIGINAL and image 2 EDITED independently, then compare. "
                 "Only describe visually verifiable evidence, not inferred story facts. "
                 "Return ONLY JSON with checks object containing EXACT boolean keys: "+required+
                 "; and defects array of concise strings. If uncertain mark the relevant check false. "
                 "Check the story, character, location, geometry, style and targeted edit. "
                 "STORY CONTRACT: "+json.dumps(scene_contract,ensure_ascii=False))
    payload={"messages":[{"role":"system","content":"You are a skeptical visual QA inspector. Never invent observations."},
                         {"role":"user","content":[
                             {"type":"text","text":instruction},
                             {"type":"image_url","image_url":{"url":_image_data(original)}},
                             {"type":"image_url","image_url":{"url":_image_data(edited)}}]}],
             "max_completion_tokens":1800,"reasoning_effort":"low",
             "chat_template_kwargs":{"enable_thinking":False},
             "temperature":0.1,"stream":False}
    endpoint="https://api.cloudflare.com/client/v4/accounts/"+account+"/ai/run/"+MODEL
    try:
        response=post(endpoint,headers={"Authorization":"Bearer "+token},json=payload,timeout=120)
        if response.status_code!=200:
            raise VisionPending("Vision API failed (HTTP "+str(response.status_code)+")")
        if len(response.content)>1_000_000:
            raise VisionPending("Vision response too large")
        body=response.json()
        if body.get("success") is False:
            raise VisionPending("Vision API rejected the request")
        result=body.get("result",body)
        if isinstance(result,dict) and isinstance(result.get("response"),str):
            raw=result["response"]
        elif isinstance(result,dict) and result.get("choices"):
            raw=result["choices"][0]["message"]["content"]
        else:
            raise VisionPending("Unsupported vision response")
        return parse_review(raw,original,edited)
    except VisionPending:
        raise
    except Exception:
        raise VisionPending("Vision model request or response failed") from None
