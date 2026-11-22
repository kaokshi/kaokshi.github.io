#!/usr/bin/env python3
"""Real, bounded Groq/Cloudflare editorial practice on three genre briefs.

Gemini is reserved as a manual fallback and never called in this run.
No public articles, RSS changes, automatic publish, paid services, or invented quality scores.
"""
from __future__ import annotations
from hashlib import sha256
from io import BytesIO
import json, os, sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_editorial_models import TASKS, CHANNEL_NAMES, SYSTEM, generate
from cloudflare_model_linked_pilot import MODELS, call_cf, record
from editorial_reader_quality import text_stats, quality_flags
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_encrypted_review import encrypt

MODEL_CHOICE={"gemma4": MODELS["gemma4"],"mistral24":MODELS["mistral24"]}
MAX_CALLS={"groq":6,"cloudflare":9,"gemini":0}

def run(root, *, env=None, groq_fn=None, cloudflare_fn=None, pause=None):
    env=dict(os.environ if env is None else env)
    groq_fn=groq_fn or (lambda prompt: generate("groq",prompt,env=env))
    cloudflare_fn=cloudflare_fn or (lambda model,prompt: call_cf(model,prompt,env))
    pause=pause or sleep
    root=Path(root)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    result={"schema":"zen-groq-cloudflare-real-practice-v1",
      "status":"running","publication_authorized":False,
      "paid_calls_authorized":False,"gemini_fallback_reserved":True,
      "gemini_calls":0,"limits":MAX_CALLS,"calls":{"groq":0,"cloudflare":0},
      "channels":[],"actual_reader_retention":None}
    if not all(env.get(k) for k in
               ("GROQ_API_KEY","CLOUDFLARE_API_TOKEN","CLOUDFLARE_ACCOUNT_ID")):
        result["status"]="blocked_missing_secrets"
        return result
    for channel,task in TASKS.items():
        row={"channel":channel,"versions":[],"status":"running"}
        brief=("Канал "+CHANNEL_NAMES[channel]+". Тема "+task["title"]+
               ". Ограничения: "+task["brief"]+
               ". Напиши новую полноценную оригинальную статью. "+SYSTEM)
        def stage(label,provider,fn):
            if result["calls"][provider]>=MAX_CALLS[provider]:
                row["versions"].append({"step":label,"status":"skipped_request_budget"})
                return None
            result["calls"][provider]+=1
            started=monotonic()
            try:
                prose,usage=fn()
                if not isinstance(prose,str) or not prose.strip():
                    raise ValueError("empty_response")
                path=root/(channel+"-"+label+".md")
                path.write_text(prose,encoding="utf-8")
                path.chmod(0o600)
                row["versions"].append({"step":label,"provider":provider,
                    "status":"generated",**record(prose,channel,monotonic()-started)})
                return prose
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as error:
                row["versions"].append({"step":label,"provider":provider,
                    "status":"failed","error_type":type(error).__name__,
                    "http_status":error.code if isinstance(error,HTTPError) else None})
                return None
        initial=stage("groq-author","groq",lambda:groq_fn(brief))
        if initial:
            for name,model in MODEL_CHOICE.items():
                edit_prompt=("Ты независимый редактор канала "+CHANNEL_NAMES[channel]+
                    ". Не выдумывай подробностей, сохрани конкретные детали исходника. "
                    "Исправь слабые переходы, штампы и повторы, придай тексту ритм "
                    "и естественный русский язык. 3000–6000 символов без пробелов, "
                    "4–8 смысловых блоков с **жирными заголовками**, "
                    "ненавязчивый призыв подписаться в конце. "
                    "Верни только готовую статью. Тема: "+task["brief"]+
                    "\nИСХОДНИК GROQ:\n"+initial)
                stage("groq-to-"+name,"cloudflare",
                    lambda model=model,prompt=edit_prompt:cloudflare_fn(model,prompt))
        else:
            row["versions"].append({"step":"cloudflare-edit","status":"skipped_no_groq_draft"})
        # Reverse direction, same model Q&A and same factual constraints.
        independent=stage("gemma4-author","cloudflare",
                          lambda:cloudflare_fn(MODEL_CHOICE["gemma4"],brief))
        if independent:
            editor_prompt=("Ты редактор русскоязычного Дзена. Улучши статью "
                "без удаления смысла и без новых неподтверждённых фактов. "
                "Усиление сюжета, разнообразие, ясность. "
                "3000–6000 знаков без пробелов, 4–8 жирных заголовков, "
                "CTA в конце.\nЗАДАНИЕ:\n"+task["brief"]+
                "\nТЕКСТ CLOUDFLARE:\n"+independent)
            stage("gemma4-to-groq","groq",lambda:groq_fn(editor_prompt))
        else:
            row["versions"].append({"step":"gemma4-to-groq",
                                    "status":"skipped_no_cloudflare_draft"})
        row["status"]="completed_with_partial_evidence"
        result["channels"].append(row)
        # Avoid bursty back-to-back Groq calls within 8k TPM; short fixed spacing.
        pause(12)
    result["status"]="real_test_complete_with_partial_or_full_evidence"
    return result

def main():
    root=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-groq-cf-practice"
    result=run(root)
    root.mkdir(parents=True,exist_ok=True)
    pubkey=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    result["encrypted_drafts"]=False
    files=list(root.glob("*.md"))
    if files and pubkey:
        mem=BytesIO()
        with ZipFile(mem,"w",ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path,arcname=path.name)
        artifact=root/"owner-drafts.enc"
        artifact.write_bytes(encrypt(mem.getvalue(),pubkey))
        artifact.chmod(0o600)
        result["encrypted_drafts"]=True
    (root/"metrics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                                     encoding="utf-8")
    print(json.dumps({"status":result["status"],"calls":result["calls"],
        "gemini_calls":0,"channels":[{"channel":x["channel"],
          "versions":[{k:v for k,v in y.items() if k in (
          "step","status","provider","chars_without_whitespace",
          "mechanical_flags","seconds","http_status","error_type")}
          for y in x["versions"]]} for x in result["channels"]]},ensure_ascii=False))
if __name__=="__main__":
    main()
