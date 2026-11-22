"""Run a bounded, real Cloudflare Klein -> local review -> Klein repair trial.

This is an *experiment*, not evidence that an image is rights- or fact-verified.
Maximum FOUR Cloudflare FREE-only model calls across two scenes (2 per scene).
Final outputs always remain review-only; the article and RSS are untouched.
"""
from __future__ import annotations
import io,json,os,pathlib,time
from PIL import Image, ImageOps, ImageDraw
import torch
from src.editorial_providers import klein, ProviderError
from src.editorial_image_feedback import inspect_visual, repair_prompt
from src.editorial_images import similarity

ROOT=pathlib.Path("metro-audit-source")
OUT=pathlib.Path("metro-klein-feedback-results")
OUT.mkdir(exist_ok=True)

SCENES=[
    {
      "slug":"phantom-train", "old_file":"05-tunnel.jpg",
      "subject":"an old stopped passenger train with open doors at an abandoned metro platform",
      "brief":"Inside a deserted underground metro station at night: a complete old passenger subway train has stopped beside the platform, the nearest carriage doors stand wide open, one yellow overhead lamp illuminates the train and tiled platform, empty and unnervingly quiet. Clearly visible train and platform, wide cinematic shot.",
      "must_include":["old subway train","open carriage doors","platform next to train","single yellow overhead lamp"],
      "must_avoid":["empty railway tunnel with no train","railway tracks without carriage","ticket booth","crowds of people"],
      "wrong":"A deserted, empty underground railway tunnel with tracks and no train, no subway platform",
    },
    {
      "slug":"four-clocks", "old_file":"06-control-room.jpg",
      "subject":"four different wall clocks inside an abandoned underground metro control room",
      "brief":"An old underground metro control room at night, exactly FOUR separate clearly visible round wall clocks above a vintage dispatcher console, each clock showing a different time, glowing dim screens and worn control panels, no people. Four clocks are the unmistakable main subject, wide atmospheric cinematic composition.",
      "must_include":["four separate round wall clocks","old metro dispatcher desk and control panels","four distinct clock faces"],
      "must_avoid":["a single wall clock","train tunnel instead of control room","empty corridor","crowds of people"],
      "wrong":"An empty old metro station control room with only ONE large round wall clock above the desk",
    }
]
STYLE="Realistic cinematic mystery illustration, old Soviet underground metro atmosphere, natural lighting, detailed but restrained, horizontal landscape, no words, no logos, no watermark."

def source_image(file):
    found=list(ROOT.rglob(file))
    if len(found)!=1: raise RuntimeError("missing_original_image_"+file)
    return found[0].read_bytes()

def display_image(data):
    with Image.open(io.BytesIO(data)) as image:
        rgb=ImageOps.exif_transpose(image).convert("RGB")
        rgb.thumbnail((1000,780))
    return rgb

def scores(data,scene):
    img=display_image(data)
    target=scene["brief"]; wrong=scene["wrong"]
    # Model scores are relative logits; compare images only within the same prompt set.
    with torch.inference_mode():
        from src.editorial_image_feedback import _siglip
        model,proc=_siglip()
        tokens=proc(text=["This is a photo of "+target, "This is a photo of "+wrong],
                   images=img,padding="max_length",max_length=64,return_tensors="pt")
        vals=model(**tokens).logits_per_image[0].tolist()
    clip_model, clip_vals, _ = similarity(img,[target,wrong,"an unrelated image with no connection with this topic"])
    return {"siglip_target_minus_wrong":round(vals[0]-vals[1],4),
            "siglip_target_logit":round(vals[0],4),
            "clip_target_minus_wrong":round(clip_vals[0]-clip_vals[1],4),
            "clip_target_similarity":clip_vals[0], "clip_model":clip_model}

