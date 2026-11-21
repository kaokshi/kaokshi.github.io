"""Single-image documentary photo/claim audit with strict fail-closed parsing.

Only tests broad visual categories; metadata (date, place, authenticity) is
NOT inferable from pixels and must be independently verified.
"""
import json
import os
import re
from hashlib import sha256
from editorial_cloudflare_vision import _image_data, VisionPending, MODEL


def review_photo_claim(image_data, expected, *, env=None, post=None):
    if not isinstance(expected,dict) or not isinstance(expected.get("claim"),str) or not 5<=len(expected["claim"])<=500:
        raise ValueError("Missing claim")
    env=os.environ if env is None else env
    if env.get("ZEN_IMAGE_VISION_PILOT")!="true" or env.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED")!="true":
        raise VisionPending("Free vision pilot disabled")
    if env.get("ZEN_IMAGE_ALLOW_PAID","false").lower() not in ("false","0","no"):
        raise VisionPending("Paid vision mode forbidden")
    account,token=env.get("CLOUDFLARE_ACCOUNT_ID",""),env.get("CLOUDFLARE_API_TOKEN","")
    if not re.fullmatch("[0-9a-fA-F]{32}",account) or not token:
        raise VisionPending("Missing Cloudflare credentials")
    if post is None:
        from editorial_cloud_budget import bounded_post
        post = bounded_post(env, 1_000_000)
    claim=expected["claim"]
    prompt=("Assess ONLY the clearly visible objects in the attached real photograph. "
            "Does this specific claim correctly describe the visually identifiable MAIN SUBJECT? "
            "Do not infer source, location, time, image authenticity, or camera metadata. "
            "Do not agree with false claims just because they sound plausible. "
            'Return ONLY JSON: {"claim_matches_visible_subject":boolean,'
            '"observable_evidence":"short visible observation",'
            '"uncertain":boolean}. Mark uncertain=true if you cannot tell. '
            "CLAIM: "+claim)
    payload={"messages":[{"role":"system","content":"You are a strict visual verifier. Report only visible evidence."},
                         {"role":"user","content":[{"type":"text","text":prompt},
                           {"type":"image_url","image_url":{"url":_image_data(image_data)}}]}],
             "max_completion_tokens":800,"reasoning_effort":"low",
             "chat_template_kwargs":{"enable_thinking":False},"temperature":0.1,"stream":False}
    url="https://api.cloudflare.com/client/v4/accounts/"+account+"/ai/run/"+MODEL
    try:
        response=post(url,headers={"Authorization":"Bearer "+token},json=payload,timeout=120)
        if response.status_code!=200:
            raise VisionPending("Vision HTTP "+str(response.status_code))
        if len(response.content)>1_000_000:
            raise VisionPending("Vision response too large")
        data=response.json()
        if data.get("success") is False:
            raise VisionPending("Vision request rejected")
        result=data.get("result",data)
        if isinstance(result,dict) and isinstance(result.get("response"),str):
            raw=result["response"]
        elif isinstance(result,dict) and result.get("choices"):
            raw=result["choices"][0]["message"]["content"]
        else:
            raise VisionPending("Vision response unsupported")
        try:
            obj=json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        except (ValueError,TypeError):
            raise VisionPending("Vision response not JSON") from None
        if (not isinstance(obj,dict) or type(obj.get("claim_matches_visible_subject")) is not bool
                or type(obj.get("uncertain")) is not bool or not isinstance(obj.get("observable_evidence"),str)
                or set(obj) != {"claim_matches_visible_subject", "uncertain", "observable_evidence"}
                or not obj["observable_evidence"].strip() or len(obj["observable_evidence"])>500):
            raise VisionPending("Missing visual verdict fields")
        accepted=obj["claim_matches_visible_subject"] and not obj["uncertain"]
        return {"status":"needs_independent_validation" if accepted else "reject_candidate",
                "predicted_match":accepted,
                "claim_matches_visible_subject":obj["claim_matches_visible_subject"],
                "uncertain":obj["uncertain"],"observable_evidence":obj["observable_evidence"],
                "sha256":sha256(image_data).hexdigest(),
                "semantic_verified":False,"model":MODEL}
    except VisionPending:
        raise
    except Exception:
        raise VisionPending("Vision photo claim audit unavailable") from None
