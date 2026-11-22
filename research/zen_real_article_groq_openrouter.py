#!/usr/bin/env python3
"""One real owner-encrypted Russian story, Groq author -> free Nemotron critic -> Groq revision.
Strict 3 calls max. No Gemini/Cloudflare, paid calls, publication or raw articles in public logs.
"""
import os,json,time,re,hashlib,io,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from zipfile import ZipFile,ZIP_DEFLATED
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_encrypted_review import encrypt
from diagnose_groq_api import diagnostic_error

TOPIC="Часы на закрытой водонапорной башне: кто переводил стрелки каждую ночь"
GROQ="openai/gpt-oss-120b"
CRITIC="nvidia/nemotron-3-ultra-550b-a55b:free"
SYSTEM=("Ты опытный редактор русскоязычного Дзена. Живой, конкретный и естественный русский язык без канцелярита, "
"навязчивого драматизма и шаблонных повторов. Не выдавай вымысел за документальные события.")
AUTHOR=("Напиши оригинальную мистическую историю для «Легенды тёмных улиц» под названием «"+TOPIC+"». "
"Это художественная выдумка, не утверждай, будто это установленный реальный случай. "
"От 3500 до 4700 символов БЕЗ пробелов, 5–7 смысловых разделов, каждый начинается с заголовка **жирным**. "
"Начни с конкретного события, добавь причинно связанные действия героя, убедительные подробности и завершённую развязку. "
"Без повторов сцен и одинаковых оборотов. В конце ненавязчиво позови подписаться. Верни только статью.")
def api(url,key,model,prompt,max_tokens):
    body={"model":model,"messages":[{"role":"system","content":SYSTEM},
      {"role":"user","content":prompt}],"temperature":0.55,**({"max_completion_tokens":max_tokens} if "groq.com" in url else {"max_tokens":max_tokens})}
    req=Request(url,data=json.dumps(body,ensure_ascii=False).encode(),
      headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","User-Agent":"ZenEditorialAPIDiagnostic/1.0"},method="POST")
    with urlopen(req,timeout=150) as resp:data=json.load(resp)
    choices=data.get("choices") or []
    if not choices:raise ValueError("empty_choices")
    content=(choices[0].get("message") or {}).get("content") or ""
    if not content.strip():raise ValueError("empty_visible_content")
    return content,data.get("usage") or {}
def metrics(text):
    paragraphs=[x.strip() for x in re.split(r"\n\s*\n",text.strip()) if x.strip()]
    headings=re.findall(r"(?m)^\*\*[^\n*]{3,110}\*\*\s*$",text)
    length=len(re.sub(r"\s+","",text))
    lines=text.splitlines()
    seen=set(); repetitions=0
    words=re.findall(r"[А-Яа-яЁё]+",text.lower())
    for i in range(len(words)-4):
        group=tuple(words[i:i+5])
        if group in seen:repetitions+=1
        seen.add(group)
    return {"sha256":hashlib.sha256(text.encode()).hexdigest(),"chars_no_spaces":length,
      "bold_headers":len(headings),"paragraphs":len(paragraphs),
      "repeated_fivegrams":repetitions,
      "cta_at_end":bool(re.search(r"подпис|канал",text[-380:],re.I)),
      "mechanical_pass":3000<=length<=6000 and 4<=len(headings)<=8 and repetitions<=3
                         and bool(re.search(r"подпис|канал",text[-380:],re.I))}
def main():
    out=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-real-article";out.mkdir(parents=True,exist_ok=True)
    r={"schema":"zen-groq-openrouter-real-article-v1","channel":"legends",
      "topic":TOPIC,"gemini_calls":0,"cloudflare_calls":0,"paid_calls":0,
      "publication_authorized":False,"stages":[],"final_editorial_ready":False,
      "fact_check_completed":False,"image_rights_checked":False,"human_review_completed":False}
    env=os.environ
    if not all(env.get(k) for k in ("GROQ_API_KEY","OPENROUTER_API_KEY","ZEN_REVIEW_PUBLIC_KEY_HEX")):
        r["status"]="missing_secret";return r
    try:
        req=Request("https://openrouter.ai/api/v1/models",headers={"Authorization":"Bearer "+env["OPENROUTER_API_KEY"]})
        with urlopen(req,timeout=25) as resp:catalog=json.load(resp)
        allowed=any(m.get("id")==CRITIC and str((m.get("pricing") or {}).get("prompt")) in ("0","0.0","0.00")
          and str((m.get("pricing") or {}).get("completion")) in ("0","0.0","0.00") for m in catalog.get("data",[]))
        if not allowed:r["status"]="free_critic_unavailable";return r
    except (HTTPError,URLError,ValueError,TimeoutError,OSError) as e:
        r["status"]="catalog_failed";r["error_type"]=type(e).__name__;return r
    originals={}
    def stage(name,provider,url,key,model,prompt,tokens):
        start=time.monotonic()
        try:
            text,usage=api(url,key,model,prompt,tokens)
            originals[name]=text
            d={"stage":name,"provider":provider,"status":"responded","elapsed_seconds":round(time.monotonic()-start,2),
               "usage_total_tokens":usage.get("total_tokens"),"content_sha256":hashlib.sha256(text.encode()).hexdigest()}
            if name!="critic":d["article_metrics"]=metrics(text)
            r["stages"].append(d);return text
        except (HTTPError,URLError,ValueError,TimeoutError,OSError,KeyError,TypeError) as e:
            r["stages"].append({"stage":name,"provider":provider,"status":"failed",
                "error_type":type(e).__name__,"http_status":e.code if isinstance(e,HTTPError) else None,
                **({"safe_diagnostic":diagnostic_error(e)} if isinstance(e,HTTPError) and provider=="groq" else {})})
            return None
    draft=stage("author","groq","https://api.groq.com/openai/v1/chat/completions",
      env["GROQ_API_KEY"],GROQ,AUTHOR,3200)
    if draft:
        review=stage("critic","openrouter","https://openrouter.ai/api/v1/chat/completions",
          env["OPENROUTER_API_KEY"],CRITIC,
          "Ты независимый литературный редактор. Проверь именно этот рассказ. Найди 3–5 КОНКРЕТНЫХ недостатков: логика, повторения, причинность событий, слабое начало, слабая развязка. "
          "Обязательно отметь, если недостатков нет. Не выдумывай проверку источников и прав на изображения. "
          "Ответь компактным редакторским разбором по-русски, не переписывай статью.\n"+draft,1350)
        # One real revision when critical feedback exists, never more than two author calls.
        if review:
            final=stage("revision","groq","https://api.groq.com/openai/v1/chat/completions",
                env["GROQ_API_KEY"],GROQ,
                "Внеси одну содержательную редакторскую доработку по конкретным замечаниям независимого редактора. "
                "Сохрани оригинальный сюжет и не выдумывай факты или документальные источники. "
                "Строго от 3500 до 4700 символов без пробелов, 5–7 жирных заголовков разделов, CTA в конце. "
                "Верни ПОЛНЫЙ улучшенный рассказ без пояснений.\nЗАМЕЧАНИЯ:\n"+review+"\nИСХОДНИК:\n"+draft,3600)
        else:final=draft
        r["selected_stage"]="revision" if originals.get("revision") else "author"
        r["format_pass"]=metrics(final)["mechanical_pass"] if final else False
        r["status"]="revised_needs_fact_image_owner_review" if originals.get("revision") else "draft_needs_manual_review"
    else:r["status"]="generation_failed"
    if originals:
        archive=io.BytesIO()
        with ZipFile(archive,"w",ZIP_DEFLATED) as z:
            for name,value in originals.items():z.writestr(name+".md",value)
            z.writestr("metrics.json",json.dumps(r,ensure_ascii=False,indent=2))
        (out/"owner-review.enc").write_bytes(encrypt(archive.getvalue(),env["ZEN_REVIEW_PUBLIC_KEY_HEX"]))
        r["encrypted_owner_packet"]=True
    else:r["encrypted_owner_packet"]=False
    return r
if __name__=="__main__":
    # Never log article or critique text; do not leave plaintext in repository.
    result=main()
    path=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-real-article"/"metrics.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False))
