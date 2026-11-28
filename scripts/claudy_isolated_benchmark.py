"""Isolated synthetic provider benchmark. Never imports the production editor."""
import json
import os
import time
import urllib.request
import urllib.error
from decimal import Decimal

BASE = "https://api.claudy.shop"
TASKS = [
    ("opus", "python", """Return ONLY a JSON object with keys duplicate, empty, normal, bugs.
Consider:
def reserve(rows, key, amount):
    if rows.get(key): return "duplicate"
    rows[key] = amount
    return "ok"
def total(rows): return sum(rows.values()) / len(rows)
def add(xs, x, seen=set()):
    if x not in seen: xs.append(x); seen.add(x)
    return xs
What does reserve({"a":0},"a",2) return? What exception does total({}) raise?
What does total({"a":2,"b":6}) return? bugs must be an array of the three line-specific defects, explained briefly in Russian.
"""),
    ("opus", "javascript", """Return ONLY JSON with keys actual, expected, reason.
function accept(items) {
 const seen = new Set();
 return items.filter(x => {if (seen.has(x.id)) return true; seen.add(x); return true;});
}
For input [{id:1},{id:1},{id:2}], actual and expected are arrays of IDs before and after correcting the bug. Explain the fix in Russian."""),
    ("sonnet", "editorial", """Return ONLY JSON with keys unsupported, corrected.
Source: A study included 80 adults in a single city. Participants reported sleep duration; a statistical association with memory scores was found. No causal intervention took place.
Draft claims: (1) The study proves sleeping longer improves everyone's memory. (2) It included 80 adults. (3) Researchers followed 800 children. (4) The study found an association.
unsupported must be an array of the unsupported claim numbers. corrected must be a natural Russian sentence faithfully reflecting the source."""),
    ("sonnet", "context", "Return ONLY JSON with keys code and amount for record ZEN-042. Do not use other records.\n" +
     "\n".join("record ZEN-%03d: code R%03d, amount %d" % (i,i*3,i*7) for i in range(1,161))),
]
MODELS = {"opus":"claude-opus-5-5","sonnet":"claude-sonnet-5-5"}
RATES = {"opus":(Decimal("45"),Decimal("225")),"sonnet":(Decimal("30"),Decimal("150"))}
MAX_OUTPUT=700
CAP=Decimal("10")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def request(path, payload=None, key=None):
    headers={"Accept":"application/json"}
    if key: headers["Authorization"]="Bearer "+key
    data=None
    if payload is not None:
        headers["Content-Type"]="application/json"
        data=json.dumps(payload,ensure_ascii=False).encode()
    req=urllib.request.Request(BASE+path, data=data, headers=headers)
    start=time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=90) as res:
            body=json.loads(res.read(2_000_000))
            return body, round(time.monotonic()-start,3)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP_"+str(exc.code)) from None
    except Exception:
        raise RuntimeError("transport_or_invalid_json") from None

def score(case,d):
    if not isinstance(d,dict): return False
    if case=="python": return d.get("duplicate")=="ok" and d.get("empty")=="ZeroDivisionError" and d.get("normal")==4 and isinstance(d.get("bugs"),list) and len(d["bugs"])==3
    if case=="javascript": return d.get("actual")==[1,1,2] and d.get("expected")==[1,2] and isinstance(d.get("reason"),str)
    if case=="editorial": return isinstance(d.get("unsupported"),list) and sorted(d["unsupported"])==[1,3] and isinstance(d.get("corrected"),str)
    if case=="context": return d.get("code")=="R126" and d.get("amount")==294
    return False

def balance_value(obj):
    # Only explicitly denominated ruble fields; never assume units of generic balance.
    if not isinstance(obj,dict): return None
    for k in ("balance_rub","balanceRub","credits_rub","balance_rubles"):
        if k in obj:
            try:
                d=Decimal(str(obj[k]))
                if d.is_finite() and d>=0: return d
            except Exception: pass
    if obj.get("currency") in ("RUB","rub"):
        try:
            d=Decimal(str(obj.get("balance")))
            if d.is_finite() and d>=0: return d
        except Exception: pass
    for k in ("data","account","balance"):
        d=balance_value(obj.get(k))
        if d is not None: return d
    return None

