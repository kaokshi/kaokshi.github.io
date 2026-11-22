#!/usr/bin/env python3
"""Bounded Groq -> Gemini -> Cloudflare Qwen triad, owner-only text archive.

All three models receive the same factual constraints. The final Cloudflare
step audits Gemini's edited draft and makes a targeted correction, preserving
the previous version for comparison. No publishing, paid fallbacks or claims
of measured readership engagement.
"""
import io
import json
import os
from pathlib import Path
import sys
from time import monotonic
from urllib.error import HTTPError,URLError
from zipfile import ZipFile,ZIP_DEFLATED
from hashlib import sha256

sys.path.insert(0,str(Path(__file__).resolve().parent))
from compare_editorial_models import TASKS,CHANNEL_NAMES,generate,discover_gemini_model
from cloudflare_model_linked_pilot import MODELS,call_cf,record
from editorial_reader_quality import cross_article_diversity,quality_flags,text_stats
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_encrypted_review import encrypt

MAX_CALLS={"groq":3,"gemini":3,"cloudflare":3}
MODEL=MODELS["qwen38"]

def safely_report_error(exc):
    return {"status":"failed","error_type":type(exc).__name__,
            "http_status":exc.code if isinstance(exc,HTTPError) else None}

def run(root,env=None,writer=generate,third=call_cf,discovery=discover_gemini_model):
    env=dict(os.environ if env is None else env)
    root=Path(root)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    outcome={"schema":"zen-triad-groq-gemini-cloudflare-v1",
        "sequence":["groq_writer","gemini_expander","cloudflare_independent_editor"],
        "cloudflare_model":MODEL,"calls":{"groq":0,"gemini":0,"cloudflare":0},
        "max_calls":MAX_CALLS,"publication_authorized":False,
        "paid_calls_authorized":False,"observed_reader_retention":None,
        "channels":[],"status":"running"}
    required=("GROQ_API_KEY","GEMINI_API_KEY",
              "CLOUDFLARE_API_TOKEN","CLOUDFLARE_ACCOUNT_ID")
    missing=[name for name in required if not env.get(name)]
    if missing:
        outcome.update(status="blocked_missing_secrets",missing_secret_names=missing)
        return outcome
    if writer is generate:
        try:
            env["GEMINI_SELECTED_MODEL"]=discovery(env)
            outcome["gemini_model"]=env["GEMINI_SELECTED_MODEL"]
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
            outcome.update(status="gemini_preflight_failed",**safely_report_error(exc))
            return outcome
    for channel,task in TASKS.items():
        entry={"channel":channel,"status":"not_generated","stages":[]}
        source=("Канал: "+CHANNEL_NAMES[channel]+"\nНазвание: "+task["title"]+
            "\nФактические ограничения: "+task["brief"]+
            "\nНапиши оригинальную статью, 3000–6000 символов без пробелов, "
            "4–8 блоков с заголовками **жирным текстом**, естественный призыв "
            "подписаться в конце. Удерживай интерес через развитие событий "
            "или объяснения; не выдумывай источники и дополнительные факты.")
        versions={}
        stages=[
          ("groq","author"),
          ("gemini","expander"),
          ("cloudflare","independent_editor"),
        ]
        for backend,role in stages:
            previous=versions.get("groq") if backend=="gemini" else versions.get("gemini")
            if backend!="groq" and not previous:
                entry["stages"].append({"provider":backend,"role":role,
                                       "status":"skipped_previous_stage_failed"})
                break
            prompt=source
            if backend=="gemini":
                prompt=("Улучши первоначальный текст для канала "+CHANNEL_NAMES[channel]+
                    ". Раскрой недостающие детали, усили естественный интерес с первого "
                    "абзаца и переходы, убери однообразие. Не выдумывай ничего сверх "
                    "фактических ограничений. Не сокращай ниже 3000 символов без пробелов. "
                    "4–8 смысловых блоков с заголовками **жирным текстом** и CTA в конце. "
                    "\nЗАДАНИЕ:\n"+source+"\nЧЕРНОВИК GROQ:\n"+previous)
            if backend=="cloudflare":
                initial=versions["groq"]
                current=versions["gemini"]
                flags=quality_flags(text_stats(current,channel))
                prompt=("Ты независимый редактор-критик русскоязычного Дзена. "
                    "Сравни черновик Groq и улучшенную версию Gemini. "
                    "Выбери и сохрани лучшие выразительные детали обеих. "
                    "Устрани конкретные нарушения формата: "+", ".join(flags)+". "
                    "Проверь сильное начало, разнообразие, последовательное развитие "
                    "идеи, логику переходов, финал, отсутствие штампов и повторов. "
                    "Не придумывай сцены, научные факты или источники. "
                    "Верни ТОЛЬКО итоговую полную статью (не отчёт). "
                    "Объём 3000–6000 символов без пробелов; 4–8 жирных заголовков, "
                    "призыв подписаться в конце.\nЗАДАНИЕ:\n"+source+
                    "\nОРИГИНАЛ GROQ:\n"+initial+
                    "\nРЕДАКТУРА GEMINI:\n"+current)
            started=monotonic()
            outcome["calls"][backend]+=1
            try:
                if backend=="cloudflare":
                    body,_=third(MODEL,prompt,env)
                else:
                    body,_=writer(backend,prompt,env=env)
                if not isinstance(body,str) or not body.strip():
                    raise ValueError("empty_model_output")
                versions[backend]=body
                saved=root/(channel+"-"+role+".md")
                saved.write_text(body,encoding="utf-8")
                saved.chmod(0o600)
                entry["stages"].append({
                    "provider":backend,"role":role,"status":"generated",
                    **record(body,channel,monotonic()-started)})
            except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError,TypeError) as exc:
                entry["stages"].append({"provider":backend,"role":role,
                    **safely_report_error(exc),
                    "elapsed_seconds":round(monotonic()-started,2)})
                break
        entry["status"]="all_stages_generated" if len(versions)==3 else "partial_or_failed"
        if len(versions)==3:
            initial_flags=quality_flags(text_stats(versions["groq"],channel))
            gemini_flags=quality_flags(text_stats(versions["gemini"],channel))
            final_flags=quality_flags(text_stats(versions["cloudflare"],channel))
            entry["mechanical_comparison"]={
                "groq_flags":initial_flags,"gemini_flags":gemini_flags,
                "cloudflare_flags":final_flags,
                "final_mechanically_acceptable":not final_flags,
                "qualitative_reader_interest":"awaits_human_review",
                "factual_verification":"not_completed"}
        outcome["channels"].append(entry)
    outcome["status"]="three_stage_test_completed" if all(
        x["status"]=="all_stages_generated" for x in outcome["channels"]
    ) else "partial_or_failed"
    return outcome

def main():
    root=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-triad-pilot"
    result=run(root)
    pubkey=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    result["encrypted_packet"]=False
    files=list(root.glob("*.md"))
    if pubkey and files:
        content=io.BytesIO()
        with ZipFile(content,"w",ZIP_DEFLATED) as archive:
            for file in files:
                archive.write(file,arcname=file.name)
        dest=root/"private-triad-review.enc"
        dest.write_bytes(encrypt(content.getvalue(),pubkey))
        dest.chmod(0o600)
        result["encrypted_packet"]=True
    root.mkdir(exist_ok=True,parents=True)
    (root/"metrics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                                     encoding="utf-8")
    print(json.dumps({"status":result["status"],"calls":result["calls"],
       "channels":[{"channel":x["channel"],"status":x["status"],
                    "stages":[{k:v for k,v in z.items() if k in
                    ("provider","status","chars_without_whitespace","mechanical_flags",
                     "seconds","http_status","error_type")} for z in x["stages"]]}
                   for x in result["channels"]]},ensure_ascii=False))

if __name__=="__main__":
    main()
