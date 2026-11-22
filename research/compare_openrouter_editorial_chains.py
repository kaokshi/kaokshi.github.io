#!/usr/bin/env python3
"""Bounded real editorial comparison of Groq, Cloudflare and OpenRouter free models.

Strictly zero paid model calls, zero publication, no text in public logs.
Tests three channel briefs, two editorial directions and free-model critics.
All full articles/reviews are encrypted for the owner.
"""
from __future__ import annotations
import io,json,os,sys
from pathlib import Path
from hashlib import sha256
from time import monotonic,sleep
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from zipfile import ZipFile,ZIP_DEFLATED
sys.path.insert(0,str(Path(__file__).resolve().parent))
from compare_editorial_models import TASKS, CHANNEL_NAMES, SYSTEM, generate, http_json
from cloudflare_model_linked_pilot import MODELS,call_cf,record
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_encrypted_review import encrypt

ROUTER_MODELS={
 "nemotron_ultra":"nvidia/nemotron-3-ultra-550b-a55b:free",
 "nemotron_lightning":"nvidia/nemotron-3.5-lightning:free",
}
CF_MODEL=MODELS["gemma4"]
MAX={"openrouter":24,"groq":6,"cloudflare":6}
REQUIRED=("OPENROUTER_API_KEY","GROQ_API_KEY","CLOUDFLARE_API_TOKEN","CLOUDFLARE_ACCOUNT_ID")
CATALOG="https://openrouter.ai/api/v1/models"
CHAT="https://openrouter.ai/api/v1/chat/completions"

def only_free_model_ids(env,opener=urlopen):
    key=env["OPENROUTER_API_KEY"]
    req=Request(CATALOG,headers={"Authorization":"Bearer "+key,"Accept":"application/json",
       "User-Agent":"ZenEditorialFreeModelsPilot/1.0"})
    with opener(req,timeout=40) as resp:
        data=json.load(resp)
    matched={}
    for item in data.get("data",[]):
        if not isinstance(item,dict):continue
        name=item.get("id","")
        price=item.get("pricing") or {}
        if name in ROUTER_MODELS.values() and name.endswith(":free") and (
              str(price.get("prompt","")).strip() in ("0","0.0","0.00") and
              str(price.get("completion","")).strip() in ("0","0.0","0.00")):
            matched[name]=True
    return set(matched)

def router_call(model,prompt,env,*,max_tokens=2800,temperature=0.4):
    if not model.endswith(":free"):
        raise ValueError("nonfree_model_forbidden")
    response=http_json(CHAT,{"model":model,"temperature":temperature,
        "max_tokens":max_tokens,
        "messages":[{"role":"system","content":"Отвечай только по-русски. Не выдумывай фактов. "+SYSTEM},
                    {"role":"user","content":prompt}]},
        {"Authorization":"Bearer "+env["OPENROUTER_API_KEY"]},timeout=150)
    choices=response.get("choices") or []
    if not choices:raise ValueError("empty_choices")
    content=choices[0].get("message",{}).get("content","")
    if not isinstance(content,str) or not content.strip():raise ValueError("empty_content")
    return content,response.get("usage",{})

