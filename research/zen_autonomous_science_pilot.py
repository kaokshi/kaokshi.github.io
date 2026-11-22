#!/usr/bin/env python3
"""One bounded REAL autonomous science editorial production test (NO publishing).

NASA library imagery is sourced and downloaded BY THIS AGENT, not by ChatGPT.
Groq writes, verified-free OpenRouter Nemotron fully edits, mechanics gates,
one optional targeted Groq correction, final text SHA AI/style diagnostics, DOCX,
HTML, rights/provenance manifests and an owner-review ZIP. Incomplete is not pass.
"""
from __future__ import annotations
import hashlib,html,io,json,os,re,sys,time,zipfile
from pathlib import Path
from urllib.parse import urlencode,quote,urlparse
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research"),str(ROOT/"src")]
from editorial_reader_quality import text_stats,quality_flags
from editorial_ai_check import evaluate
from editorial_quality import text_style
from docx import Document
from docx.shared import Inches
from PIL import Image,ImageOps

TITLE="Почему полярное сияние бывает красным, а не только зелёным"
CHANNEL="science"
MODEL="nvidia/nemotron-3-ultra-550b-a55b:free"
SYSTEM="Русский научно-популярный журналист: убедительный, точный и ясный язык, без выдуманных фактов, цитат или источников."
SOURCES=[
 {"name":"NASA: Aurora","url":"https://science.nasa.gov/sun/auroras/"},
 {"name":"NOAA Space Weather Prediction Center: Aurora","url":"https://www.swpc.noaa.gov/phenomena/aurora"},
 {"name":"NASA media usage guidelines","url":"https://www.nasa.gov/nasa-brand-center/images-and-media/"},
]
def req(url,data=None,auth=None,timeout=35,max_bytes=2500000):
    headers={"User-Agent":"ZenEditorialOwnerPilot/1.0","Accept":"application/json, image/jpeg, */*"}
    if auth:headers["Authorization"]="Bearer "+auth
    if data is not None:
        headers["Content-Type"]="application/json"
        data=json.dumps(data,ensure_ascii=False).encode("utf8")
    r=Request(url,data=data,headers=headers,method="POST" if data else "GET")
    with urlopen(r,timeout=timeout) as f:
        data=f.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError("response_too_large")
        return data
def json_get(url,**kw):return json.loads(req(url,**kw))
def validate_source_urls():
    # Existence and provenance only. Semantic checks remain separate/human.
    ans=[]
    for src in SOURCES:
        if "media" in src["name"].lower():continue
        try:
            r=Request(src["url"],headers={"User-Agent":"ZenEditorialOwnerPilot/1.0"})
            with urlopen(r,timeout=16) as f:code=f.status
            ans.append({"url":src["url"],"status":"accessible" if code==200 else "http_non_200","http_status":code})
        except (HTTPError,URLError,OSError,TimeoutError) as e:
            ans.append({"url":src["url"],"status":"unavailable","error_type":type(e).__name__})
    return ans
