#!/usr/bin/env python3
"""Zero-cost OpenRouter free-model smoke test. Never emits generated text or credentials."""
import json, os, time, urllib.request, urllib.error
from pathlib import Path

ROOT="https://openrouter.ai/api/v1"
MODELS=["baidu/ernie-5.5-flash-preview:free","google/gemma-4-26b-a4b-it:free",
"google/gemma-4-31b-it:free","inclusionai/ling-3.2-flash:free",
"liquid/lfm-3-1.7b:free","nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
"nvidia/nemotron-3-ultra-550b-a55b:free","nvidia/nemotron-3.5-lightning:free",
"nvidia/nemotron-3.5-super-vl:free","particle-ai/particle-1:free",
"recursive/flint-v1.0:free"]
def request(path,key,payload=None,timeout=25):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=urllib.request.Request(ROOT+path,data=data,headers={
       "Authorization":"Bearer "+key,"Content-Type":"application/json",
       "User-Agent":"ZenEditorialFreeMatrix/1.0"},method="POST" if data else "GET")
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
def main():
    key=os.environ.get("OPENROUTER_API_KEY","")
    result={"schema":"zen-openrouter-free-matrix-v1","publication_authorized":False,
     "paid_calls":0,"generated_text_logged":False,"models":[]}
    if not key:
        result["status"]="missing_key"
    else:
        try:
            all_models=request("/models",key)
            catalog={m["id"]:m for m in all_models.get("data",[]) if isinstance(m,dict) and isinstance(m.get("id"),str)}
            available={id for id,v in catalog.items() if id.endswith(":free") and
                str((v.get("pricing") or {}).get("prompt")) in ("0","0.0","0.00") and
                str((v.get("pricing") or {}).get("completion")) in ("0","0.0","0.00")}
            result["catalog_free_count"]=len(available)
            result["deepseek_free_available"]=any("deepseek" in m.lower() for m in available)
            for model in MODELS:
                item={"model":model}
                if model not in available:item["status"]="not_free_in_catalog";result["models"].append(item);continue
                started=time.monotonic()
                try:
                    response=request("/chat/completions",key,{"model":model,"max_tokens":220,
                      "temperature":0.2,"messages":[{"role":"user","content":
                      "Напиши по-русски три коротких предложения для редакции научпопа: предложи тему о редком природном явлении, объясни зачем её проверять по первоисточникам и укажи, что нельзя выдумывать факты."}]},timeout=75)
                    choice=(response.get("choices") or [{}])[0]
                    content=(choice.get("message") or {}).get("content") or ""
                    usage=response.get("usage") or {}
                    item.update(status="responded" if content.strip() else "empty_content",
                      response_chars=len(content),elapsed_seconds=round(time.monotonic()-started,2),
                      output_tokens=usage.get("completion_tokens"),input_tokens=usage.get("prompt_tokens"))
                except urllib.error.HTTPError as e:
                    item.update(status="http_error",http_status=e.code,
                      elapsed_seconds=round(time.monotonic()-started,2))
                except (urllib.error.URLError,TimeoutError,OSError,ValueError,TypeError,KeyError) as e:
                    item.update(status="request_error",error_type=type(e).__name__,
                      elapsed_seconds=round(time.monotonic()-started,2))
                result["models"].append(item)
            result["status"]="completed_with_partial_or_full_results"
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,OSError,ValueError,TypeError) as e:
            result.update(status="catalog_error",error_type=type(e).__name__,
               http_status=e.code if isinstance(e,urllib.error.HTTPError) else None)
    path=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"zen-openrouter-free-matrix.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":main()
