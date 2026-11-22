#!/usr/bin/env python3
"""Private Groq API diagnostic: never log headers, tokens, bodies or raw messages."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT="https://api.groq.com/openai/v1"
MAX_BODY=8192
# Explicit allowlist: do not surface opaque upstream strings (could echo secrets).
KNOWN_CODES={
    "invalid_api_key","invalid_request_error","authentication_error",
    "permission_error","insufficient_quota","rate_limit_exceeded",
    "model_permission_blocked_org","model_permission_blocked_project",
    "model_decommissioned","model_not_found","access_denied",
    "permission_denied","organization_restricted",
    "organization_disabled","ip_not_allowed","region_not_supported",
    "unsupported_country_region_territory",
}
CLASSIFIERS={
    "cloudflare_challenge":("cloudflare","challenge"),
    "geographic_restriction":("region","country","location","geograph","territor"),
    "organization_permission":("organization","org_","permission"),
    "project_permission":("project","allowlist","allowed models"),
    "invalid_credential":("invalid api key","invalid_api_key","authentication"),
    "network_policy":("ip address","blocked ip","ip_not_allowed"),
}

def diagnostic_error(exc):
    report={"http_status":exc.code,"error_code":"unclassified",
            "error_type":"unknown","classification":"unclassified"}
    raw=exc.read(MAX_BODY+1)
    if len(raw)>MAX_BODY:
        report["classification"]="oversized_error_body"
        return report
    try:
        payload=json.loads(raw)
    except (ValueError,TypeError):
        report["classification"]="non_json_response"
        return report
    if not isinstance(payload,dict) or not isinstance(payload.get("error"),dict):
        report["classification"]="unrecognized_error_schema"
        return report
    error=payload["error"]
    for key in ("code","type"):
        value=error.get(key)
        if isinstance(value,str) and value in KNOWN_CODES:
            report["error_code" if key=="code" else "error_type"]=value
    if report["error_code"] == "model_permission_blocked_org":
        report["classification"]="organization_permission"
        return report
    if report["error_code"] == "model_permission_blocked_project":
        report["classification"]="project_permission"
        return report
    msg=error.get("message","")
    if isinstance(msg,str):
        lower=msg.lower()
        for label,patterns in CLASSIFIERS.items():
            if any(pattern in lower for pattern in patterns):
                report["classification"]=label
                break
    return report

def check(path,key,request_fn=urlopen):
    headers={"Accept":"application/json","Authorization":"Bearer "+key,
             "User-Agent":"ZenEditorialAPIDiagnostic/1.0"}
    request=Request(ENDPOINT+path,headers=headers)
    try:
        with request_fn(request,timeout=25) as response:
            limited=response.read(32768)
            try:
                obj=json.loads(limited)
            except (ValueError,TypeError):
                return {"status":"unexpected_success_body",
                        "http_status":response.status}
            if not isinstance(obj,dict):
                return {"status":"unexpected_success_body",
                        "http_status":response.status}
            if path=="/models":
                ids={m.get("id") for m in obj.get("data",[])
                     if isinstance(m,dict)}
                return {"status":"ok","http_status":response.status,
                        "target_model_listed":"openai/gpt-oss-120b" in ids}
            return {"status":"ok","http_status":response.status}
    except HTTPError as exc:
        return {"status":"http_rejected",**diagnostic_error(exc)}
    except (URLError,TimeoutError,OSError,ValueError) as exc:
        return {"status":"network_error","error_type":type(exc).__name__}

def run(env=None,request_fn=urlopen):
    env=os.environ if env is None else env
    key=env.get("GROQ_API_KEY","")
    result={"schema":"zen-groq-safe-diagnostic-v1",
            "publication_authorized":False,
            "key_present":bool(key),
            "endpoint":"/openai/v1/models"}
    if not key:
        result["status"]="missing_secret"
        return result
    result["models_api"]=check("/models",key,request_fn=request_fn)
    result["status"]="completed_diagnostic"
    return result

def main():
    path=Path(os.environ.get("RUNNER_TEMP","/tmp"))/"groq-safe-diagnostic.json"
    report=run()
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                    encoding="utf-8")
    path.chmod(0o600)
    # Log only safe diagnostic data (no key, raw response or request headers).
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
