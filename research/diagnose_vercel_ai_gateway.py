#!/usr/bin/env python3
"""Read-only Vercel AI Gateway connection diagnosis. Never makes billable calls.

No API-key/model-list body is printed, no prompts or article text are sent.
"""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError,URLError

def diagnose(env=None,opener=urlopen):
    env=os.environ if env is None else env
    key=env.get("AI_GATEWAY_API_KEY","")
    if not key:
        return {"status":"blocked_missing_secret","required":"AI_GATEWAY_API_KEY"}
    request=Request("https://ai-gateway.vercel.sh/v1/models",
        headers={"Authorization":"Bearer "+key,"Accept":"application/json",
                 "User-Agent":"ZenEditorialReadOnlyDiagnostic/1.0"})
    try:
        with opener(request,timeout=30) as response:
            data=json.load(response)
        models=data.get("data",[])
        if not isinstance(models,list):
            return {"status":"unexpected_response_schema"}
        ids={item.get("id","") for item in models if isinstance(item,dict)}
        # No model calls or content: public status only. These are candidate names,
        # not a claim that free credits cover a model.
        candidates=("deepseek/deepseek-v4.1-flash","nvidia/nemotron-3.5-lightning")
        return {"status":"authenticated_model_catalog_access",
                "catalog_models":len(ids),
                "candidate_ids_visible":{x:x in ids for x in candidates},
                "generation_checked":False,"free_credit_eligibility_checked":False,
                "chargeable_calls":0,"publication_authorized":False}
    except (HTTPError,URLError,TimeoutError,OSError,ValueError,TypeError) as exc:
        return {"status":"catalog_access_failed","error_type":type(exc).__name__,
                "http_status":exc.code if isinstance(exc,HTTPError) else None,
                "generation_checked":False,"chargeable_calls":0}
if __name__=="__main__":
    print(json.dumps(diagnose(),ensure_ascii=False))