def get_pictures(folder):
    """NASA Library metadata and bytes, unique IDs, hashes and visually distinct results."""
    searches=("aurora borealis","red aurora","aurora from space","aurora australis")
    from PIL import ImageStat
    folder.mkdir(parents=True,exist_ok=True)
    found=[];seen_ids=set();seen_sha=set();seen_hashes=[]
    failures=[]
    for query in searches:
        if len(found)>=8:break
        try:
            url="https://images-api.nasa.gov/search?"+urlencode({"q":query,"media_type":"image","page_size":"30"})
            js=json_get(url,timeout=22)
            items=js.get("collection",{}).get("items",[])
        except (ValueError,URLError,HTTPError,TimeoutError,OSError) as e:
            failures.append({"query":query,"error_type":type(e).__name__});continue
        for item in items:
            if len(found)>=8:break
            meta=(item.get("data") or [{}])[0]
            ident=meta.get("nasa_id","")
            title=meta.get("title","")
            desc=meta.get("description","") or ""
            if not ident or ident in seen_ids:continue
            if not re.search("aurora|polar light|northern light",title+" "+desc[:350],re.I):continue
            # Filter third-party copyright notes; no automatic rights approval.
            copyright_evidence=(meta.get("copyright","") or "")+" "+(meta.get("secondary_creator","") or "")
            if re.search("©|copyright|all rights reserved",copyright_evidence,re.I):continue
            links=item.get("links") or []
            urls=[l.get("href","") for l in links if l.get("rel") in ("preview","alternate") or l.get("render")=="image"]
            if not urls:continue
            u=next((u for u in urls if re.search(r"\.(jpg|jpeg|png)(\?|$)",u,re.I)),urls[0])
            if not u.startswith("https://") or urlparse(u).hostname not in ("images-assets.nasa.gov","images.nasa.gov","images-api.nasa.gov"):continue
            try:
                raw=req(u,timeout=22,max_bytes=4000000)
                pic=Image.open(io.BytesIO(raw));pic.verify()
                pic=Image.open(io.BytesIO(raw)).convert("RGB")
                if min(pic.size)<380:continue
                pix=ImageOps.grayscale(pic).resize((8,8))
                vals=list(pix.getdata());avg=sum(vals)/64
                phash=sum((1<<i) for i,v in enumerate(vals) if v>avg)
                if any((phash^x).bit_count()<8 for x in seen_hashes):continue
                digest=hashlib.sha256(raw).hexdigest()
                if digest in seen_sha:continue
                pic.thumbnail((1400,1100))
                path=folder/f"{len(found)+1:02d}.jpg"
                pic.save(path,"JPEG",quality=86,optimize=True)
                seen_ids.add(ident);seen_sha.add(digest);seen_hashes.append(phash)
                found.append({"file":path.name,"title":title[:160],"nasa_id":ident,"source_page":"https://images.nasa.gov/details/"+quote(ident),
                   "api_metadata":url,"asset_url":u,"creator":meta.get("photographer") or meta.get("center") or "NASA archive (specific creator unverified)",
                   "original_sha256":digest,"download_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                   "width":pic.width,"height":pic.height,
                   "rights_status":"manual_review_required","rights_basis":"NASA media generally available, individual third-party rights not independently established"})
            except (ValueError,HTTPError,URLError,OSError,TimeoutError,Exception) as e:
                failures.append({"stage":"image_download","error_type":type(e).__name__})
                continue
    return found,failures[:12]
def article_stats(t):
    s=text_stats(t,CHANNEL)
    return {"length_without_whitespace":s["chars_without_whitespace"],"bold_headers":s["bold_section_headers"],
        "cta":s["subscription_cta_at_end"],"repeat_fivegrams":s["repeated_fivegrams"],
        "flags":quality_flags(s)}
