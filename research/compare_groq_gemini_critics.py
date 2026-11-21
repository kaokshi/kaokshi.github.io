#!/usr/bin/env python3
"""Proposed Groq writer -> Gemini expander -> independent critic comparison.

This is deliberately opt-in. The critic stage calls external providers only
when credentials have been explicitly added. Secrets and article text never
appear in logs. Review signals are hypotheses, not readership statistics.
"""
import json
import os
from pathlib import Path
import sys
from hashlib import sha256
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request,urlopen

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from compare_editorial_models import (TASKS,CHANNEL_NAMES,SYSTEM,generate,
    discover_gemini_model,http_json,metrics,private_packet)
from editorial_reader_quality import text_stats,quality_flags,review_checklist

CRITICS={
    "mistral":{"secret":"MISTRAL_API_KEY","model_env":"MISTRAL_CRITIC_MODEL",
               "endpoint":"https://api.mistral.ai/v1/chat/completions"},
    "sambanova":{"secret":"SAMBANOVA_API_KEY","model_env":"SAMBANOVA_CRITIC_MODEL",
                 "endpoint":"https://api.sambanova.ai/v1/chat/completions"},
    "cloudflare":{"secret":"CLOUDFLARE_API_TOKEN","model_env":"CLOUDFLARE_CRITIC_MODEL",
                  "endpoint":"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"},
}

def independent_critique(provider,source,improved,channel,env=None,transport=http_json):
    env=os.environ if env is None else env
    cfg=CRITICS[provider]
    key=env.get(cfg["secret"])
    model=env.get(cfg["model_env"])
    if not key or not model:
        return {"status":"blocked_missing_configuration",
                "required":[k for k in (cfg["secret"],cfg["model_env"])
                            if not env.get(k)]}
    endpoint=cfg["endpoint"]
    if provider=="cloudflare":
        account=env.get("CLOUDFLARE_ACCOUNT_ID")
        if not account:
            return {"status":"blocked_missing_configuration",
                    "required":["CLOUDFLARE_ACCOUNT_ID"]}
        endpoint=endpoint.replace("{account}",account)
    task=TASKS[channel]
    prompt=("Ты независимый редактор. Отвечай JSON на русском языке. "
            "Сравни две версии статьи для канала "+CHANNEL_NAMES[channel]+
            ". Критерии: начало цепляет конкретикой; новая мысль в каждом "
            "разделе; логика и развитие интриги; разнообразие формулировок; "
            "оригинальность без штампов; связность и ясная развязка. "
            "Проверяй, что вторичная редактура НЕ потеряла важные детали и "
            "НЕ ввела новые неподтверждённые факты. "
            "Не выдавай редакционное впечатление за измеренное удержание. "
            "План источников: "+task["brief"]+
            "\\nОригинал Groq:\\n"+source+
            "\\nВерсия Gemini:\\n"+improved+
            '\\nВерни только объект JSON: {"preferred_version":"groq|gemini|neither",'
            '"specific_issues":["..."],"interest_rationale":"...",'
            '"diversity_rationale":"...",'
            '"needs_human_review":true}. Не включай цитаты из текстов.')
    payload={"model":model,"temperature":0.1,"max_tokens":1100,
             "messages":[{"role":"system","content":"Независимый литературный критик. Верни JSON."},
                         {"role":"user","content":prompt}]}
    start=monotonic()
    try:
        result=transport(endpoint,payload,{"Authorization":"Bearer "+key},timeout=110)
        if provider=="cloudflare" and isinstance(result,dict) and "result" in result:
            result=result["result"]
        answer=result["choices"][0]["message"].get("content","")
        # Never put freeform critique in public metrics; encrypted packet only.
        return {"status":"response_received","elapsed_seconds":round(monotonic()-start,2),
                "raw_response":answer,"model":model}
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
        return {"status":"provider_failed","error_type":type(exc).__name__,
                "http_status":exc.code if isinstance(exc,HTTPError) else None}

