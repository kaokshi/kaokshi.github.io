#!/usr/bin/env python3
"""Actual autonomous single-article chain with FLUX.2 Klein 4B inside GitHub Actions.

Cloudflare one-image preflight FIRST. A quota block stops expensive Groq/OpenRouter
calls rather than silently substituting ChatGPT images. If available: 7 distinct
images, Groq author, OpenRouter full rewrite, 4 local AI diagnostics, DOCX/HTML,
owner-encrypted archive, and honest non-publication QA report.
"""
import base64,hashlib,io,json,os,re,sys,zipfile,html
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from PIL import Image,ImageOps
from docx import Document
from docx.shared import Inches
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research"),str(ROOT/"src")]
from editorial_encrypted_review import encrypt
from editorial_ai_check import extract_text
import zen_full_article_pilot as base

MODEL="@cf/black-forest-labs/flux-2-klein-4b"
# A fresh, non-duplicate mystery with visual beats matching every illustration.
base.TITLE="Станция метро, которой не было на ночной схеме"
base.BRIEF=("Оригинальная мистическая история для канала «Легенды тёмных улиц» "
 "под заголовком «"+base.TITLE+"». Главный герой дежурит в метрополитене "
 "и после полуночи обнаруживает остановку поезда у давно закрытой станции. "
 "Семь точных визуальных сцен: пустой подземный перрон с одной горящей лампой; "
 "закрытый вход метро под дождём; старая схема линий; кассовое окошко с пыльным "
 "стеклом; чёрный тоннель; диспетчерская с часами; утро у закрытого входа. "
 "Сюжет цельный с убедимыми действиями героя, мотивами и логичной необычной "
 "развязкой, без развязок «всё приснилось» и «герой мёртв». Это художественная "
 "история, а не утверждение о реальных происшествиях. "
 "От 3000 до 6000 символов БЕЗ ПРОБЕЛОВ, желательно 4200–5100; "
 "от 5 до 7 смысловых блоков, каждый с **жирным заголовком** отдельной строкой "
 "и первый блок начинается с жирного заголовка; финальный естественный призыв "
 "подписаться на «Легенды тёмных улиц». Не используй повторяющиеся сцены "
 "и штампованные фразы. Верни полный рассказ, без пояснений.")
IMAGE_BRIEFS=[
 ("cover","Cinematic high-quality cover illustration, deserted abandoned underground metro platform at midnight, one warm glowing overhead light above old curved tunnel, damp floor reflections, deep teal and amber color, lonely eerie atmosphere, striking perspective, no typography, no text, 1024x768."),
 ("entrance","Photorealistic fictional old city underground metro entrance at midnight, heavy locked steel gate, wet urban steps, cold blue rain and muted lamps, atmospheric mysterious realistic scene, no text, no legible signs."),
 ("map","Close-up atmospheric photograph-like old fictional subway route map laid on a worn desk, hand with small flashlight, worn folded paper with abstract colored lines and no readable labels, blue shadows and warm desk lamp."),
 ("ticket-booth","Abandoned underground metro ticket booth with dusty glass, dim lamp, rusted change tray, silent empty corridor at night, realistic cinematic mystery, no legible text."),
 ("tunnel","Dark abandoned underground subway railway tunnel, wet tracks disappearing into deep shadows, a single distant amber lamp, fog and old cables, realistic cinematic photoart, no people, no text."),
 ("control-room","Small old underground metro control room with analog wall clock and unused switches, single amber desk lamp, shadows through glass into a dark platform, realistic moody photorealistic art."),
 ("morning","At blue morning dawn the locked old entrance of a mysterious underground city station, empty wet square, pale light after rain, quiet eerie resolution, realistic cinematic wide shot, no text.")
]
def cf_image(prompt,env):
    boundary="----zenklein20261122a54d"
    parts=[]
    for name,value in (("prompt",prompt),("width","1024"),("height","768")):
        parts.append(("--"+boundary+"\r\nContent-Disposition: form-data; name=\""+name+"\"\r\n\r\n"+value+"\r\n").encode("utf8"))
    parts.append(("--"+boundary+"--\r\n").encode())
    target=("https://api.cloudflare.com/client/v4/accounts/"+env["CLOUDFLARE_ACCOUNT_ID"]+
        "/ai/run/"+MODEL)
    req=Request(target,data=b"".join(parts),headers={
      "Authorization":"Bearer "+env["CLOUDFLARE_API_TOKEN"],
      "Content-Type":"multipart/form-data; boundary="+boundary,
      "Accept":"application/json",
      "User-Agent":"ZenEditorialAutonomousKlein/1.0"},method="POST")
    try:
        with urlopen(req,timeout=120) as r:
            body=r.read(7000000)
            if len(body)>=7000000:raise ValueError("image_response_oversize")
            typ=r.headers.get("Content-Type","")
    except HTTPError as e:
        code=None
        try:
            blob=json.loads(e.read(4000))
            errs=blob.get("errors") or []
            code=(errs[0] or {}).get("code") if errs else None
        except (ValueError,TypeError,AttributeError):pass
        err=RuntimeError("cf_http_"+str(e.code)+"_provider_"+str(code))
        err.http_status=e.code
        err.provider_code=code
        raise err
    if body.startswith(b"\x89PNG") or body.startswith(b"\xff\xd8"):
        return body
    obj=json.loads(body)
    if not obj.get("success",True):raise ValueError("cloudflare_success_false")
    payload=obj.get("result") or obj
    if isinstance(payload,dict):
        img=payload.get("image")
        if isinstance(img,str):return base64.b64decode(img,validate=True)
    raise ValueError("cloudflare_image_missing_or_bad_response")