def main():
    key=os.environ.get("CLAUDY_API_KEY")
    if not key: raise RuntimeError("missing_key")
    models,_=request("/v1/models")
    ids={x.get("id") for x in models.get("data",[]) if isinstance(x,dict)}
    if not set(MODELS.values())<=ids: raise RuntimeError("models_missing")
    print("CLAUDY_CATALOG",json.dumps({"opus":True,"sonnet":True,"deepseek":any("deepseek" in str(x).lower() for x in ids),"image_models":any(any(y in str(x).lower() for y in ("seedream","flux","image")) for x in ids)}),flush=True)
    account,_=request("/v1/me",key=key)
    before=balance_value(account)
    print("CLAUDY_ACCOUNT_AUTH_OK",flush=True)
    print("CLAUDY_BALANCE_RUB",str(before) if before is not None else "schema_unverified",flush=True)
    # Conservative UTF-8 byte input estimate plus envelope; cache write at 2x.
    bounds=[]
    for model,case,prompt in TASKS:
        rate,out=RATES[model]
        bounds.append((Decimal(len(prompt.encode())+1024)*rate*2+MAX_OUTPUT*out)/1_000_000)
    if sum(bounds)>CAP: raise RuntimeError("estimated_budget_exceeded")
    if before is None: raise RuntimeError("balance_schema_unverified")
    if before<sum(bounds): raise RuntimeError("balance_insufficient")
    print("CLAUDY_ESTIMATED_MAX_RUB",str(sum(bounds)),flush=True)
    est=Decimal(0)
    passed=0
    for model,case,prompt in TASKS:
        body,seconds=request("/v1/chat/completions",{"model":MODELS[model],"messages":[{"role":"user","content":prompt}],"max_tokens":MAX_OUTPUT,"stream":False},key)
        usage=body.get("usage") or {}
        i,o=usage.get("prompt_tokens"),usage.get("completion_tokens")
        if type(i) is not int or type(o) is not int or min(i,o)<0 or o>MAX_OUTPUT or i>len(prompt.encode())+1024: raise RuntimeError("usage_unverified")
        rate,out=RATES[model]
        details=usage.get("prompt_tokens_details") or {}
        cached=details.get("cached_tokens",0)
        if type(cached) is not int or not 0<=cached<=i: raise RuntimeError("cache_usage_unverified")
        # No explicit cache requested. Report standard tariff estimate; actual debit is separate.
        cost=(Decimal(i-cached)*rate+Decimal(cached)*rate/10+Decimal(o)*out)/1_000_000
        est+=cost
        choices=body.get("choices") or [{}]
        choice=choices[0]
        raw=(choice.get("message") or {}).get("content") or ""
        try: d=json.loads(raw.strip().removeprefix("\x60\x60\x60json").removesuffix("\x60\x60\x60").strip())
        except Exception: d=None
        model_ok=body.get("model")==MODELS[model]
        ok=bool(model_ok and choice.get("finish_reason")=="stop" and score(case,d))
        passed+=int(ok)
        print("CLAUDY_CASE",json.dumps({"model":model,"case":case,"model_match":model_ok,"finish":choice.get("finish_reason") if choice.get("finish_reason") in ("stop","length","content_filter","tool_calls") else "other","input":i,"output":o,"cached":cached,"estimated_rub":str(cost),"seconds":seconds,"pass":ok}),flush=True)
        if not model_ok: raise RuntimeError("model_mismatch")
        if est>CAP: raise RuntimeError("estimated_budget_exceeded")
    after=balance_value(request("/v1/me",key=key)[0])
    actual=before-after if before is not None and after is not None else None
    print("CLAUDY_SUMMARY",json.dumps({"passed":passed,"total":len(TASKS),"estimated_rub":str(est),"balance_delta_rub":str(actual) if actual is not None else None}),flush=True)
    if passed!=len(TASKS) or actual is None or actual<0 or actual>CAP:
        raise RuntimeError("quality_or_billing_not_confirmed")

if __name__=="__main__":
    try: main()
    except RuntimeError as e:
        print("CLAUDY_STOP",str(e),flush=True)
        raise SystemExit(1)
    except Exception:
        print("CLAUDY_STOP unexpected_schema",flush=True)
        raise SystemExit(1)
