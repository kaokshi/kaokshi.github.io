"""Real Grounding DINO CPU trial on existing Klein clock images.

Purpose: see if a free open-vocabulary detector can distinguish one, two
and four visible clocks; detections are not automatically ground truth.
"""
import io,json,time,zipfile,os
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import torch
from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection

ROOT=Path("metro-count-input")
OUT=Path("metro-object-count")
OUT.mkdir(exist_ok=True)
MODEL_ID="IDEA-Research/grounding-dino-tiny"
TESTS=[("original","06-control-room.jpg",1),
       ("new-1","four-clocks-attempt-1.jpg",4),
       ("new-2","four-clocks-attempt-2.jpg",2)]

def locate(name):
    paths=list(ROOT.rglob(name))
    if len(paths)!=1: raise RuntimeError("missing_source_"+name)
    return paths[0]

def dedupe(boxes,scores,iou=.45):
    order=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
    keep=[]
    def overlap(a,b):
        x=max(0,min(a[2],b[2])-max(a[0],b[0]))
        y=max(0,min(a[3],b[3])-max(a[1],b[1]))
        intersection=x*y
        area=lambda b:max(0,b[2]-b[0])*max(0,b[3]-b[1])
        return intersection/max(1,area(a)+area(b)-intersection)
    for ix in order:
        if all(overlap(boxes[ix],boxes[other])<iou for other in keep):
            keep.append(ix)
    return keep

def main():
    report={"model":MODEL_ID,"data":"real Klein images, expected counts manually assigned from review",
            "expected_counts_are_not_independent_object_annotations":True,
            "auto_approve_images":False,"rows":[]}
    start=time.monotonic()
    try:
        processor=AutoProcessor.from_pretrained(MODEL_ID)
        model=AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID).eval()
        for kind,name,expected in TESTS:
            with Image.open(locate(name)) as source: image=source.convert("RGB")
            image.thumbnail((900,900))
            inputs=processor(images=image,text="wall clock.",return_tensors="pt")
            with torch.no_grad():
                output=model(**inputs)
            predictions=processor.post_process_grounded_object_detection(
                output,inputs.input_ids,threshold=.25,text_threshold=.25,
                target_sizes=[(image.height,image.width)])[0]
            boxes=[[round(float(v),1) for v in box] for box in predictions["boxes"]]
            scores=[round(float(v),4) for v in predictions["scores"]]
            labels=[str(v) for v in predictions["labels"]]
            keep=dedupe(boxes,scores)
            result={"file":name,"expected_human_approx":expected,
                    "detected_count":len(keep),"boxes":[boxes[i] for i in keep],
                    "scores":[scores[i] for i in keep],"labels":[labels[i] for i in keep],
                    "correct_count":len(keep)==expected}
            report["rows"].append(result)
            draw=ImageDraw.Draw(image)
            for i in keep:
                box=boxes[i]
                draw.rectangle(box,outline="red",width=3)
                draw.text((box[0],box[1]),str(scores[i]),fill="red")
            image.save(OUT/(kind+"-detected.jpg"),quality=87)
            print("REAL_OBJECT_COUNT",json.dumps(result),flush=True)
        report["status"]="completed"
    except Exception as exc:
        report["status"]="failed"
        report["failure"]=type(exc).__name__+":"+str(exc)[:250]
        print("DETECTION_FAILED",report["failure"],flush=True)
    finally:
        report["elapsed_s"]=round(time.monotonic()-start,2)
        (OUT/"count-trial.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf8")
        print("COUNT_EXPERIMENT_END",report["status"],flush=True)
if __name__=="__main__":main()
