"""Harder visual QA benchmark: real FLUX/Klein edits + sourced scientific diagrams.

Actual edit labels are conservative. Ambiguous style differences are unscored.
Scientific diagrams are deliberately constructed; they are NOT real research
figures and are never passed to publication.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont
from editorial_cloudflare_vision import review_pair, VisionPending
from editorial_scene_contract import make_contract
from editorial_science_vision import review_science_diagram, CONTRACTS


def font(size=29):
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name,size)
        except OSError:
            continue
    return ImageFont.load_default()


def scientific_diagram(topic, labels, variant=0):
    img=Image.new("RGB",(960,540),("#f4f7fc" if variant==0 else "#effaf5"))
    d=ImageDraw.Draw(img)
    big=font(34)
    small=font(24)
    if topic=="solar_inner_planets":
        d.text((60,42),"INNER SOLAR SYSTEM  |  FROM SUN OUTWARD",font=big,fill="#15233e")
        d.ellipse((20,192,150,322),fill="#eeb240",outline="#a65c16",width=5)
        d.text((49,331),"SUN",font=small,fill="#15233e")
        centers=(275,475,675,865)
        for j,(cx,lbl) in enumerate(zip(centers,labels)):
            size=[20,35,31,27][j]
            d.ellipse((cx-size,252-size,cx+size,252+size),fill=("#aeb1b7","#d2a16d","#438bc4","#b76440")[j])
            bbox=d.textbbox((0,0),lbl,font=small)
            width=bbox[2]-bbox[0]
            d.text((cx-width//2,315),lbl,font=small,fill="#142038")
            d.line((cx,294,cx,309),fill="#142038",width=2)
        d.text((60,440),"Simplified labelled educational diagram (not to scale)",font=small,fill="#48536a")
    else:
        d.text((58,38),"DNA  |  COMPLEMENTARY BASE PAIRS",font=big,fill="#15233e")
        d.line((235,155,235,455),fill="#2d77bd",width=13)
        d.line((710,155,710,455),fill="#d67c50",width=13)
        for j,pair in enumerate(labels):
            y=170+j*85
            left,right=pair.split("-")
            d.line((245,y,700,y),fill="#667d90",width=6)
            for x,letter in ((340,left),(605,right)):
                d.rounded_rectangle((x-58,y-33,x+58,y+33),radius=18,fill="#ffffff",outline="#415c79",width=3)
                d.text((x-12,y-21),letter,font=big,fill="#17243a")
        d.text((60,475),"Simplified base pairing illustration",font=small,fill="#48536a")
    stream=BytesIO()
    img.save(stream,format="PNG")
    return stream.getvalue()


def scene_cases(originals,refined):
    contracts={
        1:make_contract("A young woman in dark green coat and holding a light stands in "
              "an old abandoned apartment corridor looking toward an open doorway to trees.",
              subject="same woman in dark coat",
              location="old apartment corridor with a door leading outdoors",
              action="woman facing illuminated doorway",
              must_keep=["camera looks down old corridor","open doorway and trees",
                         "same position and existing lighting"],
              fix="Keep the image exactly the same; no changes"),
        2:make_contract("A dark-haired woman in forest green coat stands in snow near "
              "the wooden doorway to a forest after coming from the old apartment.",
              subject="same dark-haired woman wearing dark green coat",
              location="snowy forest near old wooden doorway",
              action="woman faces the forest from the doorway",
              must_keep=["woman","upright wooden doorframe","snow and forest"],
              fix="Remove wooden bench blocking the doorway",
              forbid=["do not add or restore a bench in the doorway"]),
        3:make_contract("A young woman in dark green coat stands in an old corridor "
              "facing a closed door with snowy footprints across old wooden floor.",
              subject="same woman wearing dark coat",
              location="old wallpapered corridor with closed door",
              action="woman looks toward door",
              must_keep=["same woman","closed door","footprints"],
              fix="Keep the scene and existing atmosphere unchanged")
    }
    # Grounded objective labels for identity, clear reversal, clear scene substitution.
    yield ("genuine_bench_fix",True,originals[2],refined[2],contracts[2],True)
    yield ("bench_reintroduced",False,refined[2],originals[2],contracts[2],True)
    yield ("unrelated_hall_replaces_forest",False,originals[2],refined[3],contracts[2],True)
    yield ("snowy_forest_replaces_closed_hall",False,originals[3],refined[2],contracts[3],True)
    yield ("unchanged_corridor",True,originals[1],originals[1],contracts[1],True)
    yield ("unchanged_closed_hall",True,originals[3],originals[3],contracts[3],True)
    # More genuine edits, but without verified labels on subjective style drift.
    yield ("real_klein_corridor_edit",None,originals[1],refined[1],contracts[1],False)
    yield ("real_klein_closed_hall_edit",None,originals[3],refined[3],contracts[3],False)


def science_cases():
    true_planets=["Mercury","Venus","Earth","Mars"]
    wrong_planets=["Mercury","Earth","Venus","Mars"]
    true_dna=["A-T","G-C","C-G","T-A"]
    wrong_dna=["A-G","G-C","C-G","T-A"]
    for category,true,wrong in (
        ("solar_inner_planets",true_planets,wrong_planets),
        ("dna_complementary_base_pairs",true_dna,wrong_dna)):
        for variant in (0,1):
            yield (category+"_correct_style"+str(variant),True,scientific_diagram(category,true,variant),category)
            yield (category+"_swapped_style"+str(variant),False,scientific_diagram(category,wrong,variant),category)


def benchmark(outdir,originals,refined):
    outdir.mkdir(parents=True,exist_ok=True)
    report=[]
    def record(name,kind,expected,func):
        try:
            verdict=func()
            accepted=verdict.get("status")=="needs_independent_validation"
            result={"case":name,"kind":kind,"expected_accept":expected,
                    "model_status":verdict["status"],"predicted_accept":accepted,
                    "correct":accepted==expected if expected is not None else None,
                    "evidence":verdict}
        except VisionPending as exc:
            result={"case":name,"kind":kind,"expected_accept":expected,
                    "model_status":"pilot_failed","error":str(exc),
                    "predicted_accept":None,"correct":None}
        report.append(result)
        (outdir/"detail.json").write_text(json.dumps(report,indent=2,ensure_ascii=False))
        print("HARD_CASE_%02d %-39s expected=%s actual=%s" %
              (len(report),name,expected,result["predicted_accept"]),flush=True)
        time.sleep(.35)

    for name,expected,src,candidate,contract,scored in scene_cases(originals,refined):
        record(name,"genuine_flux_klein_pair",expected,
               lambda a=src,b=candidate,c=contract:review_pair(a,b,c))
    for name,expected,image,category in science_cases():
        record(name,"sourced_scientific_label_check",expected,
               lambda a=image,c=category:review_science_diagram(a,c))
    scored=[x for x in report if x["expected_accept"] is not None]
    completed=[x for x in scored if x["correct"] is not None]
    negatives=[x for x in completed if not x["expected_accept"]]
    positives=[x for x in completed if x["expected_accept"]]
    summary={"total_cases":len(report),"scored_cases":len(scored),
             "ambiguous_unscored":len(report)-len(scored),
             "completed_scored":len(completed),"failed_scored":len(scored)-len(completed),
             "correct":sum(x["correct"] for x in completed),
             "negatives":len(negatives),
             "false_accepts":sum(x["predicted_accept"] for x in negatives),
             "positives":len(positives),
             "false_rejects":sum(not x["predicted_accept"] for x in positives),
             "semantic_verified":False,"automated_approval_enabled":False}
    (outdir/"summary.json").write_text(json.dumps(summary,indent=2))
    print("HARD_BENCHMARK_SUMMARY="+json.dumps(summary),flush=True)
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--old",type=Path,required=True)
    p.add_argument("--refined",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    originals={i:(args.old/f"cloudflare-scene-{i}.jpg").read_bytes() for i in (1,2,3)}
    refined={i:(args.refined/f"scene-{i}"/"02-klein.jpg").read_bytes() for i in (1,2,3)}
    benchmark(args.out,originals,refined)
