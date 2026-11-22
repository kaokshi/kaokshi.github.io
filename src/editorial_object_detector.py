"""Optional CPU object evidence for illustration repair, not auto-approval.

Grounding DINO labels and counts are predictions, not fact/right verification.
Bounding boxes are NMS deduplicated; missing or ambiguous results are review-only.
"""
from functools import lru_cache
from threading import Lock
import io
from .editorial_providers import Blocked

MODEL = "IDEA-Research/grounding-dino-tiny"
LOCK = Lock()

@lru_cache(maxsize=1)
def _model():
    from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
    return (AutoProcessor.from_pretrained(MODEL),
            AutoModelForZeroShotObjectDetection.from_pretrained(MODEL).eval())

def _iou(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]))
    y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    area=lambda q:max(0,q[2]-q[0])*max(0,q[3]-q[1])
    return x*y/max(1,area(a)+area(b)-x*y)

def objects(raw, labels, *, threshold=.25, text_threshold=.25):
    if not isinstance(labels, list) or not labels or len(labels)>12:
        raise ValueError("invalid_detected_object_labels")
    if any(not isinstance(x,str) or not 2<=len(x.strip())<=80 for x in labels):
        raise ValueError("invalid_object_name")
    try:
        import torch
        from PIL import Image,ImageOps
        with Image.open(io.BytesIO(raw)) as p:
            image=ImageOps.exif_transpose(p).convert("RGB")
            image.thumbnail((900,900))
        processor, model=_model()
        rows={}
        with LOCK, torch.inference_mode():
            for label in labels:
                tokens=processor(images=image,text=label.strip().rstrip(".")+".",return_tensors="pt")
                output=model(**tokens)
                pred=processor.post_process_grounded_object_detection(
                    output,tokens.input_ids,threshold=threshold,
                    text_threshold=text_threshold,target_sizes=[(image.height,image.width)])[0]
                boxes=[[float(v) for v in box] for box in pred["boxes"]]
                scores=[float(s) for s in pred["scores"]]
                order=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
                retained=[]
                for i in order:
                    if all(_iou(boxes[i],boxes[j])<.45 for j in retained):
                        retained.append(i)
                rows[label]={"count":len(retained),
                             "scores":[round(scores[i],4) for i in retained],
                             "boxes":[[round(v,1) for v in boxes[i]] for i in retained]}
        return {"model":MODEL,"detections":rows,"verified_independently":False,
                "independent_of_captioner":True,
                "note":"detector predictions only; no automatic factual or count certification"}
    except (ImportError,OSError,RuntimeError,ValueError,TypeError,KeyError) as exc:
        raise Blocked("object_detector_unavailable_"+type(exc).__name__) from None

def evidence_for_scene(raw,scene):
    from .editorial_visual_constraints import validate_constraints
    counts=validate_constraints(scene)
    labels=list(dict.fromkeys(x["object"] for x in counts))
    # Short concrete nouns only: arbitrary negative phrases cannot safely be
    # transformed to object detection queries.
    for key in ("must_include","must_avoid"):
        for label in scene.get(key,[]):
            if len(label.split())<=3 and label not in labels:
                labels.append(label)
    if not labels:
        return {"model":MODEL,"detections":{},"verified_independently":False}
    return objects(raw,labels[:12])

def discrepancy_instructions(scene,detector_evidence):
    from .editorial_visual_constraints import validate_constraints
    requirements=validate_constraints(scene)
    detected=detector_evidence.get("detections",{})
    findings=[]
    for required in requirements:
        label=required["object"]
        result=detected.get(label,{})
        actual=result.get("count")
        if type(actual) is int and actual!=required["value"]:
            findings.append(f"Detector identified {actual} instances of {label}; target is EXACTLY {required['value']}.")
        elif type(actual) is not int:
            findings.append(f"Count for {label} was not verified; depict exactly {required['value']} distinct instances.")
    return findings
