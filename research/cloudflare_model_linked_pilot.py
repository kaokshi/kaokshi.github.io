#!/usr/bin/env python3
"""Limited, owner-encrypted Cloudflare/Groq/Gemini editorial comparison.

Only public numerical metrics reach CI logs. No automatic RSS/publication.
At most 12 Cloudflare calls, 3 Groq calls and 3 Gemini calls.
"""
import io
import json
import os
from pathlib import Path
import sys
from time import monotonic
from urllib.error import HTTPError,URLError
from zipfile import ZipFile,ZIP_DEFLATED
sys.path.insert(0,str(Path(__file__).resolve().parent))
from compare_editorial_models import TASKS,CHANNEL_NAMES,generate,http_json,discover_gemini_model
from editorial_reader_quality import text_stats,quality_flags,cross_article_diversity
from hashlib import sha256
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_encrypted_review import encrypt

MODELS={
 "qwen38":"@cf/qwen/qwen3.8-27b",
 "gemma4":"@cf/google/gemma-4-26b-a4b-it",
 "mistral24":"@cf/mistralai/mistral-small-3.1-24b-instruct",
}
LIMITS={"cloudflare":12,"groq":6,"gemini":3}
RULES=("Русский Яндекс Дзен. Живой оригинальный текст, без шаблонных фраз. "
       "3000–6000 символов без пробелов, 4–8 разделов с **жирными заголовками**. "
       "Каждый блок добавляет новые факты или события. Подписка на канал в финале. "
       "Не выдумывать источники, статистику, персонажей и исторические факты. "
       "Только статья без пояснений.")
def call_cf(model,prompt,env,transport=http_json):
    token=env.get("CLOUDFLARE_API_TOKEN")
    account=env.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise ValueError("missing_cloudflare_credentials")
    url=("https://api.cloudflare.com/client/v4/accounts/"+
         account+"/ai/v1/chat/completions")
    data=transport(url,{"model":model,"max_tokens":2400,"temperature":0.55,
        "messages":[{"role":"system","content":RULES},
                    {"role":"user","content":prompt}]},
        {"Authorization":"Bearer "+token},timeout=140)
    if not isinstance(data,dict) or not data.get("choices"):
        raise ValueError("unexpected_cloudflare_response")
    value=data["choices"][0]["message"].get("content","")
    if not isinstance(value,str) or not value.strip():
        raise ValueError("empty_cloudflare_response")
    return value,data.get("usage",{})

def record(text,channel,seconds):
    stats=text_stats(text,channel)
    return {"sha256":sha256(text.encode()).hexdigest(),
            "chars_without_whitespace":stats["chars_without_whitespace"],
            "bold_section_headers":stats["bold_section_headers"],
            "cta_at_end":stats["subscription_cta_at_end"],
            "repeat_fivegrams":stats["repeated_fivegrams"],
            "repeat_paragraphs":stats["repeated_paragraphs"],
            "lexical_diversity_diagnostic":stats["lexical_variety_sample"],
            "mechanical_flags":quality_flags(stats),
            "mechanical_pass":not quality_flags(stats),
            "seconds":round(seconds,2),
            "human_readability_review":"not_completed",
            "reader_retention_observed":None,
            "facts_verified":False,
            "image_rights_verified":False}

