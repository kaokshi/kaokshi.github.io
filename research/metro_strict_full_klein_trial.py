"""Bounded real Cloudflare Klein + CLIP/SigLIP/Florence/DINO candidate trial.

No article publishing. Existing final ImageIndex.save is deliberately reused.
At most three Klein calls, with rights pending and explicit unresolved QA blockers.
"""
import json,os,sys,threading,time
from pathlib import Path
from PIL import Image,ImageDraw
from src.editorial_images import ImageIndex,generated
from src.editorial_providers import Blocked

OUT=Path("metro-strict-full-trial")
OUT.mkdir(exist_ok=True)
SCENE={"section":4, "kind":"illustration",
       "subject":"four different round clocks in a metro control room",
       "brief":"Inside an abandoned metro dispatcher room, exactly FOUR separate wall clocks aligned across a wall above the railway control console, each with distinct hands and visible circular edges, low light, no people, cinematic mystery still",
       "count_requirements":[{"object":"wall clock","mode":"exact","value":4}],
       "must_include":[],"must_avoid":[]}
class Memory:
    def __init__(self):
        self.lock=threading.Lock()
        self.root=OUT/"local-state"
        self.root.mkdir(exist_ok=True)
        self.calls=0
        self.started=time.monotonic()
    def time_left(self): return 3600-(time.monotonic()-self.started)
    def call(self, task, article, provider, callback):
        if provider!="klein" or self.calls>=3: raise Blocked("experimental_provider_call_cap")
        self.calls+=1
        raw,params=callback()
        (OUT/f"attempt-{self.calls}.png").write_bytes(raw)
        print("STRICT_KLEIN_CALL",self.calls,"image_bytes",len(raw),flush=True)
        return raw,params
    def save(self): pass

def contact_sheet():
    files=sorted(OUT.glob("attempt-*.png"))
    canvas=Image.new("RGB",(1200,((len(files)+1)//2)*455),(242,242,242))
    draw=ImageDraw.Draw(canvas)
    for i,p in enumerate(files):
        with Image.open(p) as img:
            img=img.convert("RGB"); img.thumbnail((570,410))
            x=(i%2)*600+10;y=(i//2)*455+10
            canvas.paste(img,(x,y))
            draw.text((x,y+415),"Klein attempt "+str(i+1),fill=(0,0,0))
    if files:canvas.save(OUT/"attempts.jpg",quality=90)

def main():
    record={"source":"isolated user-approved metro illustration experiment",
            "article_publication":False,"max_klein_calls":3,
            "models":["openai/clip-vit-base-patch32","google/siglip2-base-patch16-224",
                      "florence-community/Florence-2-base","IDEA-Research/grounding-dino-tiny"],
            "status":"started"}
    memory=Memory()
    try:
        if os.environ.get("EDITORIAL_CF_FREE_ONLY")!="true":
            raise Blocked("cloudflare_free_attestation_missing")
        if os.environ.get("EDITORIAL_IMAGE_FEEDBACK_EXPERIMENT")!="true" or os.environ.get("EDITORIAL_OBJECT_DETECTION_EXPERIMENT")!="true":
            raise Blocked("experimental_review_flags_missing")
        index=ImageIndex(memory,[])
        result=generated(SCENE,"metro mystery","unused",index,"image-4",
                         "metro-strict-test","legends")
        record["status"]="completed"
        record["selected"]=result
        record["selected_image"]=result.get("file")
        record["blocking_visual_status"]=result.get("visual_feedback",{}).get("candidate_status")
        if record["blocking_visual_status"]=="owner_review_required":
            record["publication_blocked"]=True
        else:
            record["publication_blocked"]=False
    except Exception as exc:
        record["status"]="blocked"
        record["blocker"]=type(exc).__name__+":"+str(exc)[:180]
    finally:
        record["klein_calls"]=memory.calls
        record["generated_images"]=[x.name for x in sorted(OUT.glob("attempt-*.png"))]
        contact_sheet()
        (OUT/"strict-full-results.json").write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf8")
        print("STRICT_FULL_RESULT",json.dumps({"status":record["status"],"calls":memory.calls,
          "published":False,"visual_status":record.get("blocking_visual_status"),
          "blocker":record.get("blocker")},ensure_ascii=False),flush=True)
    if record["status"]!="completed" or not record["publication_blocked"]:
        raise SystemExit(2)
if __name__=="__main__":main()