def compose(rows):
    canvas=Image.new("RGB",(1536,2*610),(245,245,245))
    draw=ImageDraw.Draw(canvas)
    for y,scene in enumerate(SCENES):
        attempts=rows[y].get("attempts",[])
        items=[("ORIGINAL",source_image(scene["old_file"]))]
        for attempt in attempts:
            candidate=OUT/attempt["file"]
            if candidate.exists(): items.append((f"NEW #{attempt['attempt']}",candidate.read_bytes()))
        for col,(label,data) in enumerate(items[:3]):
            im=display_image(data)
            im.thumbnail((490,510))
            x=col*512+12; yy=y*610+10
            canvas.paste(im,(x,yy))
            draw.text((x,yy+518),f"{scene['slug']} / {label}",fill=(0,0,0))
    canvas.save(OUT/"comparison.jpg",quality=88)

def main():
    report={"article":"Станция метро, которой не было на ночной схеме",
            "source_run":45027517283,"models":["openai/clip-vit-base-patch32",
              "google/siglip2-base-patch16-224","florence-community/Florence-2-base",
              "@cf/black-forest-labs/flux-2-klein-4b"],
            "max_cloudflare_calls":4,"cloudflare_calls":0,"published":False,
            "independently_verified_semantic_accuracy":False,"scenes":[]}
    try:
        if os.environ.get("EDITORIAL_CF_FREE_ONLY")!="true":
            raise RuntimeError("free_only_attestation_missing")
        for scene in SCENES:
            item={"slug":scene["slug"],"must_include":scene["must_include"],
                  "must_avoid":scene["must_avoid"],"attempts":[]}
            report["scenes"].append(item)
            original=source_image(scene["old_file"])
            item["original"]=scores(original,scene)
            item["original"]["florence_caption"]=inspect_visual(original,scene)["florence_caption"]
            previous={"florence_caption":item["original"]["florence_caption"]}
            for attempt in (1,2):
                if report["cloudflare_calls"]>=4: break
                prompt=(scene["brief"]+" "+STYLE if attempt==1 else
                        repair_prompt(scene,previous,style=STYLE))
                # Increment before API call to enforce a hard attempt ceiling even on failure.
                report["cloudflare_calls"]+=1
                print("KLEIN_CALL",scene["slug"],attempt,flush=True)
                try:
                    t=time.monotonic()
                    data,params=klein(prompt)
                    file=f"{scene['slug']}-attempt-{attempt}.jpg"
                    display_image(data).save(OUT/file,quality=90)
                    measured=scores(data,scene)
                    visual=inspect_visual(data,scene)
                    measured["florence_caption"]=visual["florence_caption"]
                    item["attempts"].append({"attempt":attempt,"file":file,
                        "seconds":round(time.monotonic()-t,2),"scores":measured,
                        "prompt":prompt,"parameters":params})
                    previous=visual
                    print("KLEIN_RESULT",scene["slug"],attempt,
                          json.dumps({"siglip_difference":measured["siglip_target_minus_wrong"],
                                      "clip_difference":measured["clip_target_minus_wrong"]}),flush=True)
                except (ProviderError,RuntimeError,ValueError,OSError) as exc:
                    item["attempts"].append({"attempt":attempt,"error":type(exc).__name__+":"+str(exc)[:160]})
                    print("KLEIN_BLOCKED",scene["slug"],type(exc).__name__,str(exc)[:130],flush=True)
                    break
            candidates=[x for x in item["attempts"] if "scores" in x]
            if candidates:
                item["best_attempt_by_relative_siglip"]=max(candidates,key=lambda c:c["scores"]["siglip_target_minus_wrong"])["attempt"]
                item["best_siglip_vs_original_delta"]=round(max(x["scores"]["siglip_target_minus_wrong"] for x in candidates)-item["original"]["siglip_target_minus_wrong"],4)
        compose(report["scenes"])
        report["status"]="completed" if all(x.get("attempts") and any("scores" in y for y in x["attempts"]) for x in report["scenes"]) else "partial"
    except Exception as exc:
        report["status"]="blocked"
        report["error"]=type(exc).__name__+":"+str(exc)[:200]
        print("EXPERIMENT_BLOCKED",report["error"],flush=True)
    finally:
        (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
        print("EXPERIMENT_END",json.dumps({"status":report.get("status"),"calls":report["cloudflare_calls"],
         "scenes":[{"slug":s["slug"],"attempts":len(s["attempts"])} for s in report["scenes"]]}),flush=True)
if __name__=="__main__":main()
