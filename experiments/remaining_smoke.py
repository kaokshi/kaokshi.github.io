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
    """Test a complete editor review cycle, never the real Zen website."""
    from playwright.sync_api import sync_playwright
    import shutil
    installed_browser=(shutil.which("google-chrome") or shutil.which("chromium")
                       or shutil.which("chromium-browser"))
    if not installed_browser:
        raise RuntimeError("No preinstalled Chromium/Chrome on GitHub runner")
    local=ROOT/"experiments/approval_preview.html"
    decisions=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=installed_browser)
        page=browser.new_page()
        page.goto(local.as_uri())
        # An incomplete approval request must not be accepted.
        page.locator("#approve").click()
        assert page.locator("#state").inner_text()=="Текст отсутствует"
        for channel in ("legends","science","cinema"):
            page.locator("#channel").select_option(channel)
            page.locator("#draft").fill("Тестовая статья для проверки редакционного согласования.")
            page.locator("#comment").fill("")
            page.locator("#revision").click()
            assert page.locator("#state").inner_text()=="Нужно указать замечание"
            page.locator("#comment").fill("Уточнить факт и источник.")
            page.locator("#revision").click()
            changes=page.evaluate("window.__localReviewResult")
            assert changes["decision"]=="changes_requested"
            assert changes["publication_authorized"] is False
            page.locator("#approve").click()
            approved=page.evaluate("window.__localReviewResult")
            assert approved["decision"]=="editorially_approved"
            assert approved["channel"]==channel
            assert approved["publication_authorized"] is False
            decisions.append({"channel":channel,"revision_request_passed":True,
                              "approval_state_passed":True,"publication_authorized":False})
        browser.close()
    return {"status":"completed","local_form_operated":True,
            "channels_tested":len(decisions),"cases":decisions,
            "missing_content_blocked":True,"missing_revision_reason_blocked":True,
            "external_website_tested":False,"authentication_tested":False,
            "publication_authorized":False,
            "recommendation":"UI prototype is valid. No real approvals or Zen actions were performed."}

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
