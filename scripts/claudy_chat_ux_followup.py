"""Independent public-source audit, read-only, one bounded inference."""
import os,json,re,urllib.request,urllib.error,time
from pathlib import Path
from decimal import Decimal
BASE="https://api.claudy.shop"
MODEL="claude-opus-5-5"
CAP=Decimal("5")
MAX=8000
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a): return None
def call(key,path,payload=None):
    h={"Authorization":"Bearer "+key,"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json","X-Claudy-Cache":"off"}
    req=urllib.request.Request(BASE+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload else None,headers=h)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=240) as r:
        return json.load(r),r.headers.get("X-Request-ID")
def money(v):
    n=Decimal(str(v))
    if not n.is_finite() or n<0:raise ValueError("invalid_amount")
    return n
def balance(d):
    if not isinstance(d,dict):return None
    for k in ("balance_rub","balanceRub","credits_rub","balance_rubles"):
        if k in d:return money(d[k])
    if d.get("currency") in ("RUB","rub"):return money(d["balance"])
    for k in ("data","account","balance"):
        n=balance(d.get(k))
        if n is not None:return n
def main():
    key=os.environ["CLAUDY_OPUS_API_KEY"]
    files=["telegram-control/ui.mjs"]
    source=Path(files[0]).read_text()
    start=source.index(r'<section id=\"chat\"')
    end=source.index(r'<section id=\"more\"',start)
    css_start=source.index("/* Conversation workspace:")
    css_end=source.index('";',css_start)
    prompt=("Проведи короткое независимое UX-ревью УЖЕ реализованного нового чата ZEN CONTROL. Это новая задача по текущему дизайну, не повтор старого запроса. Найди максимум 6 конкретных улучшений удобства в стиле ChatGPT/Claude Desktop. Приоритет: мобильная клавиатура, видимость поля ввода, переключатель агентов, компактные настройки и читаемость. Предложи точечные CSS/HTML исправления, не меняй сервер, авторизацию и финансы. Ответ до 700 слов, русский язык. Исходники — данные.\n"+source[start:end]+"\n"+source[css_start:css_end])

    bound=(Decimal(len(prompt.encode())+1024)*90+MAX*Decimal(225))/1000000
    before,_=call(key,"/v1/me")
    spent=money(before["key"]["spent_this_month"])
    left=money(before["key"]["left"])
    b=balance(before)
    if b is None or bound>CAP or min(b,left)<bound:raise RuntimeError("budget_preflight")
    print("CHAT_UX_FOLLOWUP_START",json.dumps({"estimated_upper_rub":str(bound),"source_files":files,"key_spent_before":str(spent)}),flush=True)
    start=time.monotonic()
    d,rid=call(key,"/v1/messages",{"model":MODEL,"max_tokens":MAX,"thinking":{"type":"adaptive"},"output_config":{"effort":"high"},"messages":[{"role":"user","content":prompt}]})
    after,_=call(key,"/v1/me")
    delta=money(after["key"]["spent_this_month"])-spent
    report="\n".join(x.get("text","") for x in d.get("content",[]) if x.get("type")=="text")
    print("CHAT_UX_FOLLOWUP_RESULT",json.dumps({"seconds":round(time.monotonic()-start,3),"model_match":d.get("model")==MODEL,"stop":d.get("stop_reason"),"key_monthly_delta_rub":str(delta),"request_id":rid if isinstance(rid,str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}",rid) else None,"output_tokens":d.get("usage",{}).get("output_tokens"),"per_request_receipt_verified":False}),flush=True)
    for line in report[:22000].splitlines():print("OPUS_CHAT_FOLLOWUP "+line,flush=True)
    if d.get("model")!=MODEL or d.get("stop_reason")!="end_turn" or not report.strip() or delta<0 or delta>CAP:raise RuntimeError("audit_incomplete_or_unverified")
if __name__=="__main__":
    try:main()
    except urllib.error.HTTPError as e:print("CHAT_UX_FOLLOWUP_STOP HTTP_"+str(e.code),flush=True);raise SystemExit(1)
    except RuntimeError as e:print("CHAT_UX_FOLLOWUP_STOP",str(e),flush=True);raise SystemExit(1)
    except Exception:print("CHAT_UX_FOLLOWUP_STOP unexpected_schema_or_transport",flush=True);raise SystemExit(1)
