#!/usr/bin/env python3
"""Three safe smoke checks for previously deferred editorial automation tools."""
from pathlib import Path
import json
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
RESULT=ROOT/"editorial-remaining-tests"
RESULT.mkdir(exist_ok=True)

def run_clip():
    from PIL import Image
    import torch
    import open_clip
    article=json.loads((ROOT/"content/articles/cinema-01-truman-show-real-photos.json").read_text())
    paths=[ROOT/p for p in article["image_files"][:3]]
    prompts=["a man looking into a mirror", "a group of people watching a television",
             "a brightly lit suburban neighborhood with houses"]
    model, _, transform=open_clip.create_model_and_transforms("ViT-B-32",pretrained="laion2b_s34b_b79k",device="cpu")
    tokenizer=open_clip.get_tokenizer("ViT-B-32")
    with torch.no_grad():
        image_tensor=torch.stack([transform(Image.open(path).convert("RGB")) for path in paths])
        image_features=model.encode_image(image_tensor)
        text_features=model.encode_text(tokenizer(prompts))
        image_features=image_features/image_features.norm(dim=-1,keepdim=True)
        text_features=text_features/text_features.norm(dim=-1,keepdim=True)
        matrix=(image_features @ text_features.T).tolist()
    return {"status":"completed","model":"ViT-B-32/laion2b_s34b_b79k",
       "image_files":[str(x.relative_to(ROOT)) for x in paths],
       "test_descriptions":prompts,
       "similarity_matrix":[[round(float(x),3) for x in row] for row in matrix],
       "film_identity_verified":False,"scene_verified":False,
       "caution":"Contrastive similarities are not calibrated probabilities or film identity checks."}

def run_playwright():
    from playwright.sync_api import sync_playwright
    local=ROOT/"editorial-remaining-tests/local-review.html"
    local.write_text("<html><head><meta charset='utf-8'></head><body><h1>Редакторская проверка</h1><textarea id='draft'></textarea><button id='save' onclick=\"document.querySelector('#state').textContent='сохранено'\">Сохранить</button><div id='state'></div></body></html>",encoding="utf-8")
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        page.goto(local.as_uri())
        page.locator("#draft").fill("Проверка формы без внешнего сайта")
        page.locator("#save").click()
        state=page.locator("#state").inner_text()
        browser.close()
    assert state=="сохранено",state
    return {"status":"completed","local_form_operated":True,
            "external_website_tested":False,"login_or_captcha_tested":False,
            "recommendation":"Browser automation technically works; production sites need their own permission and reliability test."}

if __name__=="__main__":
    name=sys.argv[1]
    start=perf_counter()
    try:
        out={"CLIP":run_clip,"Playwright":run_playwright}[name]()
    except Exception as e:
        out={"status":"unavailable","error":type(e).__name__+": "+str(e)[:350]}
    out["duration_seconds"]=round(perf_counter()-start,2)
    out["test"]=name
    out["publication_authorized"]=False
    (RESULT/(name.lower()+".json")).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False))
    if out["status"]!="completed":
        raise SystemExit(1)
