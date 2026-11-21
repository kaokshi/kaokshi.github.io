"""Scientific-illustration label audit via Cloudflare Vision; fact rules stay outside VLM.

The Qwen model transcribes visible symbols/labels only. A deterministic code path
compares observations with an expert-sourced, versioned science contract. This
still cannot certify that the image is scientifically correct in all respects.
"""
from hashlib import sha256
import json
import os
import re
from editorial_cloudflare_vision import _image_data, VisionPending, MODEL


CONTRACTS = {
    "solar_inner_planets": {
        "claim":"The four inner planets in increasing distance from the Sun.",
        "expected":["Mercury","Venus","Earth","Mars"],
        "source":"https://science.nasa.gov/solar-system/planets/",
    },
    "dna_complementary_base_pairs": {
        "claim":"Complementary DNA base pairs in the four displayed rungs, top to bottom.",
        "expected":["A-T","G-C","C-G","T-A"],
        "source":"https://www.genome.gov/about-genomics/fact-sheets/Deoxyribonucleic-Acid-Fact-Sheet",
    },
}


def score_transcription(raw, image_data, contract_key):
    if contract_key not in CONTRACTS:
        raise ValueError("Unknown scientific evidence contract")
    try:
        value=json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except (ValueError,TypeError):
        raise VisionPending("Invalid scientific inspection JSON") from None
    if not isinstance(value,dict) or not isinstance(value.get("observed_labels"),list):
        raise VisionPending("Missing observed scientific labels")
    labels=value["observed_labels"]
    if any(not isinstance(x,str) or len(x)>64 for x in labels):
        raise VisionPending("Invalid scientific label")
    certainty=value.get("all_labels_legible")
    if type(certainty) is not bool:
        raise VisionPending("Missing visibility check")
    expected=CONTRACTS[contract_key]["expected"]
    # The SUN is a visible diagram anchor, NOT one of the four planet labels.
    # Preserve the exact observed transcript for audit; normalize only this
    # explicitly allowed context label when it appears before the planets.
    compared=list(labels)
    if contract_key=="solar_inner_planets" and compared and compared[0].strip().upper() in ("SUN","THE SUN"):
        compared=compared[1:]
    matched=certainty and compared==expected
    return {"status":"needs_independent_validation" if matched else "reject_candidate",
            "observed_labels":labels,"compared_labels":compared,
            "expected_labels":expected,"all_labels_legible":certainty,
            "source":CONTRACTS[contract_key]["source"],"model":MODEL,
            "image_sha256":sha256(image_data).hexdigest(),"semantic_verified":False,
            "reason":"matching_labels_only" if matched else "label_mismatch_or_unreadable"}


def review_science_diagram(image_data,contract_key,*,env=None,post=None):
    if contract_key not in CONTRACTS:
        raise ValueError("Unknown scientific evidence contract")
    env=os.environ if env is None else env
    if env.get("ZEN_IMAGE_ALLOW_PAID","false").lower() not in ("false","0","no"):
        raise VisionPending("Paid mode forbidden")
    if env.get("ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED")!="true" or env.get("ZEN_IMAGE_VISION_PILOT")!="true":
        raise VisionPending("Science vision pilot disabled")
    account,token=env.get("CLOUDFLARE_ACCOUNT_ID",""),env.get("CLOUDFLARE_API_TOKEN","")
    if not re.fullmatch("[a-fA-F0-9]{32}",account) or not token:
        raise VisionPending("Missing Cloudflare credentials")
    if post is None:
        import requests
        post=requests.post
    title=CONTRACTS[contract_key]["claim"]
    instruction=(
        "Read the actual visible text printed on this educational diagram. "
        "Your task is transcription, NOT scientific correction or guessing. "
        "Do not change labels to be scientifically correct. "
        "Return ONLY JSON: {\"observed_labels\":[strings],\"all_labels_legible\":true/false}. "
        "For a solar system row, list planet name labels left to right starting nearest SUN. "
        "For DNA, list each base pair shown top to bottom as UPPERCASE X-Y, preserving the side order. "
        "If a label is missing/unclear, put '?' for it and all_labels_legible=false. "
        "The task is: "+title)
    payload={"messages":[{"role":"system","content":"Careful visual transcription. Never fix an observed incorrect label."},
                        {"role":"user","content":[{"type":"text","text":instruction},
                            {"type":"image_url","image_url":{"url":_image_data(image_data)}}]}],
             "max_completion_tokens":1000,"reasoning_effort":"low",
             "chat_template_kwargs":{"enable_thinking":False},
             "temperature":0.1,"stream":False}
    url="https://api.cloudflare.com/client/v4/accounts/"+account+"/ai/run/"+MODEL
    try:
        response=post(url,headers={"Authorization":"Bearer "+token},json=payload,timeout=120)
        if response.status_code!=200:
            raise VisionPending("Scientific QA HTTP "+str(response.status_code))
        if len(response.content)>1_000_000:
            raise VisionPending("Scientific QA response too large")
        body=response.json()
        if body.get("success") is False:
            raise VisionPending("Scientific QA rejected by provider")
        result=body.get("result",body)
        if isinstance(result,dict) and isinstance(result.get("response"),str):
            raw=result["response"]
        elif isinstance(result,dict) and result.get("choices"):
            raw=result["choices"][0]["message"]["content"]
        else:
            raise VisionPending("Scientific QA response format unsupported")
        return score_transcription(raw,image_data,contract_key)
    except VisionPending:
        raise
    except Exception:
        raise VisionPending("Scientific QA model unavailable") from None
