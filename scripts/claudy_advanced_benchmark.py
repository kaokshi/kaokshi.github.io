"""Second isolated benchmark, synthetic code only; no generated code execution."""
import json
import os
import time
import urllib.request
import urllib.error
from decimal import Decimal
from claudy_isolated_benchmark import request,balance_value,NoRedirect,BASE,MODELS,RATES

TASKS=[
 ("opus","state_machine", """Return JSON only with keys accepted, balance, defects.
A queue starts with balance 10 and no processed IDs. For each (ID, cost), reject negative costs, duplicate accepted IDs and costs greater than remaining balance. Rejected IDs are not marked processed. Process [('a',4),('a',2),('b',8),('b',3),('c',-2),('d',3),('e',1)]. accepted is ordered list of accepted IDs; balance is final number. In defects, describe in Russian two bugs in this implementation: if seen.get(id): return; if cost > remaining: return; remaining -= cost; seen[id] = cost. Include the zero-cost duplicate and negative cost cases."""),
 ("opus","code_trace", """Return JSON only with keys outputs, final, fix. Trace:
def process(xs):
    cache={}
    for k,v in xs:
        cache.setdefault(k,[]).append(v)
    return cache
rows=process([('a',1),('b',2),('a',3)])
copy=rows.copy()
copy['a'].append(9)
outputs=[sum(rows['a']),sum(copy['a']),len(rows)]
final=rows['a']
Give outputs and final exactly. In fix explain in Russian how to isolate the nested lists."""),
 ("sonnet","manager", """You manage a test editorial queue. Never publish. Only propose jobs; never claim completed work. User asks: подготовь две статьи для кино и одну для науки завтра. Current Moscow date 2026-11-28. Return strictly JSON with keys reply, proposal, job. proposal must be null. job has items [{channel,count}], day ISO date and instructions. Allowed channels cinema, science, legends. No other channels. Use natural Russian reply."""),
 ("sonnet","long_context","Return only JSON with keys codes and amount. Find records 17, 419 and 798 in that order; codes is list of their code strings, amount is sum of their amounts. Ignore all others.\n"+"\n".join("record %04d code K%06d amount %d"%(i,i*31+7,i*13+2) for i in range(1,801))),
]
MAX=1600
CAP=Decimal("8")
def score(case,d):
    if not isinstance(d,dict): return False
    if case=="state_machine": return d.get("accepted")==["a","b","d"] and d.get("balance")==0 and isinstance(d.get("defects"),list) and len(d["defects"])==2
    if case=="code_trace": return d.get("outputs")==[13,13,2] and d.get("final")==[1,3,9] and bool(d.get("fix"))
    if case=="long_context": return d.get("codes")==["K000534","K012996","K024745"] and d.get("amount")==16048
    if case=="manager":
        j=d.get("job")
        return isinstance(j,dict) and d.get("proposal","missing") is None and isinstance(d.get("reply"),str) and j.get("day")=="2026-11-29" and sorted((x.get("channel"),x.get("count")) for x in j.get("items",[]))==[("cinema",2),("science",1)]
    return False

def call(key,payload):
    req=urllib.request.Request(BASE+"/v1/messages",data=json.dumps(payload,ensure_ascii=False).encode(),headers={"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json","Accept":"application/json"})
    start=time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=100) as r:
            return json.loads(r.read(2_000_000)),round(time.monotonic()-start,3)
    except urllib.error.HTTPError as e: raise RuntimeError("HTTP_"+str(e.code)) from None
    except Exception: raise RuntimeError("transport_or_invalid_json") from None

def main():
    key=os.environ.get("CLAUDY_API_KEY")
    if not key: raise RuntimeError("missing_key")
    initial=balance_value(request("/v1/me",key=key)[0])
    if initial is None: raise RuntimeError("balance_schema_unverified")
    bounds=[(Decimal(len(p.encode())+1024)*RATES[m][0]*2+MAX*RATES[m][1])/1_000_000 for m,_,p in TASKS]
    if sum(bounds)>CAP or initial<sum(bounds): raise RuntimeError("budget_preflight")
    print("ADV_START",json.dumps({"balance":str(initial),"estimated_upper":str(sum(bounds))}),flush=True)
    balance=initial
    estimate=Decimal(0)
    passed=0
    for m,case,p in TASKS:
        body,sec=call(key,{"model":MODELS[m],"max_tokens":MAX,"thinking":{"type":"adaptive"},"output_config":{"effort":"medium"},"messages":[{"role":"user","content":p}]})
        u=body.get("usage") or {}
        counts=[u.get("input_tokens"),u.get("output_tokens"),u.get("cache_read_input_tokens",0),u.get("cache_creation_input_tokens",0)]
        if any(type(x) is not int or x<0 for x in counts) or counts[1]>MAX or sum(counts[i] for i in (0,2,3))>len(p.encode())+1024: raise RuntimeError("usage_unverified")
        i,o,cr,cw=counts
        ri,ro=RATES[m]
        cost=(ri*i+ro*o+ri*Decimal("0.1")*cr+ri*Decimal("1.25")*cw)/1_000_000
        estimate+=cost
        content=body.get("content") or []
        raw="".join(x.get("text","") for x in content if x.get("type")=="text")
        try: d=json.loads(raw.strip().removeprefix("\x60\x60\x60json").removesuffix("\x60\x60\x60").strip())
        except Exception: d=None
        ok=body.get("model")==MODELS[m] and body.get("stop_reason")=="end_turn" and score(case,d)
        passed+=int(ok)
        new_balance=balance_value(request("/v1/me",key=key)[0])
        if new_balance is None or new_balance>balance or initial-new_balance>CAP: raise RuntimeError("billing_unverified")
        print("ADV_CASE",json.dumps({"case":case,"model":m,"pass":ok,"match":body.get("model")==MODELS[m],"stop":body.get("stop_reason") if body.get("stop_reason") in ("end_turn","max_tokens","tool_use","stop_sequence") else "other","input":i,"output":o,"cache_read":cr,"cache_write":cw,"thinking_blocks":sum(x.get("type")=="thinking" for x in content),"estimated_rub":str(cost),"actual_delta_rub":str(balance-new_balance),"seconds":sec}),flush=True)
        balance=new_balance
        if body.get("model")!=MODELS[m]: raise RuntimeError("model_mismatch")
    print("ADV_SUMMARY",json.dumps({"passed":passed,"total":len(TASKS),"estimated_rub":str(estimate),"actual_rub":str(initial-balance),"remaining_rub":str(balance)}),flush=True)
    if passed!=len(TASKS): raise RuntimeError("quality_not_confirmed")

if __name__=="__main__":
    try: main()
    except RuntimeError as e: print("ADV_STOP",str(e),flush=True); raise SystemExit(1)
    except Exception: print("ADV_STOP unexpected_schema",flush=True); raise SystemExit(1)
