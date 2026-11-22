"""Live source-image audit with complete three-model reviewer and object finding.
Outputs source filenames, model observations, and repair briefs, not autoapproval.
"""
import json
from pathlib import Path
from src.editorial_image_feedback import inspect_visual,repair_prompt
from src.editorial_visual_constraints import evaluate

SCENE={
  "subject":"four round wall clocks above a vintage metro dispatcher console",
  "brief":"An abandoned metro control room, exactly four separate round wall clocks on the wall above an old control panel, each showing a different time",
  "count_requirements":[{"object":"wall clock","mode":"exact","value":4}]
}
FILES=["06-control-room.jpg","four-clocks-attempt-1.jpg","four-clocks-attempt-2.jpg"]
OUTPUT=Path("metro-complete-review")
OUTPUT.mkdir(exist_ok=True)
records=[]
for name in FILES:
    matches=list(Path("metro-count-input").rglob(name))
    if len(matches)!=1: raise RuntimeError("missing original sample "+name)
    data=matches[0].read_bytes()
    result=inspect_visual(data,SCENE)
    review=evaluate(SCENE,result)
    repair=repair_prompt(SCENE,result,style="realistic cinematic metro mystery")
    row={"file":name,"review":review,
         "florence_caption":result["florence_caption"],
         "siglip_relative_margin":result["siglip_relative_margin"],
         "grounding":result.get("object_detector"),
         "repair_prompt":repair,
         "automatically_accepted":review["passed"]}
    records.append(row)
    print("INTEGRATED_VISUAL_CHECK",json.dumps({"file":name,
        "count":row["grounding"]["detections"].get("wall clock",{}).get("count"),
        "review_status":review["status"],"repair_mentions_count":"EXACTLY 4" in repair}),flush=True)
(OUTPUT/"integrated-real-review.json").write_text(json.dumps({
    "status":"completed","automatic_publication":False,
    "model_assessment_is_not_human_ground_truth":True,
    "records":records},indent=2,ensure_ascii=False),encoding="utf8")
