"""Real CPU technical trial on up to 30 freely discoverable Wikimedia photos.

Labels are derived from SEARCH QUERIES, not independent human verification.
Accordingly, scores are weak-label proxy diagnostics, NOT accuracy/precision claims.
Never saves fetched copyrighted images to public GitHub artifacts.
"""
from __future__ import annotations
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

from PIL import Image, ImageOps
from src.editorial_images import commons_photos, request, COMMONS

SCENES = [
    ("aurora", "a real photograph of the aurora borealis in the night sky", "aurora borealis northern lights"),
    ("coral", "a real underwater photograph of coral reef and reef fish", "coral reef underwater"),
    ("tornado", "a real photograph of a tornado funnel touching ground", "tornado funnel cloud"),
]
OUT = Path("image-benchmark")
OUT.mkdir(exist_ok=True)
TIMES = {}


def images():
    rows, seen = [], set()
    for label, caption, query in SCENES:
        for candidate in commons_photos(query):
            if sum(x["weak_label"] == label for x in rows) >= 10:
                break
            if candidate["asset_url"] in seen:
                continue
            seen.add(candidate["asset_url"])
            try:
                raw = request(candidate["asset_url"], COMMONS, timeout=9, limit=4_000_000)
                with Image.open(io.BytesIO(raw)) as im:
                    if min(im.size) < 150 or im.width*im.height > 15_000_000:
                        continue
                    rgb = ImageOps.exif_transpose(im).convert("RGB")
                    rgb.thumbnail((640, 640))
                rows.append({"image": rgb.copy(), "weak_label": label,
                             "source_page": candidate["source_page"],
                             "license": candidate["license"], "caption": caption})
                print("CANDIDATE_COLLECTED", label, len(rows), flush=True)
            except Exception as exc:
                print("IMAGE_REJECTED", type(exc).__name__, flush=True)
    return rows


def run_model(rows, model_id, sigmoid=False):
    import torch
    from transformers import AutoModel, AutoProcessor
    t=time.monotonic()
    model=AutoModel.from_pretrained(model_id).eval()
    processor=AutoProcessor.from_pretrained(model_id)
    TIMES[model_id + ":load_s"] = round(time.monotonic()-t,2)
    names=[x[0] for x in SCENES]
    prompts=[x[1] for x in SCENES]
    records=[]
    for row in rows:
        t=time.monotonic()
        if sigmoid:
            inp=processor(text=[f"This is a photo of {p}." for p in prompts],
                images=row["image"],padding="max_length",max_length=64,return_tensors="pt")
        else:
            inp=processor(text=prompts,images=row["image"],padding=True,truncation=True,
                max_length=77,return_tensors="pt")
        with torch.inference_mode():
            output=model(**inp)
        logits=output.logits_per_image[0].tolist()
        top=names[max(range(len(logits)),key=lambda i:logits[i])]
        records.append({"source_page":row["source_page"],"weak_label":row["weak_label"],
                        "top_predicted":top,"scores_by_class":dict(zip(names,[round(v,4) for v in logits])),
                        "inference_s":round(time.monotonic()-t,2)})
    del model, processor
    return {"model":model_id,"n":len(rows),"proxy_agreement_count":sum(x["weak_label"]==x["top_predicted"] for x in records),
            "proxy_agreement_fraction":round(sum(x["weak_label"]==x["top_predicted"] for x in records)/len(rows),3),
            "mean_inference_s":round(sum(x["inference_s"] for x in records)/len(rows),2),
            "records":records,"warning":"weak labels are search-query categories and NOT independently validated truth"}


def florence(rows):
    import torch
    from transformers import AutoProcessor, Florence2ForConditionalGeneration
    model_id="florence-community/Florence-2-base"
    t=time.monotonic()
    model=Florence2ForConditionalGeneration.from_pretrained(model_id).eval()
    processor=AutoProcessor.from_pretrained(model_id)
    TIMES[model_id+":load_s"]=round(time.monotonic()-t,2)
    samples=[]
    for row in (next((x for x in rows if x["weak_label"]==category),None) for category in ["aurora","coral","tornado"]):
        if row is None: continue
        t=time.monotonic()
        prompt="<MORE_DETAILED_CAPTION>"
        inputs=processor(text=prompt,images=row["image"],return_tensors="pt")
        with torch.inference_mode():
            output=model.generate(**inputs,max_new_tokens=110,num_beams=2)
        decoded=processor.batch_decode(output,skip_special_tokens=False)[0]
        parsed=processor.post_process_generation(decoded,task=prompt,image_size=row["image"].size)
        samples.append({"weak_label":row["weak_label"],"source_page":row["source_page"],
                        "caption":parsed,"inference_s":round(time.monotonic()-t,2)})
    return {"model":model_id,"samples":samples}


def main():
    start=time.monotonic()
    report={"test":"live Wikimedia discovery and real CPU inference",
            "ground_truth":"NOT verified independently; weak labels from search query",
            "status":"started","expected_images":30,"publication_authorized":False}
    try:
        rows=images()
        report["found_images"]=len(rows)
        report["counts"]={s:sum(x["weak_label"]==s for x in rows) for s,_,_ in SCENES}
        if len(rows)<12: raise RuntimeError("insufficient_images_for_trial")
        results=[]
        for model_id,sigmoid in [("openai/clip-vit-base-patch32",False),
                                  ("google/siglip2-base-patch16-224",True)]:
            try:
                results.append(run_model(rows,model_id,sigmoid=sigmoid))
                print("MODEL_COMPLETED",model_id,flush=True)
            except Exception as exc:
                report.setdefault("errors",[]).append({"model":model_id,"error":type(exc).__name__+":"+str(exc)[:160]})
                print("MODEL_FAILED",model_id,type(exc).__name__,flush=True)
        report["retrieval"]=results
        try:
            report["florence"]=florence(rows)
            print("FLORENCE_COMPLETED",flush=True)
        except Exception as exc:
            report.setdefault("errors",[]).append({"model":"Florence-2-base","error":type(exc).__name__+":"+str(exc)[:160]})
            print("FLORENCE_FAILED",type(exc).__name__,flush=True)
        report["status"]="completed_partial" if report.get("errors") else "completed"
    except Exception as exc:
        report["status"]="blocked"
        report["error"]=type(exc).__name__+":"+str(exc)[:160]
        traceback.print_exc(limit=3)
    finally:
        report["elapsed_s"]=round(time.monotonic()-start,2)
        report["model_initialization_times"]=TIMES
        (OUT/"proxy-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print("BENCHMARK_SUMMARY",json.dumps({k:v for k,v in report.items() if k not in ("retrieval","florence")},ensure_ascii=False),flush=True)
        print("NO HUMAN VERIFIED PRECISION CLAIMS",flush=True)
    return 0 if report["status"].startswith("completed") else 2


if __name__ == "__main__":
    sys.exit(main())
