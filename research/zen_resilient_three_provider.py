#!/usr/bin/env python3
"""Read-only live probe of Groq -> Cloudflare -> OpenRouter, Gemini never invoked.

Only error codes, response lengths and timings are public. No provider secrets,
generated prose, raw error payloads or article contents are printed.
"""
import json, os, re, time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

FREE=("nvidia/nemotron-3-ultra-550b-a55b:free","google/gemma-4-26b-a4b-it:free")
CF_MODELS=("@cf/google/gemma-4-26b-a4b-it","@cf/mistralai/mistral-small-3.1-24b-instruct")
PROMPT=("Ты редактор научно-популярного канала «За гранью очевидного». "
"Напиши на русском ровно 4 предложения о том, как изучать серебристые облака: "
"что наблюдать, как проверять физическое объяснение по первоисточникам и "
"почему нельзя выдумывать источники. Без ссылки на несуществующие статьи.")
def request(url,token,payload=None,timeout=35):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=Request(url,data=data,headers={"Authorization":"Bearer "+token,
      "Content-Type":"application/json","User-Agent":"ZenThreeProviderDiagnostic/1.0"},
      method="POST" if data else "GET")
    try:
        with urlopen(req,timeout=timeout) as r:return json.load(r)
    except HTTPError as e:
        body=e.read(2048).decode("utf-8","replace")
        try: obj=json.loads(body)
        except (TypeError,ValueError):obj={}
        codes=[]
        def walk(v):
            if isinstance(v,dict):
                if isinstance(v.get("code"),int):codes.append(v["code"])
                for value in v.values():
                    if isinstance(value,(dict,list)):walk(value)
            elif isinstance(v,list):
                for value in v:walk(value)
        walk(obj)
        e.safe_codes=sorted(set(codes))[:6]
        e.retry_after=e.headers.get("Retry-After","")
        raise
def classify_error(e):
    result={"status":"error","error_type":type(e).__name__}
    if isinstance(e,HTTPError):
        result["http_status"]=e.code
        if getattr(e,"safe_codes",None):result["provider_codes"]=e.safe_codes
        retry=getattr(e,"retry_after","")
        if retry and retry.isdigit():result["retry_after_seconds"]=min(int(retry),86400)
        result["reason"]=(
          "daily_free_neurons_exhausted" if 3036 in result.get("provider_codes",[]) or 4006 in result.get("provider_codes",[]) else
          "temporary_capacity" if 3040 in result.get("provider_codes",[]) else
          "rate_or_quota_unknown" if e.code==429 else
          "access_or_model_error" if e.code in (401,403,404) else
          "other_http_error")
    return result
def call(url,token,model,prompt,max_tokens=750):
    data=request(url,token,{"model":model,"max_tokens":max_tokens,
       "messages":[{"role":"user","content":prompt}],"temperature":0.2},timeout=70)
    text=((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage=data.get("usage") or {}
    return text,usage
def main():
    env=os.environ
    report={"schema":"zen-three-provider-resilient-v1","gemini_calls":0,
      "publication_authorized":False,"paid_calls":0,"steps":[]}
    missing=[k for k in ("GROQ_API_KEY","CLOUDFLARE_ACCOUNT_ID","CLOUDFLARE_API_TOKEN","OPENROUTER_API_KEY") if not env.get(k)]
    if missing:
        report.update(status="blocked_missing_credentials",missing_names=missing)
        return report
    groq_url="https://api.groq.com/openai/v1/chat/completions"
    cf_url="https://api.cloudflare.com/client/v4/accounts/"+env["CLOUDFLARE_ACCOUNT_ID"]+"/ai/v1/chat/completions"
    router_url="https://openrouter.ai/api/v1/chat/completions"
    # Verify exact free model IDs and zero prices BEFORE completion calls.
    try:
        catalog=request("https://openrouter.ai/api/v1/models",env["OPENROUTER_API_KEY"])
        allowed={x["id"] for x in catalog.get("data",[]) if isinstance(x,dict)
          and x.get("id") in FREE and x["id"].endswith(":free")
          and str((x.get("pricing") or {}).get("prompt")) in ("0","0.0","0.00")
          and str((x.get("pricing") or {}).get("completion")) in ("0","0.0","0.00")}
    except (HTTPError,URLError,ValueError,TypeError,OSError) as e:
        report.update(status="free_model_preflight_error",detail=classify_error(e))
        return report
    original=None
    def step(name,provider,url,token,model,prompt,limit):
        start=time.monotonic()
        try:
            text,usage=call(url,token,model,prompt,limit)
            if not text.strip():raise ValueError("empty_visible_content")
            result={"stage":name,"provider":provider,"status":"responded",
                "characters":len(text),"seconds":round(time.monotonic()-start,2),
                "prompt_tokens":usage.get("prompt_tokens"),
                "completion_tokens":usage.get("completion_tokens")}
            report["steps"].append(result)
            return text
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError,KeyError) as e:
            result={"stage":name,"provider":provider,**classify_error(e),
                "seconds":round(time.monotonic()-start,2)}
            report["steps"].append(result)
            return None
    original=step("author","groq",groq_url,env["GROQ_API_KEY"],"openai/gpt-oss-120b",PROMPT,700)
    if original is None:report["status"]="author_failed";return report
    # Cloudflare first; one call only to prevent wasting account quota.
    edited=step("independent_edit","cloudflare",cf_url,env["CLOUDFLARE_API_TOKEN"],CF_MODELS[0],
        "Проверь русский стиль и логику, верни улучшенные 4 предложения; факты не выдумывай.\n"+original,650)
    if edited is None:
        report["cloudflare_route"]="temporarily_bypassed"
        # Always continue to confirmed FREE OpenRouter route.
    else: report["cloudflare_route"]="responded"
    candidate=edited or original
    critic_model=next((m for m in FREE if m in allowed),None)
    if critic_model:
        review=step("independent_critic","openrouter",router_url,env["OPENROUTER_API_KEY"],critic_model,
          "Проанализируй следующий текст по-русски, укажи 2 конкретные стилистические проблемы либо сообщи, что их нет. Не выдавай фактчекинг за выполненный.\n"+candidate,1150)
    else:
        report["steps"].append({"stage":"independent_critic","provider":"openrouter","status":"no_verified_free_model"})
        review=None
    report["status"]="three_providers_responded" if edited is not None and review is not None else (
       "two_providers_responded_cloudflare_degraded" if edited is None and review is not None else
       "partial_failure")
    return report
if __name__=="__main__":
    r=main()
    path=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-resilient-3provider.json"
    path.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(r,ensure_ascii=False))