def image_check(raw,existing):
    Image.MAX_IMAGE_PIXELS=10000000
    img=Image.open(io.BytesIO(raw))
    img.verify()
    img=Image.open(io.BytesIO(raw)).convert("RGB")
    if min(img.size)<512:raise ValueError("too_small")
    small=ImageOps.grayscale(img).resize((8,8))
    pixels=list(small.getdata());mean=sum(pixels)/len(pixels)
    phash=sum(1<<i for i,v in enumerate(pixels) if v>=mean)
    if any((phash^a).bit_count()<7 for a in existing):raise ValueError("perceptually_similar")
    return img,phash
def build_docs(text,pictures,folder):
    doc=Document()
    doc.add_heading(base.TITLE,0)
    html=["<!doctype html><html lang='ru'><meta charset='utf-8'><title>Пилот для согласования</title><body>"]
    html.append("<h1>"+html.escape(base.TITLE)+"</h1>")
    idx=0
    for piece in re.split(r"(?m)^(\*\*[^*\n]+\*\*)\s*$",text):
        if not piece.strip():continue
        if piece.startswith("**") and piece.endswith("**"):
            title=piece[2:-2]
            doc.add_heading(title,level=2)
            html.append("<h2>"+html.escape(title)+"</h2>")
            if idx<len(pictures):
                name=pictures[idx]["file"]
                doc.add_picture(str(folder/name),width=Inches(5.9))
                html.append('<img src="images/'+name+'" alt="Иллюстрация '+str(idx+1)+'">')
            idx+=1
        else:
            for para in re.split(r"\n\s*\n",piece.strip()):
                if para.strip():
                    doc.add_paragraph(para.strip())
                    html.append("<p>"+para.strip().replace("\n"," ")+"</p>")
    for item in pictures[idx:]:
        doc.add_picture(str(folder/item["file"]),width=Inches(5.9))
        html.append('<img src="images/'+item["file"]+'">')
    html.append("</body></html>")
    doc.save(folder.parent/"illustrated_article.docx")
    (folder.parent/"illustrated_article.html").write_text("\n".join(html),encoding="utf8")
