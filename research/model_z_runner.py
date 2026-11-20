#!/usr/bin/env python3
"""Isolated optional Model Z research run. Requires authorized provider API keys.
QuillBot has no documented official detector API: results must be manually imported.
Never fabricate unavailable results or change production Model A.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_ai_check import extract_text

ROOT = Path(__file__).resolve().parents[1]
SLUGS = (
    "legends-01-prohodnaya-17",
    "legends-02-poslednyaya-elektrichka",
    "legends-03-lift-dvoynoy-zvonok",
    "legends-04-okno-na-cherdake",
    "legends-05-dvor-na-fotografii",
    "science-01-krasnye-spraity",
    "science-02-svetyascheesya-more",
    "science-03-tardigrady",
    "science-04-temnaya-materiya",
    "science-05-polyarnoe-siyanie",
)

def post_json(url, headers, payload):
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={**headers, "Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=90) as response:
        return json.load(response)

def check_gptzero(text):
    key = os.environ.get("GPTZERO_API_KEY")
    if not key:
        return {"status":"not_run","reason":"api_key_missing"}
    try:
        result = post_json("https://api.gptzero.me/v2/predict/text",
                           {"x-api-key":key}, {"document":text})
        docs = result.get("documents", [])
        d = docs[0] if isinstance(docs, list) and docs else {}
        classification = d.get("predicted_class")
        probs = d.get("class_probabilities")
        if classification not in ("human","ai","mixed") or not isinstance(probs,dict):
            return {"status":"error","reason":"unexpected_provider_schema"}
        return {"status":"completed","predicted_class":classification,
                "class_probabilities":{k:probs.get(k) for k in ("human","ai","mixed")},
                "model_version":result.get("version")}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return {"status":"error","reason":type(exc).__name__,
                "http_status":getattr(exc,"code",None)}

def check_winston(text):
    key = os.environ.get("WINSTON_API_KEY")
    if not key:
        return {"status":"not_run","reason":"api_key_missing"}
    try:
        result = post_json("https://api.winstonai.ai/v1/detect",
                           {"Authorization":"Bearer "+key}, {"text":text})
        score = result.get("human_score")
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=100:
            return {"status":"error","reason":"unexpected_provider_schema"}
        return {"status":"completed","human_score":score,
                "verdict":result.get("verdict"),"credits_used":result.get("credits_used")}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return {"status":"error","reason":type(exc).__name__,
                "http_status":getattr(exc,"code",None)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",type=Path,default=Path("model-z-research.json"))
    p.add_argument("--manual-quillbot",type=Path)
    args=p.parse_args()
    manual={}
    if args.manual_quillbot:
        manual=json.loads(args.manual_quillbot.read_text(encoding="utf-8"))
    rows=[]
    for slug in SLUGS:
        article=json.loads((ROOT/"content/articles"/(slug+".json")).read_text(encoding="utf-8"))
        text=extract_text(article["body_html"])
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest()
        quillbot=manual.get(slug,{"status":"not_run","reason":"no_documented_official_api_manual_web_check_required"})
        if quillbot.get("status")=="completed":
            if (quillbot.get("text_sha256")!=digest or
                isinstance(quillbot.get("ai_generated_pct"),bool) or
                not isinstance(quillbot.get("ai_generated_pct"),(int,float)) or
                not 0<=quillbot["ai_generated_pct"]<=100 or
                not quillbot.get("evidence_ref")):
                quillbot={"status":"invalid","reason":"missing_evidence_or_sha_mismatch"}
        g=check_gptzero(text)
        w=check_winston(text)
        complete=all(x["status"]=="completed" for x in (g,w,quillbot))
        rows.append({"slug":slug,"text_sha256":digest,
                     "gptzero":g,"winston":w,"quillbot":quillbot,
                     "model_z":{"status":"complete_unvalidated_three_provider_panel" if complete else "incomplete",
                                "interpretation":"separate heterogeneous outputs; no validated mean or threshold"}})
        print(slug, "GPTZero:",g["status"],"Winston:",w["status"],
              "QuillBot:",quillbot["status"],"Model Z:",rows[-1]["model_z"]["status"])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps({"schema":"zen-model-z-research-v1","providers":["GPTZero","Winston AI","QuillBot"],
                  "articles":rows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
