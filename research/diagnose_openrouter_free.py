#!/usr/bin/env python3
"""Read-only OpenRouter model catalog and account key diagnosis.

Never calls chat/completions, never makes paid API calls.
Never emits secret or full model catalog in public logs.
"""
import json
import os
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

CATALOG="https://openrouter.ai/api/v1/models"
AUTH="https://openrouter.ai/api/v1/auth/key"

def get_json(url,key,opener):
    request=Request(url,headers={"Authorization":"Bearer "+key,
        "Accept":"application/json","User-Agent":"ZenEditorialOpenRouterDiagnostic/1.0"})
    with opener(request,timeout=25) as response:
        return json.load(response)

def diagnose(env=None,opener=urlopen):
    env=os.environ if env is None else env
    key=env.get("OPENROUTER_API_KEY","")
    if not key:
        return {"status":"blocked_missing_secret","required":"OPENROUTER_API_KEY",
                "generation_calls":0}
    try:
        result=get_json(CATALOG,key,opener)
        entries=result.get("data",[])
        if not isinstance(entries,list):
            return {"status":"invalid_catalog_shape","generation_calls":0}
        deepseek=[]
        free=[]
        for item in entries:
            if not isinstance(item,dict):
                continue
            model=item.get("id","")
            if not isinstance(model,str):
                continue
            price=item.get("pricing") or {}
            # Only suffix :free is unambiguously a no-credit-purchase model.
            if model.endswith(":free"):
                free.append(model)
                if "deepseek" in model.lower():
                    deepseek.append(model)
        # auth/key may fail independently of model discovery.
        try:
            account=get_json(AUTH,key,opener).get("data") or {}
            account_status="available"
            # Monetary limit and usage are optional; never print account secrets.
            limit=account.get("limit")
            used=account.get("usage")
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError):
            account_status="unavailable"
            limit=used=None
        return {"status":"model_catalog_authenticated","catalog_entries":len(entries),
            "free_model_count":len(free),"free_deepseek_model_ids":sorted(deepseek),
            "free_model_examples":sorted(free)[:12],
            "deepseek_free_available":bool(deepseek),
            "key_info_status":account_status,
            "key_spend_limit":limit,"key_reported_usage":used,
            "generation_calls":0,"paid_calls":0,"publishing_authorized":False}
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError) as exc:
        return {"status":"catalog_access_failed",
                "error_type":type(exc).__name__,
                "http_status":exc.code if isinstance(exc,HTTPError) else None,
                "generation_calls":0,"paid_calls":0}
if __name__=="__main__":
    print(json.dumps(diagnose(),ensure_ascii=False))
