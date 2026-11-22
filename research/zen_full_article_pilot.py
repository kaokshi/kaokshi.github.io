#!/usr/bin/env python3
"""Single real multi-stage editorial pilot. Owner encrypted, no publication or Gemini.
Groq author -> OpenRouter Nemotron full rewrite -> mechanical QA -> optional Groq repair,
then independent four-detector diagnostics for the immutable final version.
Images are handled in a separate licensed-photo assembly stage.
"""
import sys,os,json,re,hashlib,time,io,subprocess
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research"),str(ROOT/"src")]
from editorial_reader_quality import text_stats,quality_flags
from editorial_encrypted_review import encrypt
from editorial_ai_check import evaluate

CHANNEL="legends"
TITLE="В доме с заколоченным подъездом каждую ночь загоралось окно"
GROQ="openai/gpt-oss-120b"
OPENROUTER="nvidia/nemotron-3-ultra-550b-a55b:free"
SYSTEM="Русская художественная мистика. Пиши живо, без штампов, повторов и бессмысленной воды. Это вымышленный сюжет, не документальный случай."
BEATS=("Старый кирпичный дом с закрытым подъездом и одним освещённым окном; "
       "герой видит свет из окна на автобусной остановке; "
       "ржавую дверь и старую табличку у подъезда; "
       "письмо из домоуправления и план здания; "
       "узкий коридор с рассохшейся лестницей; "
       "освещённую комнату и часы у окна; "
       "рассвет и пустое окно после развязки.")
BRIEF=(f"Новая оригинальная мистическая история «{TITLE}» для «Легенды тёмных улиц». "
 "Главный герой — электрик, который получает сообщение о неисправности в давно закрытом подъезде. "
 "Построй связный, законченный сюжет с неочевидным, но логичным объяснением. "
 "Не используй распространённую развязку «всё приснилось» или «герой давно умер». "
 "Для возможности точных иллюстраций вплети в сюжет 7 зрительных эпизодов в таком порядке: "+BEATS+" "
 "История явно художественная выдумка, не выдавай её за реальный документальный случай. "
 "Пиши 4200–5200 символов БЕЗ ПРОБЕЛОВ, но всегда соблюдай редакционный коридор от 3000 до 6000, "
 "5–7 разделов; КАЖДЫЙ раздел начинается отдельной строкой **жирного заголовка**. "
 "Первая строка всей статьи тоже должна быть жирным заголовком. "
 "У каждого блока конкретные новые события; естественные абзацы. "
 "В последнем абзаце оригинально пригласи подписаться на «Легенды тёмных улиц». "
 "Верни только полный текст статьи, без инструкций и метакомментариев.")
