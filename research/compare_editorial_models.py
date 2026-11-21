#!/usr/bin/env python3
"""Independent, privacy-safe generation benchmark for Zen's three channels.

No publishing. Model outputs stay in runner.temp and are encrypted for owner.
No fabricated manual-quality, scientific-fact, or image-rights scores.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
from time import monotonic
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from zipfile import ZipFile, ZIP_DEFLATED
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_encrypted_review import encrypt
from editorial_reader_quality import text_stats, quality_flags, review_checklist

MODEL_IDS={"gemini":"gemini-2.5-flash",
           "groq":"openai/gpt-oss-120b",
           "qwen":"qwen3.5-9b",
           "hybrid":"gemini-2.5-flash + openai/gpt-oss-120b"}
TASKS={
 "legends": {
   "title":"Когда из квартиры исчезли все часы",
   "brief":"Оригинальная городская мистическая история: ночной подъезд, часы, соседи, "
           "рациональные детали, нарастающее напряжение; не копируй известные сюжеты."},
 "science": {
   "title":"Почему осьминоги могут менять цвет кожи",
   "brief":"Научно-популярный текст об изменении окраски у осьминогов. "
           "Укажи роль хроматофоров и визуального камуфляжа; отделяй "
           "обоснованное от гипотез, не выдумывай научные статьи, точные цифры и цитаты."},
 "cinema": {
   "title":"Путешествие на Луну (1902) — обзор фильма",
   "brief":"Обзор немой картины Жоржа Мельеса 1902 года. "
           "Опирайся только на данные задания: группа астрономов летит "
           "в капсуле на Луну, встречает её обитателей и возвращается. "
           "Не приписывай фильму сцен и реплик, которых нет в предоставленном описании."},
}
SYSTEM=("Ты редакционный автор русскоязычного Яндекс Дзена. Пиши естественным живым "
        "русским языком, без канцелярита, повторов, фальшивых цитат, придуманных "
        "источников и утверждений о непроверенных фактах. "
        "Статья должна содержать 3000–5000 знаков без пробелов, "
        "4–7 смысловых разделов; каждый раздел начинается строкой **Жирный заголовок**. "
        "Первая строка — выразительный заголовок статьи. "
        "В конце естественный призыв подписаться на соответствующий канал. "
        "Верни только готовую статью, без пояснений.")
CHANNEL_NAMES={"legends":"Легенды тёмных улиц",
               "science":"За гранью очевидного",
               "cinema":"Кадр за Кадром"}
MAX_HTTP_BYTES=2_000_000
HYBRID_REPAIR_LIMIT=2  # Bounded pilot; never exceeds 5 agent revisions
GEMINI_PREFERENCES=("gemini-3.8-flash","gemini-3.6-flash",
                    "gemini-3.5-flash","gemini-3.1-flash-lite",
                    "gemini-2.5-flash")

def discover_gemini_model(env, opener=urlopen):
    """Choose an actually advertised generateContent model for this API key."""
    key=env.get("GEMINI_API_KEY")
    if not key:
        raise KeyError("missing_api_key")
    request=Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                    headers={"x-goog-api-key":key,"Accept":"application/json"})
    with opener(request,timeout=35) as response:
        payload=json.load(response)
    models={x.get("name","").removeprefix("models/") for x in payload.get("models",[])
            if "generateContent" in x.get("supportedGenerationMethods",[])}
    for candidate in GEMINI_PREFERENCES:
        if candidate in models:
            return candidate
    raise ValueError("no_preferred_generateContent_model_visible")

def discover_groq_access(env, opener=urlopen):
    key=env.get("GROQ_API_KEY")
    if not key:
        raise KeyError("missing_api_key")
    request=Request("https://api.groq.com/openai/v1/models",
                    headers={"Authorization":"Bearer "+key,"Accept":"application/json",
                             "User-Agent":"ZenEditorialAPIDiagnostic/1.0"})
    with opener(request,timeout=35) as response:
        data=json.load(response)
    return MODEL_IDS["groq"] in {x.get("id") for x in data.get("data",[])}


def http_json(url, payload, headers, timeout=150):
    request=Request(url,method="POST",headers={
        "Content-Type":"application/json",
        "User-Agent":"ZenEditorialAPIDiagnostic/1.0",**headers
    },data=json.dumps(payload,ensure_ascii=False).encode("utf-8"))
    with urlopen(request,timeout=timeout) as response:
        raw=response.read(MAX_HTTP_BYTES+1)
    if len(raw)>MAX_HTTP_BYTES:
        raise ValueError("response_too_large")
    return json.loads(raw)

def generate(backend,prompt,*,http=http_json,env=None,system_instruction=SYSTEM):
    env=os.environ if env is None else env
    if backend=="gemini":
        key=env.get("GEMINI_API_KEY")
        if not key:
            raise KeyError("missing_api_key")
        response=http(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            +env.get("GEMINI_SELECTED_MODEL",MODEL_IDS[backend])+":generateContent",
            {"systemInstruction":{"parts":[{"text":system_instruction}]},
             "contents":[{"role":"user","parts":[{"text":prompt}]}],
             "generationConfig":{"temperature":0.55,"maxOutputTokens":4600}},
            {"x-goog-api-key":key})
        return "\n".join(part.get("text","") for item in response["candidates"][:1]
                          for part in item["content"]["parts"]),response.get("usageMetadata",{})
    if backend=="groq":
        key=env.get("GROQ_API_KEY")
        if not key:
            raise KeyError("missing_api_key")
        response=http("https://api.groq.com/openai/v1/chat/completions",
            {"model":MODEL_IDS[backend],"temperature":0.5,"max_completion_tokens":4500,
             "messages":[{"role":"system","content":system_instruction},
                         {"role":"user","content":prompt}]},
            {"Authorization":"Bearer "+key})
        return response["choices"][0]["message"].get("content") or "",response.get("usage",{})
    if backend=="qwen":
        response=http("http://127.0.0.1:8093/v1/chat/completions",
            {"model":MODEL_IDS[backend],"temperature":0.55,"max_tokens":4500,
             "messages":[{"role":"system","content":system_instruction},
                         {"role":"user","content":prompt}]},{},timeout=900)
        return response["choices"][0]["message"].get("content") or "",response.get("usage",{})
    raise ValueError("unknown_backend")

def metrics(prose,elapsed):
    plain=re.sub(r"\*\*|[#_*]","",prose)
    chars=len(re.sub(r"\s","",plain))
    headings=len(re.findall(r"(?m)^\*\*[^*\n]{5,120}\*\*\s*$",prose))
    return {"chars_without_whitespace":chars,"length_ok":3000<=chars<=5000,
            "bold_section_headers":headings,
            "section_headers_ok":4<=headings<=7,
            "has_subscription_cta":bool(re.search(r"подпис",prose,re.I)),
            "elapsed_seconds":round(elapsed,2),
            "text_sha256":sha256(prose.encode()).hexdigest(),
            "fact_check_completed":False,"image_rights_verified":False,
            "human_quality_review_completed":False}

def run(backend,root,*,generator=generate,env=None):
    env=os.environ if env is None else env
    root=Path(root)
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    manifest={"schema":"zen-three-model-benchmark-v1",
              "backend":backend,"model":MODEL_IDS[backend],
              "generated_at":datetime.now(timezone.utc).isoformat(),
              "requested":3,"generated":0,"status":"running",
              "publication_authorized":False,
              "channels":[]}
    needed_keys={"gemini":("GEMINI_API_KEY",),"groq":("GROQ_API_KEY",),
                 "qwen":(),"hybrid":("GEMINI_API_KEY","GROQ_API_KEY")}[backend]
    absent=[key for key in needed_keys if not env.get(key)]
    if absent:
        manifest.update(status="blocked_missing_secret",
                        blocked_secret_names=absent)
        return manifest
    # Preflight yields explicit authentication/model compatibility evidence,
    # never API key contents. Only public model IDs and HTTP codes are stored.
    if generator is generate and backend in ("gemini","hybrid"):
        try:
            actual_model=discover_gemini_model(env)
            env=dict(env, GEMINI_SELECTED_MODEL=actual_model)
            manifest["gemini_model_selected"]=actual_model
            if backend=="gemini":
                manifest["model"]=actual_model
        except (KeyError,ValueError,TypeError,OSError,URLError,HTTPError) as exc:
            manifest.update(status="gemini_preflight_failed",
                            error_type=type(exc).__name__,
                            http_status=(exc.code if isinstance(exc,HTTPError) else None))
            return manifest
    if generator is generate and backend in ("groq","hybrid"):
        try:
            manifest["groq_target_listed"]=discover_groq_access(env)
            if not manifest["groq_target_listed"]:
                manifest["status"]="groq_model_not_visible_to_key"
                return manifest
        except (KeyError,ValueError,TypeError,OSError,URLError,HTTPError) as exc:
            manifest.update(status="groq_preflight_failed",
                            error_type=type(exc).__name__,
                            http_status=(exc.code if isinstance(exc,HTTPError) else None))
            return manifest
    for channel,task in TASKS.items():
        prompt=("Канал: "+CHANNEL_NAMES[channel]+"\nТема: "+task["title"]+
                "\nМатериал для написания: "+task["brief"]+
                "\nСоздай самостоятельную статью, не копируй текст описания.")
        start=monotonic()
        entry={"channel":channel,"status":"not_generated"}
        try:
            if backend == "hybrid":
                # Shared brief, two real distinct provider calls. No draft text
                # from either model is ever printed or uploaded unencrypted.
                first,first_usage=generator("gemini",prompt,env=env)
                if not isinstance(first,str) or not first.strip():
                    raise ValueError("gemini_empty_draft")
                editor_system=(
                    "Ты независимый редактор русскоязычной статьи для Дзена. "
                    "Исправь повторы, канцелярит и логические ошибки, сохраняя "
                    "смысл и фактические ограничения. Не добавляй новые факты, "
                    "цифры, сцены, источники или цитаты. "
                    "Оставь 3000–5000 символов без пробелов, 4–7 разделов, "
                    "каждый с заголовком в **двух звёздочках**, и естественный CTA. "
                    "Верни только полностью отредактированную статью."
                )
                editor_prompt=("Канал: "+CHANNEL_NAMES[channel]+"\n"
                               "Тема: "+task["title"]+"\n"
                               "Фактические ограничения: "+task["brief"]+"\n"
                               "Черновик Gemini для проверки и правки:\n"+first)
                # Default provider accepts editor_system. Test doubles use
                # only the three-argument interface and cannot claim real output.
                if generator is generate:
                    prose,second_usage=generator("groq",editor_prompt,env=env,
                                                system_instruction=editor_system)
                else:
                    prose,second_usage=generator("groq",editor_prompt,env=env)
                # The first editorial pass is NOT approved just because it
                # returned nonempty prose. Recheck actual draft constraints.
                # Further repairs count towards the agent revision budget.
                revisions=[]
                for revision in range(HYBRID_REPAIR_LIMIT+1):
                    if not isinstance(prose,str) or not prose.strip():
                        break
                    stats=text_stats(prose,channel)
                    flaws=quality_flags(stats)
                    revisions.append({
                        "revision_index":revision+1,
                        "sha256":sha256(prose.encode()).hexdigest(),
                        "flags":flaws,
                        "reader_interest_editorial_review":"pending",
                        "real_audience_retention":None,
                    })
                    if not flaws or revision==HYBRID_REPAIR_LIMIT:
                        break
                    # Pass explicit diagnostic codes and the current text.
                    # Enforce preservation of facts, genre and individual voice.
                    repair_prompt=(
                        "Канал: "+CHANNEL_NAMES[channel]+
                        "\\nИсходная задача: "+task["brief"]+
                        "\\nОтчёт проверок: "+", ".join(flaws)+
                        "\\nПожалуйста, исправь ТОЛЬКО реальные недостатки. "
                        "Объём 3000–6000 знаков без пробелов, 4–8 разделов. "
                        "Каждый новый блок начинается с **жирного заголовка**. "
                        "Сохраняй оригинальные детали, избегай повторов и пустых вступлений. "
                        "Удерживай читательский интерес через развитие сюжета/объяснения, "
                        "а не искусственную интригу. Заверши естественным призывом "
                        "подписаться на канал. Не добавляй неподтверждённых фактов. "
                        "\\nТЕКУЩИЙ ТЕКСТ:\\n"+prose)
                    if generator is generate:
                        replacement,repair_usage=generator(
                            "groq",repair_prompt,env=env,
                            system_instruction=editor_system)
                    else:
                        replacement,repair_usage=generator(
                            "groq",repair_prompt,env=env)
                    if isinstance(replacement,str) and replacement.strip():
                        prose=replacement
                        second_usage=repair_usage
                entry["agent_revisions_used"]=max(0,len(revisions)-1)
                entry["agent_revision_limit"]=5
                entry["review_versions"]=revisions
                entry["objective_quality_flags"]=quality_flags(text_stats(prose,channel))
                entry["automated_format_pass"]=not entry["objective_quality_flags"]
                entry["reader_interest_editorial_review"]="pending"
                entry["real_audience_retention"]=None
                entry["reader_interest_review_checklist"]=review_checklist(channel)
                usage={"gemini":first_usage,"groq":second_usage}
                entry["workflow"]="gemini_draft_then_groq_editor"
                entry["gemini_draft_sha256"]=sha256(first.encode()).hexdigest()
                (root/(channel+"-gemini-original.md")).write_text(first,encoding="utf-8")
                (root/(channel+"-gemini-original.md")).chmod(0o600)
            else:
                prose,usage=generator(backend,prompt,env=env)
            if not isinstance(prose,str) or not prose.strip():
                raise ValueError("empty_output")
            (root/(channel+".md")).write_text(prose,encoding="utf-8")
            (root/(channel+".md")).chmod(0o600)
            entry.update(status="draft_generated",**metrics(prose,monotonic()-start))
            entry["reader_quality_proxies"]=text_stats(prose,channel)
            entry["reader_quality_flags"]=quality_flags(entry["reader_quality_proxies"])
            entry["reader_interest_checklist"]=review_checklist(channel)
            entry["reader_retention_measured"]=False
            entry["token_usage"]=usage
            manifest["generated"]+=1
        except (KeyError,ValueError,TypeError,TimeoutError,OSError,URLError,HTTPError) as exc:
            entry.update(status="generation_failed",error_type=type(exc).__name__,
                         http_status=(exc.code if isinstance(exc,HTTPError) else None),
                         elapsed_seconds=round(monotonic()-start,2))
        manifest["channels"].append(entry)
    manifest["status"]="drafts_generated_not_publish_ready" if manifest["generated"]==3 else "partial_or_failed"
    return manifest

def private_packet(root,manifest,public_key):
    if not public_key or not manifest["generated"]:
        return None
    buffer=io.BytesIO()
    with ZipFile(buffer,"w",ZIP_DEFLATED) as zipped:
        zipped.writestr("metadata.json",json.dumps(manifest,ensure_ascii=False))
        for channel in TASKS:
            path=root/(channel+".md")
            if path.is_file():
                zipped.write(path,arcname=channel+".md")
            original=root/(channel+"-gemini-original.md")
            if original.is_file():
                zipped.write(original,arcname=channel+"-gemini-original.md")
    result=root.parent/("zen-"+manifest["backend"]+"-owner-review.enc")
    result.write_bytes(encrypt(buffer.getvalue(),public_key))
    result.chmod(0o600)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--backend",choices=MODEL_IDS,required=True)
    args=parser.parse_args()
    base=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-model-compare"
    root=base/args.backend
    report=run(args.backend,root)
    try:
        archive=private_packet(root,report,os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX",""))
        report["encrypted_packet"]=bool(archive)
    except (ValueError,TypeError,OSError) as exc:
        report["encrypted_packet"]=False
        report["encryption_error"]=type(exc).__name__
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    (base/(args.backend+"-metadata.json")).write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"backend":args.backend,"model":report["model"],
                      "status":report["status"],"generated":report["generated"],
                      "channels":report["channels"],
                      "encrypted_packet":report.get("encrypted_packet",False)},
                     ensure_ascii=False))
    # Missing secrets = honest unavailable result. The CI job is not an article approval.
    return 0

if __name__=="__main__":
    raise SystemExit(main())
