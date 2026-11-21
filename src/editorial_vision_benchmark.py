"""24-case pilot benchmark of Qwen image QA; no automatic approvals.

Labels are procedural: positive means same image or only lossless-ish resave;
negative means unrelated scene, large occlusion, hard crop, or altered palette.
They do not validate nuanced spatial reasoning or scientific correctness.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import time

from PIL import Image, ImageEnhance, ImageDraw, ImageFilter

from editorial_cloudflare_vision import review_pair, VisionPending
from editorial_scene_contract import make_contract

SCENES = {
    1: ("A woman in dark green coat stands in an abandoned apartment corridor "
        "beside a doorway which reveals a snowy evergreen forest at night.",
        "woman in green coat", "abandoned apartment corridor with portal to snowy forest"),
    2: ("A woman in dark green coat walks into a snowy evergreen forest at night "
        "near an open wooden doorframe with an apartment corridor behind it.",
        "woman in green coat", "snowy forest with old doorframe"),
    3: ("A woman wearing dark green coat stands inside an old apartment corridor "
        "looking at a closed door with snowy footprints across its wooden floor.",
        "woman in green coat", "abandoned apartment corridor with closed door"),
}


def encoded(image):
    buf = BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def make_variants(original, wrong, scene_id):
    with Image.open(BytesIO(original)) as src:
        base = src.convert("RGB")
    with Image.open(BytesIO(wrong)) as src:
        alternate = src.convert("RGB").resize(base.size)
    # Exactly eight cases per scene. Ground truth for negatives is deliberately
    # strong and observable, not subjective aesthetic quality.
    yield ("identity", True, original)
    yield ("jpeg_resave", True, encoded(base))
    yield ("other_scene", False, wrong)
    black = base.copy()
    ImageDraw.Draw(black).rectangle((0,0,base.width,base.height),fill=(0,0,0))
    yield ("black_frame", False, encoded(black))
    occluded = base.copy()
    ImageDraw.Draw(occluded).rectangle((int(base.width*.10),int(base.height*.08),
                            int(base.width*.88),int(base.height*.93)),fill=(15,15,15))
    yield ("severe_occlusion", False, encoded(occluded))
    merged = base.copy()
    merged.paste(alternate.crop((0,0,base.width,base.height//2)),
                 (0,0,base.width,base.height//2))
    yield ("other_scene_top_half", False, encoded(merged))
    crop = base.crop((int(base.width*.28),int(base.height*.28),
                      int(base.width*.72),int(base.height*.72))).resize(base.size)
    yield ("aggressive_zoom", False, encoded(crop))
    palette = ImageEnhance.Color(base).enhance(0)
    palette = ImageEnhance.Contrast(palette).enhance(2.6)
    yield ("grayscale_high_contrast", False, encoded(palette))


def run(first, others, outdir, max_requests=24):
    originals = {
        1: (first / "cloudflare-flux-schnell.jpg").read_bytes(),
        2: (others / "cloudflare-scene-2.jpg").read_bytes(),
        3: (others / "cloudflare-scene-3.jpg").read_bytes(),
    }
    outdir.mkdir(parents=True,exist_ok=True)
    cases=[]
    for i in (1,2,3):
        other = {1:2,2:3,3:1}[i]
        for test,expect,modified in make_variants(originals[i],originals[other],i):
            cases.append((i,test,expect,originals[i],modified))
    assert len(cases) == 24
    if max_requests < len(cases):
        raise ValueError("This benchmark must be run in full or be marked partial")
    report = []
    for n,(scene,test,expect,source,candidate) in enumerate(cases,1):
        text,person,location=SCENES[scene]
        contract=make_contract(text,subject=person,location=location,
                               action="Keep the same scene and subject",
                               must_keep=["do not replace scene", "keep photographic identity"],
                               fix="Retain the original scene content without changes",
                               forbid=["never substitute another location or subject"])
        record={"index":n,"scene":scene,"case":test,"expected_accept":expect,
                "source_sha256":sha256(source).hexdigest(),
                "candidate_sha256":sha256(candidate).hexdigest()}
        try:
            evidence=review_pair(source,candidate,contract)
            record["model_status"]=evidence["status"]
            record["model_checks"]=evidence["checks"]
            record["defects"]=evidence.get("defects",[])
            record["predicted_accept"]=evidence["status"]=="needs_independent_validation"
            record["correct"]=record["predicted_accept"]==expect
        except VisionPending as exc:
            record["model_status"]="pilot_failed"
            record["reason"]=str(exc)
            record["predicted_accept"]=None
            record["correct"]=None
        report.append(record)
        (outdir/"benchmark.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print("CASE %02d/%02d %-27s label=%s predicted=%s correct=%s" %
              (n,len(cases),test,expect,record["predicted_accept"],record["correct"]),flush=True)
        # Sequential on purpose: protect Workers Free shared quota and avoid bursts.
        time.sleep(0.35)
    completed=[r for r in report if r["correct"] is not None]
    negative=[r for r in completed if not r["expected_accept"]]
    positive=[r for r in completed if r["expected_accept"]]
    summary={"case_count":24,"completed":len(completed),
             "failures":len(report)-len(completed),
             "correct":sum(r["correct"] for r in completed),
             "negative_count":len(negative),
             "false_accepts":sum(r["predicted_accept"] for r in negative),
             "positive_count":len(positive),
             "false_rejects":sum(not r["predicted_accept"] for r in positive),
             "semantic_verified":False,
             "automated_approval_enabled":False,
             "scope":"Procedurally labeled image pairs; NOT scientific diagram factchecking"}
    (outdir/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print("BENCHMARK_SUMMARY="+json.dumps(summary),flush=True)
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--first",type=Path,required=True)
    p.add_argument("--others",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    run(args.first,args.others,args.output)
