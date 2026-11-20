#!/usr/bin/env python3
"""Run caption vs. image triage on all eight current movie illustrations.

CLIP values are *not* probabilities of truth, film identity or scene matching.
Scores and ranks are advisory only. The English descriptions are human-curated
translations of the existing image captions, not external scene evidence.
"""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARTICLE=ROOT/"content/articles/cinema-01-truman-show-real-photos.json"
DESCRIPTIONS = {
    "01_seahaven_site.jpg": "a neat sunny American suburban town, houses and streets",
    "02_mirror_site.jpg": "a man looking at himself in a bathroom mirror",
    "03_viewers_site.jpg": "a group of people watching a television screen",
    "04_meryl_truman_site.jpg": "a man and a woman standing together talking",
    "05_cocoa_site.jpg": "a woman advertising a cocoa product in front of a camera",
    "06_christof_site.jpg": "a television director inside a production control room",
    "07_sea_site.jpg": "a man sailing alone on a small boat in the ocean",
    "08_exit_site.jpg": "a man touching the blue wall at the edge of a simulated sky",
}
NEGATIVE_DESCRIPTIONS = [
    "a close-up photograph of a cat sitting on a sofa",
    "a snowy mountain landscape with pine trees",
    "a rocket launching into space"
]

def interpret(matrix, filenames, positive_labels, negatives):
    if len(matrix)!=len(filenames):
        raise ValueError("Bad image matrix shape")
    labels=positive_labels+negatives
    rows=[]
    for i,(name,scores) in enumerate(zip(filenames,matrix)):
        if len(scores)!=len(labels):
            raise ValueError("Bad prompt matrix shape")
        ranking=sorted(range(len(labels)),key=lambda n:scores[n],reverse=True)
        expected=float(scores[i]); best=float(scores[ranking[0]])
        other=max(float(x) for j,x in enumerate(scores) if j!=i)
        rows.append({
            "file":name,
            "expected_caption_english":positive_labels[i],
            "caption_cosine_similarity":round(expected,4),
            "top_candidate":labels[ranking[0]],
            "top_candidate_score":round(best,4),
            "expected_minus_best_other":round(expected-other,4),
            "caption_is_top_ranked":ranking[0]==i,
            "status":"editor_review_candidate" if ranking[0]!=i else "caption_ranked_first_not_verified",
            "visual_scene_verified":False,
            "film_identity_verified":False,
            "image_rights_verified":False,
            "top_three":[{"description":labels[j],"cosine":round(float(scores[j]),4)}
                         for j in ranking[:3]],
        })
    return rows

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default=str(ROOT/"editorial-remaining-tests/clip-caption-report.json"))
    args=parser.parse_args()
    from PIL import Image
    import torch
    import open_clip
    article=json.loads(ARTICLE.read_text(encoding="utf-8"))
    files=article["image_files"]
    if len(files)!=8:
        raise ValueError("Pilot expected all eight stills; none should be silently skipped")
    positive=[]
    for name in files:
        base=Path(name).name
        if base not in DESCRIPTIONS:
            raise ValueError("Missing approved English description for "+base)
        positive.append(DESCRIPTIONS[base])
    all_text=positive+NEGATIVE_DESCRIPTIONS
    model,_,transform=open_clip.create_model_and_transforms(
        "ViT-B-32",pretrained="laion2b_s34b_b79k",device="cpu")
    tokenizer=open_clip.get_tokenizer("ViT-B-32")
    with torch.no_grad():
        img_data=[]
        for name in files:
            with Image.open(ROOT/name) as im:
                img_data.append(transform(im.convert("RGB")))
        images=torch.stack(img_data)
        image_features=model.encode_image(images)
        text_features=model.encode_text(tokenizer(all_text))
        image_features=image_features/image_features.norm(dim=-1,keepdim=True)
        text_features=text_features/text_features.norm(dim=-1,keepdim=True)
        cosine=(image_features @ text_features.T).tolist()
    matches=interpret(cosine,files,positive,NEGATIVE_DESCRIPTIONS)
    result={
        "schema":"editorial-clip-captions-v2","model":"ViT-B-32/laion2b_s34b_b79k",
        "evaluated_images":len(files),"prompts":len(all_text),
        "caption_ranked_first":sum(r["caption_is_top_ranked"] for r in matches),
        "needs_editor_review":sum(not r["caption_is_top_ranked"] for r in matches),
        "rankings":matches,
        "rights_verified":False,"film_plot_verified":False,"publication_authorized":False,
        "note":"Ranking is a triage aid only. No automatic replacements or scene confirmations."
    }
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"images":result["evaluated_images"],
          "caption_ranked_first":result["caption_ranked_first"],
          "editor_review":result["needs_editor_review"],
          "publication_authorized":False},ensure_ascii=False))

if __name__=="__main__":
    main()