def api(url,key,model,prompt,tokens,temperature=.45):
    data={"model":model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}],
          "temperature":temperature,**({"max_completion_tokens":tokens} if "groq.com" in url else {"max_tokens":tokens})}
    req=Request(url,data=json.dumps(data,ensure_ascii=False).encode(),
      headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","User-Agent":"ZenEditorialAPIDiagnostic/1.0"},method="POST")
    with urlopen(req,timeout=180) as response:value=json.load(response)
    out=((value.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not isinstance(out,str) or not out.strip():raise ValueError("empty_visible_content")
    return out,(value.get("usage") or {})
def uniqueness(text):
    target=set(re.findall(r"[а-яё]{4,}",text.lower()))
    hit=[]
    for p in (ROOT/"content"/"articles").glob("*.json"):
        try:
            d=json.loads(p.read_text(encoding="utf8"))
            title=d.get("title","")
            src=re.sub(r"<[^>]+>"," ",d.get("body_html",""))
            vocab=set(re.findall(r"[а-яё]{4,}",(title+" "+src).lower()))
            intersection=len(target & vocab)/max(1,len(target|vocab))
            if intersection>.25:hit.append({"slug":p.stem,"word_jaccard":round(intersection,3)})
        except (ValueError,OSError):continue
    return {"potentially_similar_existing_articles":sorted(hit,key=lambda x:-x["word_jaccard"])[:4],
       "approximate_word_overlap_only":True,"web_plagiarism_check":False}
def stats(t):
    s=text_stats(t,CHANNEL)
    return {"chars":s["chars_without_whitespace"],"headers":s["bold_section_headers"],
       "cta":s["subscription_cta_at_end"],"repeated_fivegrams":s["repeated_fivegrams"],
       "flags":quality_flags(s)}
def draft_status(text, stage):
    return {"stage":stage,"hash":hashlib.sha256(text.encode()).hexdigest(),**stats(text)}
def text_to_article_json(text):
    import html
    blocks=re.split(r"(?m)^(\*\*[^*\n]+\*\*)\s*$",text)
    body=[]
    for b in blocks:
        if not b.strip():continue
        if b.startswith("**") and b.endswith("**"):
            body.append("<h2>"+html.escape(b[2:-2])+"</h2>")
        else:
            for p in re.split(r"\n\s*\n",b):
                if p.strip():body.append("<p>"+html.escape(p.strip()).replace("\n"," ")+"</p>")
    return {"slug":"legends-single-full-pilot-20261122","channel":CHANNEL,"title":TITLE,
      "body_html":"\n".join(body),"status":"draft","publication_authorized":False,
      "editorial_reviewed":False,"rights_reviewed":False,"ai_reviewed":False,"ai_checks":[]}
def diagnostics(text,work):
    p=work/"article-diagnostic-only.json"
    p.write_text(json.dumps(text_to_article_json(text),ensure_ascii=False),encoding="utf8")
    try:
        full=evaluate(p)
        out={}
        for name,item in full.get("checks",{}).items():
            data=item.get("data") or {}
            out[name]={"status":item.get("status"),"index_0_100":data.get("score_percent") if name!="ai_accent_detector" else (
              round(100*data.get("ai_accent_score"),2) if isinstance(data.get("ai_accent_score"),(int,float)) else None),
              "error_type":item.get("reason","")[:80] if item.get("status")!="completed" else None}
        return {"final_text_sha256":hashlib.sha256(text.encode()).hexdigest(),
          "model_a_index":full.get("editorial_threshold",{}).get("model_a_index_0_100"),
          "checks":out,"all_completed":full.get("all_checks_completed",False),
          "author_origin_proven_by_detectors":False}
    except (Exception) as exc:
        return {"status":"incomplete","error_type":type(exc).__name__,
          "final_text_sha256":hashlib.sha256(text.encode()).hexdigest()}
def main():
    env=os.environ
    work=Path(env.get("RUNNER_TEMP","/tmp"))/"zen-article-full-pilot"
    work.mkdir(parents=True,exist_ok=True)
    result={"schema":"zen-full-article-chain-v1","title":TITLE,"channel":CHANNEL,
      "published":False,"gemini_calls":0,"cloudflare_calls":0,"paid_calls":0,
      "stages":[],"editorial_ready":False,"images_ready":False,
      "owner_approval":False,"facts_verified":False}
    if any(not env.get(s) for s in ("GROQ_API_KEY","OPENROUTER_API_KEY","ZEN_REVIEW_PUBLIC_KEY_HEX")):
        result["status"]="missing_credentials";return result
    try:
        req=Request("https://openrouter.ai/api/v1/models",headers={"Authorization":"Bearer "+env["OPENROUTER_API_KEY"]})
        with urlopen(req,timeout=25) as rsp:catalog=json.load(rsp)
        free=any(x.get("id")==OPENROUTER and all(str((x.get("pricing") or {}).get(k)) in ("0","0.0","0.00") for k in ("prompt","completion"))
                 for x in catalog.get("data",[]) if isinstance(x,dict))
        if not free:result["status"]="openrouter_free_model_unavailable";return result
    except (HTTPError,URLError,ValueError,OSError,TimeoutError):
        result["status"]="free_model_preflight_failed";return result
    files={}
    def call(stage,provider,model,prompt,tokens):
        now=time.monotonic()
        url="https://api.groq.com/openai/v1/chat/completions" if provider=="groq" else "https://openrouter.ai/api/v1/chat/completions"
        key=env["GROQ_API_KEY" if provider=="groq" else "OPENROUTER_API_KEY"]
        try:
            output,usage=api(url,key,model,prompt,tokens)
            files[stage+".md"]=output
            result["stages"].append({"stage":stage,"provider":provider,"status":"completed","seconds":round(time.monotonic()-now,2),
               "usage_total_tokens":usage.get("total_tokens"),**draft_status(output,stage)})
            return output
        except (HTTPError,URLError,ValueError,OSError,TypeError,TimeoutError) as exc:
            result["stages"].append({"stage":stage,"provider":provider,"status":"error",
              "http_status":exc.code if isinstance(exc,HTTPError) else None,"error_type":type(exc).__name__})
            return None
    original=call("groq_author","groq",GROQ,BRIEF,5500)
    if not original:result["status"]="author_failed";return result
    editor_prompt=("Отредактируй ПОЛНУЮ статью литературно и содержательно, а НЕ выдавай замечания. "
      "Сохрани сюжетные события, детали и причинно-следственные связи, исправь стиль, нелепости, повторения, начало и финал. "
      "Никаких придуманных реальных фактов или документов. "
      "Длина 3000–6000 символов БЕЗ ПРОБЕЛОВ, оптимально 4200–5200, не гонись за минимальными 3000. "
      "5–7 жирных заголовков, каждый раздел начинается с заголовка, включая первый. "
      "В самом конце оригинальное приглашение подписаться на канал «Легенды тёмных улиц». "
      "Верни только полную новую редакцию, без списка ошибок.\nИСХОДНЫЙ РАССКАЗ:\n"+original)
    edited=call("openrouter_full_edit","openrouter",OPENROUTER,editor_prompt,5700)
    chosen=edited or original
    result["editor_model_responded"]=bool(edited)
    if stats(chosen)["flags"]:
        issues=", ".join(stats(chosen)["flags"])
        fix_prompt=("Внеси ТОЛЬКО исправления перечисленных реальных нарушений: "+issues+". "
          "Верни ПОЛНЫЙ текст, сохрани сюжет, персонажей, новые обстоятельства и другие выполненные требования. "
          "Минимум 3000, максимум 6000 символов без пробелов, предпочтительно 4200–5200. "
          "5–7 жирных заголовков, первый блок начинается с заголовка, CTA подписаться в конце. "
          "Не добавляй повторы или пустое повествование.\nТЕКСТ:\n"+chosen)
        fixed=call("groq_targeted_fix","groq",GROQ,fix_prompt,5500)
        if fixed:
            if len(stats(fixed)["flags"]) <= len(stats(chosen)["flags"]):chosen=fixed
            else:result["rejected_worsening_fix"]=True
    result["selected_text_hash"]=hashlib.sha256(chosen.encode()).hexdigest()
    result["selected_mechanical_checks"]=stats(chosen)
    result["historical_uniqueness_proxy"]=uniqueness(chosen)
    result["ai_diagnostics"]=diagnostics(chosen,work)
    result["status"]="prepublication_review_required"
    result["editorial_ready"]=False
    # Package owner's originals, amendments, and the exact final version.
    files["selected_final.md"]=chosen
    files["report.json"]=json.dumps(result,ensure_ascii=False,indent=2)
    archive=io.BytesIO()
    with ZipFile(archive,"w",ZIP_DEFLATED) as z:
        for name,content in files.items():z.writestr(name,content)
    packet=work/"owner-editorial-versions.enc"
    packet.write_bytes(encrypt(archive.getvalue(),env["ZEN_REVIEW_PUBLIC_KEY_HEX"]))
    result["owner_encrypted_versions"]=True
    return result
if __name__=="__main__":
    result=main()
    directory=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-article-full-pilot"
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"metrics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("FULL_PILOT_METRICS",json.dumps(result,ensure_ascii=False))
