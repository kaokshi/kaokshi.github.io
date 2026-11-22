"""Real seven-image metro article audit. No article generation and no publication.
Original material: run 45027517283 / artifact zen-mystery-owner-readable.
Images have source labels but accuracy of text-image relation is an editorial judgement.
"""
import io,json,os,time,statistics
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont

ROOT=Path("metro-audit-source")
OUT=Path("metro-audit-results")
OUT.mkdir(parents=True,exist_ok=True)
SCENES=[
  {"heading":"Ночной дозор","description":"A metro dispatcher at the control panel at midnight, a digital clock showing 00:13 and subway route display"},
  {"heading":"Призрачная платформа","description":"An abandoned underground subway platform with a stopped old empty passenger train, open doors and one yellow overhead lamp"},
  {"heading":"Ход в никуда","description":"A locked and sealed old underground metro entrance with heavy steel doors in the night rain"},
  {"heading":"Черновик реальности","description":"A worn paper subway route map on a dusty technical desk, an extra station marked by hand"},
  {"heading":"Часы без времени","description":"An underground subway control room showing four different wall clocks and empty CCTV monitors"},
  {"heading":"Утренний свет","description":"An old metro door opening to a long bright corridor with a framed portrait of an engineer and a handwritten note"},
  {"heading":"Невидимая линия","description":"An ordinary busy city square in the morning with commuters walking into a metro station entrance"}
]
def locate():
    base=ROOT
    matches=list(base.rglob("final.md"))
    if not matches: raise RuntimeError("original_final_md_not_found")
    folder=matches[0].parent
    manifest=json.loads((folder/"image-manifest.json").read_text(encoding="utf8"))
    pics=[folder/"images"/x["file"] for x in manifest]
    if len(pics)!=7 or not all(x.is_file() for x in pics): raise RuntimeError("original_seven_images_missing")
    if "Станция" not in (folder/"report.json").read_text(encoding="utf8") and "Ночной дозор" not in matches[0].read_text(encoding="utf8"):
        raise RuntimeError("not_the_expected_metro_article")
    return folder,pics,manifest
def score_model(pics,model_id,is_siglip):
    import torch
    from transformers import AutoModel,AutoProcessor
    tick=time.monotonic()
    model=AutoModel.from_pretrained(model_id).eval()
    processor=AutoProcessor.from_pretrained(model_id)
    load_s=round(time.monotonic()-tick,2)
    prompts=[x["description"] for x in SCENES]
    if is_siglip: prompts=["This is a photo of "+p+"." for p in prompts]
    rows=[]
    for p in pics:
        with Image.open(p) as f: photo=ImageOps.exif_transpose(f).convert("RGB")
        photo.thumbnail((768,768))
        t=time.monotonic()
        if is_siglip:
            inp=processor(text=prompts,images=photo,padding="max_length",max_length=64,return_tensors="pt")
        else:
            inp=processor(text=prompts,images=photo,padding=True,truncation=True,max_length=77,return_tensors="pt")
        with torch.inference_mode(): output=model(**inp)
        logits=output.logits_per_image[0].tolist()
        # Similarity scores are not probabilities; display only ranks and within-image margins.
        ranking=sorted(range(7),key=lambda j:logits[j],reverse=True)
        rows.append({"file":p.name,"predicted_section":ranking[0]+1,
                     "ranked_sections":[x+1 for x in ranking],
                     "raw_similarity":[round(v,4) for v in logits],
                     "score_margin_top2":round(logits[ranking[0]]-logits[ranking[1]],4),
                     "inference_s":round(time.monotonic()-t,2)})
    return {"model":model_id,"loading_seconds":load_s,
            "images":rows,"matched_original_order":sum(row["predicted_section"]==i+1 for i,row in enumerate(rows)),
            "note":"Matched_original_order compares against original numbering, which is NOT independently established ground truth."}
def caption_three(pics):
    import torch
    from transformers import AutoProcessor,Florence2ForConditionalGeneration
    model_id="florence-community/Florence-2-base"
    t=time.monotonic()
    model=Florence2ForConditionalGeneration.from_pretrained(model_id).eval()
    proc=AutoProcessor.from_pretrained(model_id)
    loaded=round(time.monotonic()-t,2)
    results=[]
    for p in pics:
        with Image.open(p) as f: image=ImageOps.exif_transpose(f).convert("RGB")
        image.thumbnail((768,768))
        t=time.monotonic()
        prompt="<MORE_DETAILED_CAPTION>"
        inputs=proc(text=prompt,images=image,return_tensors="pt")
        with torch.inference_mode(): output=model.generate(**inputs,max_new_tokens=110,num_beams=2)
        text=proc.batch_decode(output,skip_special_tokens=False)[0]
        desc=proc.post_process_generation(text,task=prompt,image_size=image.size)
        results.append({"file":p.name,"model_caption":desc,"inference_s":round(time.monotonic()-t,2)})
    return {"model":model_id,"loading_seconds":loaded,"captions":results}
def contact_sheet(pics,manifest):
    from PIL import ImageDraw
    canvas=Image.new("RGB",(1200,4*275),(245,245,245));d=ImageDraw.Draw(canvas)
    for k,p in enumerate(pics):
        with Image.open(p) as f: image=f.convert("RGB")
        image.thumbnail((570,240))
        x=(k%2)*600+15;y=(k//2)*275+10
        canvas.paste(image,(x,y))
        d.text((x,y+243),f'{k+1}. {p.name} | section {k+1}',fill=(0,0,0))
    canvas.save(OUT/"original-seven-contact-sheet.jpg",quality=85)
def main():
    report={"source_run":45027517283,"source_artifact":"zen-mystery-owner-readable",
            "model_scores_are_probabilities":False,"publication_authorized":False,
            "scenes":SCENES,"status":"started"}
    try:
        folder,pics,manifest=locate()
        report["original_manifest"]=[{"file":x["file"],"prompt":x["prompt"],"rights_status":x["rights_status"]} for x in manifest]
        contact_sheet(pics,manifest)
        for mid,siglip in [("openai/clip-vit-base-patch32",False),
                            ("google/siglip2-base-patch16-224",True)]:
            try:
                report[mid]=score_model(pics,mid,siglip)
                print("METRO_MODEL_DONE",mid,flush=True)
            except Exception as e:
                report.setdefault("model_failures",[]).append({"name":mid,"error":type(e).__name__+":"+str(e)[:150]})
                print("METRO_MODEL_FAIL",mid,type(e).__name__,flush=True)
        try:
            report["florence"]=caption_three(pics)
            print("METRO_FLORENCE_DONE",flush=True)
        except Exception as e:
            report.setdefault("model_failures",[]).append({"name":"florence","error":type(e).__name__+":"+str(e)[:150]})
            print("METRO_FLORENCE_FAIL",type(e).__name__,flush=True)
        report["status"]="completed" if not report.get("model_failures") else "partial"
    except Exception as e:
        report["status"]="blocked";report["blocker"]=type(e).__name__+":"+str(e)[:180]
    (OUT/"metro-models-report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf8")
    print("METRO_AUDIT",json.dumps({"status":report["status"],"images":7,"models":list(k for k in report if k in ["florence","openai/clip-vit-base-patch32","google/siglip2-base-patch16-224"]),"blocker":report.get("blocker")},ensure_ascii=False),flush=True)
    if report["status"]=="blocked":raise SystemExit(2)
if __name__=="__main__":main()
