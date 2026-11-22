#!/usr/bin/env python3
"""Actual autonomous single-article chain with FLUX.2 Klein 4B inside GitHub Actions.

Cloudflare one-image preflight FIRST. A quota block stops expensive Groq/OpenRouter
calls rather than silently substituting ChatGPT images. If available: 7 distinct
images, Groq author, OpenRouter full rewrite, 4 local AI diagnostics, DOCX/HTML,
owner-encrypted archive, and honest non-publication QA report.
"""
import base64,hashlib,io,json,os,re,sys,zipfile
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
IMAGE_BRIEFS=[
 ("cover","Cinematic photorealistic editorial cover art. Wide shot of an abandoned five-floor brick apartment house after midnight, boarded entrance on the left, exactly one high window glowing warm amber, fog, rain-soaked pavement, lonely street light, no text, no watermark, 16:9 cinematic composition."),
 ("bus-stop","Cinematic photorealistic eerie Russian city street at dusk, empty wet bus stop facing an abandoned brick apartment house with one strangely lit window, cold teal shadows, no lettering, no watermark."),
 ("door","Close cinematic photo illustration of a heavy rusted padlocked entrance door in a long-abandoned brick apartment building, peeling paint, dim night street lamps, ominous but realistic, no writing."),
 ("blueprint","A worker's hands with an old paper building floor plan and flashlight on a scratched wooden desk, old technical pencil marks but NO legible writing, natural warm desk lighting, urban mystery photorealistic."),
 ("stairs","Cinematic photorealistic deserted decaying stairwell inside a boarded abandoned apartment building, cracked plaster, chipped concrete steps, dangling exposed wire, cold blue dusk from broken window, no people."),
 ("clock-room","Haunting cinematic realistic abandoned apartment room, single warm yellow table lamp near an old mechanical clock and lace-curtained window, dusty furniture, long shadows, mystery atmosphere."),
 ("dawn","Cinematic photoillustration of an old abandoned red brick apartment building with boarded entrance in blue early-morning fog, pale sky, window now dark, eerie stillness, no letters or signs.")
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
    html.append("<h1>"+base.TITLE+"</h1>")
    idx=0
    for piece in re.split(r"(?m)^(\*\*[^*\n]+\*\*)\s*$",text):
        if not piece.strip():continue
        if piece.startswith("**") and piece.endswith("**"):
            if idx<len(pictures):
                name=pictures[idx]["file"]
                doc.add_picture(str(folder/name),width=Inches(5.9))
                html.append('<img src="images/'+name+'" alt="Иллюстрация '+str(idx+1)+'">')
            title=piece[2:-2]
            doc.add_heading(title,level=2)
            html.append("<h2>"+title+"</h2>")
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
    # Extract the exact immutable editor-selected text from local private AI-report source.
    article=ROOT/"editorial-private-diagnostics"/"article-diagnostic-only.json"
    if not article.is_file():
        report["status"]="text_source_missing";save();return report
    payload=json.loads(article.read_text(encoding="utf8"))
    text=extract_text(payload.get("body_html",""))
    # Markdown structure comes from selected full text in body_html HTML headings.
    from html.parser import HTMLParser
    class Parser(HTMLParser):
        def __init__(self):super().__init__();self.parts=[];self.header=False
        def handle_starttag(self,tag,attrs):
            if tag=="h2":self.parts.append("\n\n**");self.header=True
            elif tag=="p":self.parts.append("\n\n")
        def handle_endtag(self,tag):
            if tag=="h2":self.parts.append("**\n");self.header=False
        def handle_data(self,data):self.parts.append(data)
    p=Parser();p.feed(payload["body_html"]);prose="".join(p.parts).strip()
    report["reconstructed_markdown_sha256"]=hashlib.sha256(prose.encode()).hexdigest()
    report["selected_sha256_from_model"]=result.get("selected_text_hash")
    report["text_sha_note"]="HTML extraction changes whitespace: compare normalized content, source full draft remains in owner-encrypted source packet"
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
    return report
if __name__=="__main__":
    result=main()
    print("ZEN_KLEIN_AUTONOMOUS_REPORT",json.dumps(result,ensure_ascii=False))
