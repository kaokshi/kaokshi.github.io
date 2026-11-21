"""Independent documentary-photo vision benchmark using six NEW Wikimedia Commons files.

24 requests = 6 real new photographs, each with 2 correct and 2 incorrect
source-independent subject descriptions. Labels reflect PRIMARY SUBJECT, not
finer scientific claim accuracy. No original or edited images are published.
"""
from io import BytesIO
from hashlib import sha256
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlencode, urlsplit
import requests
from PIL import Image, ImageOps

from editorial_photo_claim_qa import review_photo_claim
from editorial_cloudflare_vision import VisionPending

# Each file title and principal subject was independently checked from its
# Wikimedia Commons file page on 2026-11-21.
PHOTOS=(
    {"key":"earth","title":"File:The Earth seen from Apollo 17.jpg",
     "subject":"A round blue Earth viewed from space, with swirling white clouds",
     "category":"planet viewed from space"},
    {"key":"mars","title":"File:Perseverance's First Full-Color Look at Mars.png",
     "subject":"A rocky dusty surface of the planet Mars photographed by a rover",
     "category":"rocky planetary surface"},
    {"key":"volcano","title":"File:MSH80 eruption mount st helens plume 05-18-80.jpg",
     "subject":"A volcanic eruption with a towering ash plume rising above the mountain",
     "category":"erupting volcano"},
    {"key":"jellyfish","title":"File:Jellyfish and starfish 1.jpg",
     "subject":"A jellyfish and sea stars underwater among seaweed",
     "category":"marine invertebrates underwater"},
    {"key":"butterfly","title":"File:Monarch butterfly (70387).jpg",
     "subject":"A monarch butterfly showing orange and black patterned wings",
     "category":"orange and black butterfly"},
    {"key":"mushroom","title":"File:Amanita muscaria mushrooms.jpg",
     "subject":"Fly agaric mushrooms with distinctive red caps and pale spots",
     "category":"red-capped wild mushrooms"},
)


def load_photo(spec):
    api="https://commons.wikimedia.org/w/api.php"
    query={"action":"query","format":"json","prop":"imageinfo",
           "iiprop":"url|size","iiurlwidth":"960","titles":spec["title"]}
    headers={"User-Agent":"ZenVisionQA-Pilot/1.0 (educational accuracy research; GitHub kaokshi/kaokshi.github.io)"}
    response=requests.get(api,params=query,headers=headers,timeout=45)
    response.raise_for_status()
    pages=response.json()["query"]["pages"]
    if len(pages)!=1:
        raise ValueError("Unexpected Commons response")
    page=next(iter(pages.values()))
    if "missing" in page or not page.get("imageinfo"):
        raise ValueError("Commons file not found: "+spec["key"])
    info=page["imageinfo"][0]
    url=info.get("thumburl") or info["url"]
    host=urlsplit(url).hostname
    if (urlsplit(url).scheme!="https" or host not in
            ("upload.wikimedia.org","commons.wikimedia.org")):
        raise ValueError("Unapproved image host: "+str(host))
    print("IMAGE_HOST "+spec["key"]+" "+host,flush=True)
    img_resp=requests.get(url,headers=headers,timeout=60)
    img_resp.raise_for_status()
    if len(img_resp.content)>15_000_000:
        raise ValueError("Oversize test photo")
    with Image.open(BytesIO(img_resp.content)) as image:
        image.load()
        if min(image.size)<150:
            raise ValueError("Test photograph is too small")
        image=ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((960,960))
        out=BytesIO()
        image.save(out,format="JPEG",quality=88)
    return out.getvalue(), {"file_page":"https://commons.wikimedia.org/wiki/"+spec["title"].replace(" ","_"),
                             "visual_image_sha256":sha256(out.getvalue()).hexdigest(),
                             "pixel_size":list(image.size)}


def run(outdir):
    outdir.mkdir(parents=True,exist_ok=True)
    photos=[]
    for spec in PHOTOS:
        data,source=load_photo(spec)
        photos.append((spec,data,source))
        print("FETCHED "+spec["key"]+" sha="+source["visual_image_sha256"][:10],flush=True)
    results=[]
    for i,(spec,pixels,source) in enumerate(photos):
        # Two synonymous, objectively visible correct claims and two descriptions
        # of distinct other real photographs. No synthetic image artifacts.
        cases=[
            (spec["subject"], True,"primary_subject"),
            ("This photograph primarily shows "+spec["category"]+".", True,"short_description"),
            (photos[(i+2)%len(photos)][0]["subject"],False,"wrong_topic_2"),
            ("This photograph primarily shows "+photos[(i+3)%len(photos)][0]["category"]+".",False,"wrong_topic_3")
        ]
        for claim,expected,case in cases:
            record={"index":len(results)+1,"image_key":spec["key"],"case":case,
                    "expected_match":expected,"claim":claim,
                    "image_source":source}
            try:
                verdict=review_photo_claim(pixels,{"claim":claim})
                record["predicted_match"]=verdict["predicted_match"]
                record["model_status"]=verdict["status"]
                record["observed_evidence"]=verdict["observable_evidence"]
                record["correct"]=verdict["predicted_match"]==expected
            except VisionPending as exc:
                record["predicted_match"]=None
                record["model_status"]="pilot_failed"
                record["error"]=str(exc)
                record["correct"]=None
            results.append(record)
            (outdir/"cases.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
            print("NEW_PHOTO_CASE %02d/24 %-15s %-18s true=%s predicted=%s" %
                  (len(results),spec["key"],case,expected,record["predicted_match"]),flush=True)
            time.sleep(.35)
    finished=[r for r in results if r["correct"] is not None]
    false_accepts=[r for r in finished if not r["expected_match"] and r["predicted_match"]]
    false_rejects=[r for r in finished if r["expected_match"] and not r["predicted_match"]]
    summary={"cases":len(results),"independent_photos":len(photos),
             "completed":len(finished),"failed":len(results)-len(finished),
             "correct":sum(r["correct"] for r in finished),
             "false_accepts":len(false_accepts),"false_rejects":len(false_rejects),
             "false_accept_cases":[r["index"] for r in false_accepts],
             "false_reject_cases":[r["index"] for r in false_rejects],
             "scientific_accuracy_verified":False,
             "autonomous_image_approval_enabled":False,
             "scope":"Broad subject claims of six new real documentary photographs; not subtle natural artifacts"}
    (outdir/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print("NEW_PHOTO_BENCHMARK_SUMMARY="+json.dumps(summary),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    run(args.out)