def main():
    env=dict(os.environ)
    work=Path(env.get("RUNNER_TEMP","/tmp"))/"zen-klein-real-test"
    images=work/"images"
    images.mkdir(parents=True,exist_ok=True)
    report={"schema":"zen-one-mystery-autonomous-klein-v1","channel":"legends",
      "topic":base.TITLE,"image_model":MODEL,"publication_authorized":False,
      "gemini_calls":0,"cloudflare_text_calls":0,"paid_api_calls_authorized":False,
      "owner_approval":False,"image_stages":[],"status":"starting",
      "images_generation_by":"github_actions_cloudflare_klein_no_chatgpt"}
    def save(): (work/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    if any(not env.get(k) for k in ("CLOUDFLARE_ACCOUNT_ID","CLOUDFLARE_API_TOKEN","GROQ_API_KEY","OPENROUTER_API_KEY","ZEN_REVIEW_PUBLIC_KEY_HEX")):
        report["status"]="blocked_missing_credentials";save();return report
    hashes=[]
    def render(idx,label,prompt):
        try:
            raw=cf_image(prompt,env)
            picture,phash=image_check(raw,hashes)
            hashes.append(phash)
            name=f"{idx:02d}-{label}.jpg"
            path=images/name;picture.save(path,quality=88,optimize=True)
            info={"number":idx,"name":label,"file":name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
              "status":"generated","width":picture.width,"height":picture.height,
              "provider":"Cloudflare Workers AI","model":MODEL,"prompt":prompt,
              "rights_status":"review_needed"}
            report["image_stages"].append({k:v for k,v in info.items() if k not in ("prompt",)})
            return info
        except (HTTPError,URLError,TimeoutError,ValueError,OSError,RuntimeError) as exc:
            msg=str(exc)
            report["image_stages"].append({"number":idx,"name":label,"status":"failed",
              "error_type":type(exc).__name__,"error_code":msg if msg.startswith("cf_http_") else None})
            return None
    # One minimal image request tests whether account is eligible and has available neurons.
    cover=render(1,*IMAGE_BRIEFS[0])
    if not cover:
        report["status"]="image_preflight_blocked_no_text_tokens_spent";save();return report
    # Reuse the existing real Groq -> OpenRouter -> four-detector code.
    result=base.main()
    report["text_pilot"]={k:v for k,v in result.items() if k not in ("stages",)}
    report["text_stages"]=result.get("stages",[])
    if result.get("status")!="prepublication_review_required" or not result.get("selected_text_hash"):
        report["status"]="text_stage_failed_no_more_images";save();return report
    # Read the exact editor-selected text, not a lossy HTML reconstruction.
    article=Path(env.get("RUNNER_TEMP","/tmp"))/"zen-article-full-pilot"/"selected_final.md"
    if not article.is_file():
        report["status"]="exact_owner_review_text_missing";save();return report
    prose=article.read_text(encoding="utf8")
    current_sha=hashlib.sha256(prose.encode()).hexdigest()
    if current_sha != result["selected_text_hash"]:
        report["status"]="final_text_sha_mismatch";save();return report
    (work/"final.md").write_text(prose,encoding="utf8")
    report["final_text_sha256"]=current_sha
    pictures=[cover]
    for index,(label,prompt) in enumerate(IMAGE_BRIEFS[1:],start=2):
        info=render(index,label,prompt)
        if not info:
            report["status"]="partial_images_generated_stopped";save();break
        pictures.append(info)
    (work/"image-manifest.json").write_text(json.dumps(pictures,ensure_ascii=False,indent=2),encoding="utf8")
    if len(pictures)==7:
        build_docs(prose,pictures,images)
        report["status"]="full_pipeline_generated_owner_review_pending"
        report["docx_generated"]=True
        report["html_generated"]=True
    report["images_created"]=len(pictures)
    report["image_rights_reviewed"]=False
    report["independent_scene_semantic_check"]="not_completed"
    report["owner_approval"]=False
    save()
    packet=io.BytesIO()
    with zipfile.ZipFile(packet,"w",zipfile.ZIP_DEFLATED) as z:
        for file in work.rglob("*"):
            if file.is_file() and file.name!="owner-package.enc":
                z.write(file,arcname=str(file.relative_to(work)))
    (work/"owner-package.enc").write_bytes(encrypt(packet.getvalue(),env["ZEN_REVIEW_PUBLIC_KEY_HEX"]))
    # Readable handoff goes ONLY to a one-day GitHub Actions artifact; never git or Pages.
    if report.get("docx_generated") and report.get("html_generated"):
        readable=work/"owner-review-readable.zip"
        with zipfile.ZipFile(readable,"w",zipfile.ZIP_DEFLATED) as z:
            for p in (work/"final.md",work/"illustrated_article.docx",
                      work/"illustrated_article.html",work/"image-manifest.json",
                      work/"report.json"):
                z.write(p,arcname=p.name)
            for p in sorted(images.glob("*.jpg")):
                z.write(p,arcname="images/"+p.name)
        report["reviewable_readable_file"]=True
        save()
    return report
if __name__=="__main__":
    result=main()
    print("ZEN_KLEIN_AUTONOMOUS_REPORT",json.dumps(result,ensure_ascii=False))