def run(root,env=None,cf=call_cf,external=generate,select_model="qwen38"):
    env=dict(os.environ if env is None else env)
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    result={"schema":"zen-cloudflare-3model-linked-pilot-v1",
            "publication_authorized":False,"paid_calls_authorized":False,
            "limits":LIMITS,"calls":{"cloudflare":0,"groq":0,"gemini":0},
            "tests":[],"status":"running","model_ids":MODELS,
            "selection_for_linked_test":select_model}
    if not env.get("CLOUDFLARE_API_TOKEN") or not env.get("CLOUDFLARE_ACCOUNT_ID"):
        result["status"]="blocked_cloudflare_secret_absent"
        return result
    drafts={}
    def save(model,channel,version,content,seconds):
        key=(model,channel,version)
        filename="-".join(key)+".md"
        p=root/filename
        p.write_text(content,encoding="utf-8")
        p.chmod(0o600)
        report={"model":model,"channel":channel,"version":version,
                "status":"generated",**record(content,channel,seconds)}
        result["tests"].append(report)
        drafts[key]=content
    def failed(model,channel,version,exc):
        result["tests"].append({"model":model,"channel":channel,
            "version":version,"status":"failed",
            "error_type":type(exc).__name__,
            "http_status":exc.code if isinstance(exc,HTTPError) else None})
    # Same exact brief, same word count and title constraints for each model.
    for slug,model in MODELS.items():
        for channel,task in TASKS.items():
            if result["calls"]["cloudflare"]>=LIMITS["cloudflare"]:
                break
            prompt="Канал: "+CHANNEL_NAMES[channel]+"\nЗаголовок: "+task["title"]+"\n"+task["brief"]
            start=monotonic()
            try:
                result["calls"]["cloudflare"]+=1
                body,usage=cf(model,prompt,env)
                save(slug,channel,"author",body,monotonic()-start)
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
                failed(slug,channel,"author",exc)
    # One fixed candidate for combined comparison, not a cherry-picked winner.
    chosen=select_model if select_model in MODELS else "qwen38"
    if external is generate and env.get("GEMINI_API_KEY"):
        try:
            env["GEMINI_SELECTED_MODEL"]=discover_gemini_model(env)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError):
            env.pop("GEMINI_API_KEY",None)
    for channel,task in TASKS.items():
        source=drafts.get((chosen,channel,"author"))
        if not source:
            continue
        for provider in ("groq","gemini"):
            if not env.get("GROQ_API_KEY" if provider=="groq" else "GEMINI_API_KEY"):
                result["tests"].append({"model":chosen+"->"+provider,
                    "channel":channel,"version":"edited",
                    "status":"blocked_provider_secret_or_preflight"})
                continue
            if result["calls"][provider]>=LIMITS[provider]:
                continue
            prompt=("Улучши статью для канала "+CHANNEL_NAMES[channel]+
                    ". Верни только новую версию. Сохрани все подтверждённые детали, "
                    "не выдумывай факты; улучши первое впечатление, ритм, "
                    "разнообразие абзацев и удержание интереса, не сокращай текст "
                    "ниже 3000 знаков без пробелов. 3000–6000 знаков; 4–8 "
                    "блоков с **жирным заголовком**, CTA в конце. "
                    "Ограничения: "+task["brief"]+
                    "\nОригинал для редактуры:\n"+source)
            started=monotonic()
            try:
                result["calls"][provider]+=1
                prose,usage=external(provider,prompt,env=env)
                if not prose or not prose.strip():
                    raise ValueError("empty_edit")
                save(chosen+"->"+provider,channel,"edited",prose,
                     monotonic()-started)
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
                failed(chosen+"->"+provider,channel,"edited",exc)
        # Reverse experiment: Groq creates, Cloudflare selected model edits.
        # Only once per channel, within the strict 12-call Cloudflare budget.
        if env.get("GROQ_API_KEY") and result["calls"]["cloudflare"]<LIMITS["cloudflare"]:
            try:
                result["calls"]["groq"]+=1
                initial,_=external("groq","Канал: "+CHANNEL_NAMES[channel]+
                    "\nТема: "+task["title"]+"\n"+task["brief"]+
                    "\n"+RULES,env=env)
                if not initial:
                    raise ValueError("empty_groq_draft")
                save("groq",channel,"author",initial,0)
                begin=monotonic()
                result["calls"]["cloudflare"]+=1
                modified,_=cf(MODELS[chosen],"Разверни и улучши этот черновик, "
                    "проверь связность и детали, не выдумывай факты. "
                    "Сохрани 3000–6000 символов без пробелов, жирные заголовки "
                    "каждого раздела, CTA в конце.\n"+initial,env)
                save("groq->"+chosen,channel,"edited",modified,monotonic()-begin)
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
                failed("groq->"+chosen,channel,"edited",exc)
    result["status"]="test_completed_with_evidence"
    result["cross_article_diversity"]={
        model:cross_article_diversity({
            ch:drafts[(model,ch,"author")]
            for ch in TASKS if (model,ch,"author") in drafts})
        for model in MODELS}
    return result

def main():
    root=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-cf-pilot"
    result=run(root)
    root.mkdir(parents=True,exist_ok=True)
    pubkey=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    result["encrypted_packet"]=False
    if pubkey and any(x.get("status")=="generated" for x in result["tests"]):
        stream=io.BytesIO()
        with ZipFile(stream,"w",ZIP_DEFLATED) as archive:
            for p in root.glob("*.md"):
                archive.write(p,arcname=p.name)
        encrypted=root/"private-owner-benchmark.enc"
        encrypted.write_bytes(encrypt(stream.getvalue(),pubkey))
        encrypted.chmod(0o600)
        result["encrypted_packet"]=True
    (root/"metrics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                                      encoding="utf-8")
    print(json.dumps({"status":result["status"],"calls":result["calls"],
        "results":[{k:v for k,v in row.items() if k in
             ("model","channel","version","status","chars_without_whitespace",
              "mechanical_flags","seconds","http_status","error_type")}
            for row in result["tests"]]},ensure_ascii=False))

if __name__=="__main__":
    main()