def model_api(url,token,model,prompt,max_output):
    data={"model":model,"temperature":.4,
        **({"max_completion_tokens":max_output} if "groq.com" in url else {"max_tokens":max_output}),
        "messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]}
    rsp=json_get(url,data=data,auth=token,timeout=165)
    body=((rsp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not isinstance(body,str) or not body.strip():raise ValueError("empty_visible_content")
    return body,rsp.get("usage") or {}
def article_html(text,pictures):
    pieces=[]
    blocks=re.split(r"(?m)^(\*\*[^*\n]+\*\*)\s*$",text)
    sections=[]
    for b in blocks:
        if not b.strip():continue
        if b.startswith("**") and b.endswith("**"):
            sections.append(("heading",b[2:-2]))
        else:
            for para in re.split(r"\n\s*\n",b):
                if para.strip():sections.append(("text",para.strip()))
    count=0
    for kind,s in sections:
        if kind=="heading":
            count+=1
            if count<=len(pictures):
                p=pictures[count-1]
                pieces.append(f'<figure><img src="images/{p["file"]}" alt="{html.escape(p["title"])}"></figure>')
            pieces.append("<h2>"+html.escape(s)+"</h2>")
        else:pieces.append("<p>"+html.escape(s).replace("\n"," ")+"</p>")
    if len(pictures)>count:
        for p in pictures[count:]:
            pieces.append(f'<figure><img src="images/{p["file"]}" alt="{html.escape(p["title"])}"></figure>')
    return "<!doctype html><html lang='ru'><meta charset='utf-8'><title>"+html.escape(TITLE)+"</title><body><h1>"+html.escape(TITLE)+"</h1>"+"\n".join(pieces)+"</body></html>"
def docx(text,pictures,dest):
    doc=Document();doc.add_heading(TITLE,0)
    blocks=re.split(r"(?m)^(\*\*[^*\n]+\*\*)\s*$",text)
    count=0
    for block in blocks:
        if not block.strip():continue
        if block.startswith("**") and block.endswith("**"):
            if count<len(pictures):
                try:doc.add_picture(str(dest.parent/"images"/pictures[count]["file"]),width=Inches(5.7))
                except (OSError,ValueError):pass
            doc.add_heading(block[2:-2],level=2);count+=1
        else:
            for para in re.split(r"\n\s*\n",block):
                if para.strip():doc.add_paragraph(para.strip())
    for p in pictures[count:]:
        try:doc.add_picture(str(dest.parent/"images"/p["file"]),width=Inches(5.7))
        except (OSError,ValueError):pass
    doc.save(dest)
def four_detectors(text,folder):
    article={"slug":"science-aurora-autonomous-pilot-20261122","channel":CHANNEL,"title":TITLE,
      "body_html":"<p>"+html.escape(text).replace("\n","</p><p>")+"</p>",
      "status":"draft","ai_reviewed":False,"publication_authorized":False}
    p=ROOT/"editorial-private-diagnostics"/"autonomous-test-science.json"
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(article,ensure_ascii=False),encoding="utf8")
    try:
        data=evaluate(p)
        checks={}
        for name,d in data.get("checks",{}).items():
            val=d.get("data") or {}
            checks[name]={"status":d.get("status"),"score":val.get("score_percent") if name!="ai_accent_detector" else val.get("ai_accent_score"),"error":str(d.get("reason",""))[:140] if d.get("status")!="completed" else None}
        return {"checks":checks,"all_completed":data.get("all_checks_completed"),"sha256":hashlib.sha256(text.encode()).hexdigest(),
          "model_a_index":data.get("editorial_threshold",{}).get("model_a_index_0_100"),
          "not_authorship_proof":True}
    except Exception as e:
        return {"status":"incomplete","error_type":type(e).__name__}
def main():
    folder=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-autonomous-complete"
    folder.mkdir(parents=True,exist_ok=True)
    results={"schema":"zen-autonomous-science-pilot-v1","title":TITLE,"channel":CHANNEL,
      "gemini_calls":0,"cloudflare_calls":0,"paid_calls":0,"publication_authorized":False,
      "stages":[],"human_editor_approved":False,"images_provenance_verified":False,
      "final_ready_for_publication":False}
    def save(): (folder/"report.json").write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    imgs,errors=get_pictures(folder/"images")
    results["image_pipeline"]={"method":"NASA images library search/download by Github agent",
      "count":len(imgs),"needed":7,"failures":errors,"source_manifest":"image_sources.json",
      "rights_manual_approval_needed":True}
    (folder/"image_sources.json").write_text(json.dumps(imgs,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    save()
    if len(imgs)<7:
        results["status"]="blocked_not_enough_verified_source_images";save();return results
    results["source_links"]=validate_source_urls()
    required=("GROQ_API_KEY","OPENROUTER_API_KEY")
    if any(not os.getenv(k) for k in required):
        results["status"]="blocked_model_secrets_missing";save();return results
    try:
        cat=json_get("https://openrouter.ai/api/v1/models",auth=os.environ["OPENROUTER_API_KEY"],timeout=30)
        if not any(m.get("id")==MODEL and all(str((m.get("pricing") or {}).get(k)) in ("0","0.0","0.00") for k in ("prompt","completion")) for m in cat.get("data",[])):
            results["status"]="blocked_not_free_openrouter";save();return results
    except Exception as e:
        results["status"]="blocked_openrouter_price_check";results["preflight_error"]=type(e).__name__;save();return results
    def call(stage,provider,model,prompt,budget):
        tm=time.monotonic()
        try:
            url="https://api.groq.com/openai/v1/chat/completions" if provider=="groq" else "https://openrouter.ai/api/v1/chat/completions"
            out,usage=model_api(url,os.environ["GROQ_API_KEY" if provider=="groq" else "OPENROUTER_API_KEY"],model,prompt,budget)
            (folder/(stage+".md")).write_text(out,encoding="utf8")
            results["stages"].append({"name":stage,"provider":provider,"status":"completed",
              "elapsed":round(time.monotonic()-tm,2),"total_tokens":usage.get("total_tokens"),
              "sha256":hashlib.sha256(out.encode()).hexdigest(),**article_stats(out)})
            save();return out
        except Exception as e:
            results["stages"].append({"name":stage,"provider":provider,"status":"failed",
                "error_type":type(e).__name__,"http_status":e.code if isinstance(e,HTTPError) else None});save();return None
    prompt=(f"Напиши новую оригинальную научно-популярную статью «{TITLE}» для канала «За гранью очевидного». "
      "По проверяемым данным NASA и NOAA объясни, как солнечный ветер, магнитосфера, столкновения частиц "
      "и кислород/азот вызывают северные/южные сияния, чем различаются зелёное и красное свечение, "
      "почему цвет зависит от высоты и состояния разреженной атмосферы, и почему фотографии с длинной выдержкой "
      "не всегда соответствуют восприятию глаза. Не выдумывай точных высот, цитат и статистики, если не уверен. "
      "Ссылки на источники: "+", ".join(x["url"] for x in SOURCES[:2])+". "
      "Около 4200–5100 символов без пробелов, допустимый диапазон 3000–6000. "
      "5–7 разделов, каждый начинается отдельным **жирным заголовком** с первой строки; "
      "конкретика, объяснения и связь с изображениями сияния, без поверхностной воды; "
      "в конце естественная фраза подписаться на «За гранью очевидного». Только статья.")
    source=call("01_groq_author","groq","openai/gpt-oss-120b",prompt,4900)
    if not source:results["status"]="author_failed";save();return results
    edit=("Ты независимый редактор. Верни ПОЛНУЮ исправленную версию текста, не обзор и не список замечаний. "
      "Сохрани подтверждённую физику и научные оговорки, убери необоснованные подробности, ошибки, повторения. "
      "Не добавляй неподтвержденных чисел/исследований/цитат. "
      "Обязательный диапазон 3000–6000 символов без пробелов, целевой 4200–5100, не своди всё к 3000. "
      "5–7 разделов, первый начинается жирным заголовком, CTA о подписке в конце. "
      "Реальные ссылки NASA и NOAA: "+SOURCES[0]["url"]+" "+SOURCES[1]["url"]+
      "\nИСХОДНИК:\n"+source)
    revised=call("02_openrouter_full_editor","openrouter",MODEL,edit,5600)
    result=revised or source
    if article_stats(result)["flags"]:
        issues="; ".join(article_stats(result)["flags"])
        fixed=call("03_groq_targeted_revision","groq","openai/gpt-oss-120b",
          "Внеси только содержательные исправления отмеченных нарушений: "+issues+
          ". Не меняй проверяемые научные факты, не добавляй сомнительных цифр. "
          "Верни статью ПОЛНОСТЬЮ с 3000–6000 знаками без пробелов, 5–7 жирными заголовками и CTA.\n"+result,5500)
        if fixed and len(article_stats(fixed)["flags"])<len(article_stats(result)["flags"]):result=fixed
    (folder/"final.md").write_text(result,encoding="utf8")
    results["final_sha256"]=hashlib.sha256(result.encode()).hexdigest()
    results["final_text_checks"]=article_stats(result)
    results["four_detector_report"]=four_detectors(result,folder)
    try:results["style_checks"]=text_style(result)
    except Exception as e:results["style_checks"]={"status":"error","error_type":type(e).__name__}
    results["fact_check_status"]="source_links_tested_but_claim_by_claim_verification_not_complete"
    results["external_plagiarism_check"]="not_performed"
    (folder/"article.html").write_text(article_html(result,imgs),encoding="utf8")
    docx(result,imgs,folder/"article.docx")
    results["docx_built"]=(folder/"article.docx").exists()
    results["image_count_in_html"]=(folder/"article.html").read_text(encoding="utf8").count("<img ")
    results["status"]="prepared_for_owner_review_not_publishing"
    save()
    with zipfile.ZipFile(folder/"owner-review.zip","w",zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob("*"):
            if p.is_file() and p.name!="owner-review.zip":z.write(p,arcname=p.relative_to(folder))
    return results
if __name__=="__main__":
    report=main()
    print("ZEN_AUTONOMOUS_FINAL_REPORT="+json.dumps({
      k:v for k,v in report.items() if k not in ("style_checks",)},ensure_ascii=False))