def run(root,env=None,writer=generate,critic=independent_critique):
    env=dict(os.environ if env is None else env)
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    report={"schema":"zen-groq-gemini-independent-critics-v1",
            "publication_authorized":False,"generated":0,"channels":[],
            "real_reader_retention":None,"critics":list(CRITICS)}
    required=("GROQ_API_KEY","GEMINI_API_KEY")
    missing=[key for key in required if not env.get(key)]
    if missing:
        report.update(status="blocked_missing_secret",missing_secrets=missing)
        return report
    if writer is generate:
        try:
            env["GEMINI_SELECTED_MODEL"]=discover_gemini_model(env)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
            report.update(status="gemini_preflight_failed",error_type=type(exc).__name__,
                          http_status=exc.code if isinstance(exc,HTTPError) else None)
            return report
    for channel,task in TASKS.items():
        entry={"channel":channel,"status":"not_generated","critics":{}}
        try:
            prompt="Канал: "+CHANNEL_NAMES[channel]+"\\nТема: "+task["title"]+"\\n"+task["brief"]
            groq,groq_usage=writer("groq",prompt,env=env)
            if not isinstance(groq,str) or not groq.strip():
                raise ValueError("groq_empty")
            upgrade=("Ты улучшаешь готовый русский текст для Дзена. "
                     "Увеличь глубину и детализацию без пустых слов и новых "
                     "неподтверждённых фактов; сохрани лучшую оригинальную "
                     "конкретику. Разнообразь синтаксис и начало разделов, "
                     "усиль развитие интереса и связность. "
                     "3000–6000 символов без пробелов, 4–8 смысловых блоков, "
                     "каждый начинается с **жирного заголовка**. "
                     "Естественный призыв подписаться в конце. "
                     "Верни только текст статьи.\\n"
                     "Исходное задание: "+task["brief"]+
                     "\\nОригинал Groq:\\n"+groq)
            gemini,gemini_usage=writer("gemini",upgrade,env=env)
            if not isinstance(gemini,str) or not gemini.strip():
                raise ValueError("gemini_empty")
            # Retain both stages in the owner's encrypted packet only.
            (root/(channel+"-groq-original.md")).write_text(groq,encoding="utf-8")
            (root/(channel+".md")).write_text(gemini,encoding="utf-8")
            for p in (root/(channel+"-groq-original.md"),root/(channel+".md")):
                p.chmod(0o600)
            entry.update(status="draft_generated",
                groq=metrics(groq,0),gemini=metrics(gemini,0),
                groq_flags=quality_flags(text_stats(groq,channel)),
                gemini_flags=quality_flags(text_stats(gemini,channel)),
                groq_sha256=sha256(groq.encode()).hexdigest(),
                gemini_sha256=sha256(gemini.encode()).hexdigest(),
                human_interest_review="pending",
                reader_interest_checklist=review_checklist(channel))
            report["generated"]+=1
            for provider in CRITICS:
                result=critic(provider,groq,gemini,channel,env=env)
                raw=result.pop("raw_response",None)
                if raw:
                    (root/(channel+"-"+provider+"-critique.txt")).write_text(raw,encoding="utf-8")
                    (root/(channel+"-"+provider+"-critique.txt")).chmod(0o600)
                    result["encrypted_critique_in_packet"]=True
                entry["critics"][provider]=result
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
            entry.update(status="generation_failed",error_type=type(exc).__name__,
                         http_status=exc.code if isinstance(exc,HTTPError) else None)
        report["channels"].append(entry)
    report["status"]="drafts_generated_critics_experimental" if report["generated"]==3 else "partial_or_failed"
    return report

def main():
    root=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-groq-gemini-critics"
    report=run(root)
    # Deliver only metadata publicly. Owner archive encrypts all text and critiques.
    from io import BytesIO
    from zipfile import ZipFile,ZIP_DEFLATED
    from editorial_encrypted_review import encrypt
    key=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    if report["generated"] and key:
        b=BytesIO()
        with ZipFile(b,"w",ZIP_DEFLATED) as z:
            z.writestr("metrics.json",json.dumps(report,ensure_ascii=False))
            for path in root.glob("*.md"):
                z.write(path,arcname=path.name)
            for path in root.glob("*-critique.txt"):
                z.write(path,arcname=path.name)
        archive=root/"private-owner-review.enc"
        archive.write_bytes(encrypt(b.getvalue(),key))
        archive.chmod(0o600)
        report["encrypted_packet"]=True
    else:
        report["encrypted_packet"]=False
    (root/"metrics.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                                     encoding="utf-8")
    print(json.dumps({"status":report["status"],"generated":report["generated"],
                     "critic_statuses":[{"channel":x["channel"],
                                       "critics":{k:v["status"] for k,v in x["critics"].items()}}
                                      for x in report["channels"]]},
                     ensure_ascii=False))

if __name__=="__main__":
    main()