def do_pilot(root,env=None,*,router=router_call,groq=None,cf=None,preflight=only_free_model_ids,pause=sleep):
    env=dict(os.environ if env is None else env)
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    report={"schema":"zen-3-provider-free-connection-comparison-v1",
      "publication_authorized":False,"paid_calls_authorized":False,
      "gemini_calls":0,"limits":MAX,
      "calls":{k:0 for k in MAX},"channels":[],
      "fact_check_completed":False,"human_review_completed":False}
    if any(not env.get(k) for k in REQUIRED):
        report["status"]="blocked_missing_secret"
        report["missing_secret_names"]=[k for k in REQUIRED if not env.get(k)]
        return report
    try:
        free=preflight(env)
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError) as exc:
        report["status"]="openrouter_preflight_failed"
        report["http_status"]=exc.code if isinstance(exc,HTTPError) else None
        return report
    accepted={nick:model for nick,model in ROUTER_MODELS.items() if model in free}
    report["verified_free_models"]=accepted
    if not accepted:
        report["status"]="no_verified_free_models"
        return report
    groq=groq or (lambda prompt:generate("groq",prompt,env=env))
    cf=cf or (lambda prompt:call_cf(CF_MODEL,prompt,env))
    articles={}
    critiques={}
    def run_step(channel,step,provider,call,is_review=False):
        if report["calls"][provider]>=MAX[provider]:
            return None,{"step":step,"status":"skipped_budget"}
        report["calls"][provider]+=1
        started=monotonic()
        try:
            out,_=call()
            if not isinstance(out,str) or not out.strip():
                raise ValueError("empty_model_response")
            filename=channel+"--"+step+(".json" if is_review else ".md")
            output=root/filename;output.write_text(out,encoding="utf-8");output.chmod(0o600)
            if is_review:
                # Critic output is encrypted like articles; never public-review-score.
                detail={"step":step,"status":"generated","provider":provider,
                  "seconds":round(monotonic()-started,2),
                  "sha256":sha256(out.encode()).hexdigest(),
                  "comments_encrypted":True}
            else:
                detail={"step":step,"status":"generated","provider":provider,
                        **record(out,channel,monotonic()-started)}
                articles[(channel,step)]=out
            return out,detail
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError,KeyError) as exc:
            return None,{"step":step,"status":"failed","provider":provider,
                  "error_type":type(exc).__name__,
                  "http_status":exc.code if isinstance(exc,HTTPError) else None}
    for channel,task in TASKS.items():
        row={"channel":channel,"stages":[]}
        basic="Канал: "+CHANNEL_NAMES[channel]+". Заголовок: "+task["title"]+"\nЗадание: "+task["brief"]+"\n"+SYSTEM
        sources=[]
        for label,backend,fn in (
            ("groq-author","groq",lambda:groq(basic)),
            ("cloudflare-author","cloudflare",lambda:cf(basic)),
        ):
            draft,detail=run_step(channel,label,backend,fn)
            row["stages"].append(detail)
            if draft:sources.append((label,draft))
        if "nemotron_ultra" in accepted:
            model=accepted["nemotron_ultra"]
            draft,detail=run_step(channel,"nemotron-ultra-author","openrouter",
                              lambda:router(model,basic,env))
            row["stages"].append(detail)
            if draft:sources.append(("nemotron-ultra-author",draft))
        # Opposite directions: Groq -> Cloudflare, Cloudflare -> Groq,
        # OpenRouter Ultra -> Groq, OpenRouter Ultra -> Cloudflare.
        for name,draft in list(sources):
            editor_roles=(
                (("groq","groq",groq),) if name=="cloudflare-author" else
                (("cloudflare","cloudflare",cf),("groq","groq",groq))
                  if name=="nemotron-ultra-author" else
                (("cloudflare","cloudflare",cf),)
            )
            for suffix,provider,fn in editor_roles:
                prompt=("Отредактируй следующую статью для Дзена, сохрани все события "
                  "и подтверждённые факты; не сокращай её ниже 3000 символов без пробелов, "
                  "не добавляй выдуманные факты. Сделай 4–8 блоков с **жирными заголовками**, "
                  "закончить оригинальным призывом подписаться. "
                  "Верни полную статью. Исходное задание: "+task["brief"]+
                  "\nИсходная статья:\n"+draft)
                out,detail=run_step(channel,name+"-to-"+suffix,provider,
                             lambda fn=fn,prompt=prompt:fn(prompt))
                row["stages"].append(detail)
                if out:sources.append((name+"-to-"+suffix,out))
        # Two independent free critics on each base author version and on edits.
        # At most 24 OpenRouter calls shared; no exhaustive cartesian explosion.
        for name,draft in sources:
            if name not in ("groq-author","cloudflare-author","nemotron-ultra-author"):
                continue
            for critic in ("nemotron_ultra","nemotron_lightning"):
                if critic not in accepted:continue
                model=accepted[critic]
                if name.startswith("nemotron") and critic=="nemotron_ultra":
                    continue  # don't self-review own article with its own model
                critique_prompt=("Ты независимый литературный редактор, не переписывай статью. "
                    "Верни короткий JSON по-русски с ключами: проблемные_места, "
                    "риски_фактов, сильные_стороны, рекомендации. "
                    "Приводи конкретные проверяемые текстовые примеры. "
                    "Не утверждай, что факты проверены по источникам. "
                    "Канал "+CHANNEL_NAMES[channel]+". Ограничения "+task["brief"]+
                    "\nТЕКСТ ДЛЯ АНАЛИЗА:\n"+draft)
                _,detail=run_step(channel,name+"-critic-"+critic,"openrouter",
                        lambda model=model,prompt=critique_prompt:router(model,prompt,env,max_tokens=1100),
                        is_review=True)
                row["stages"].append(detail)
        report["channels"].append(row)
        pause(5)
    report["status"]="completed_with_partial_or_full_evidence"
    return report

def main():
    root=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-3provider-free-pilot"
    r=do_pilot(root)
    root.mkdir(parents=True,exist_ok=True)
    files=[p for p in root.iterdir() if p.suffix in (".md",".json") and p.name!="metrics.json"]
    pubkey=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    r["owner_encrypted_drafts"]=False
    if files and pubkey:
        zipdata=io.BytesIO()
        with ZipFile(zipdata,"w",ZIP_DEFLATED) as archive:
            for f in files:archive.write(f,arcname=f.name)
        encrypted=root/"owner-drafts-and-critiques.enc"
        encrypted.write_bytes(encrypt(zipdata.getvalue(),pubkey))
        encrypted.chmod(0o600)
        r["owner_encrypted_drafts"]=True
    (root/"metrics.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",
                                      encoding="utf-8")
    print(json.dumps({"status":r["status"],"calls":r["calls"],
      "free_models":r.get("verified_free_models"),
      "channels":[{"channel":ch["channel"],
         "stages":[{k:v for k,v in st.items() if k in
               ("step","status","provider","chars_without_whitespace",
                "mechanical_flags","seconds","http_status","error_type")}
                for st in ch["stages"]]} for ch in r.get("channels",[])]},
       ensure_ascii=False))
if __name__=="__main__":main()
