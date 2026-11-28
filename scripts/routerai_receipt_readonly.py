"""Read-only receipt and control observation. No model calls or financial mutations."""
import os,json,urllib.request,urllib.error,urllib.parse
from decimal import Decimal
from datetime import datetime,timezone
GEN="rai-1795867204-VaaDvD7zCpcfVn24xAs7"
EXPECTED=Decimal("1.142495")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a):return None
def read(url,key,body=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers={"Authorization":"Bearer "+key,"Accept":"application/json","Content-Type":"application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as r:
            return json.loads(r.read(1000001),parse_float=Decimal),None
    except urllib.error.HTTPError as e:return None,e.code
    except Exception:return None,"unavailable"
def main():
    out={"paid_requests":0,"financial_mutations":0,"hold_release_authorized":False}
    d,err=read("https://routerai.ru/api/v1/generation?"+urllib.parse.urlencode({"id":GEN}),os.environ["ROUTERAI_API_KEY"])
    if isinstance(d,dict):
        row=d.get("data",d)
        if isinstance(row,dict):
            raw=row.get("total_cost")
            try:
                amount=Decimal(str(raw))
                valid=amount.is_finite() and amount>=0
            except Exception:
                valid=False
            out["provider"]={"id_matches":row.get("id")==GEN,"cost_rub":str(amount) if valid else None,"amount_matches_display":valid and abs(amount-EXPECTED)<=Decimal(".0000005"),"model_matches":row.get("model")=="anthropic/claude-sonnet-5.5","created_at":row.get("created_at") if isinstance(row.get("created_at"),str) else None,"keys":sorted(str(k) for k in row.keys())[:50]}
    else:out["provider"]={"http_error":err}
    state,err=read("https://zen-editorial-control.kvasrapr.workers.dev/internal/zen-core/account",os.environ["ZEN_CORE_INTAKE_SECRET"],{"action":"status"})
    if isinstance(state,dict) and state.get("schema")=="zen-core/account-status-v1":
        chat=state.get("chat")
        out["core"]={"production_hold":bool(state.get("production")),"chat_hold":bool(chat),"chat_status":chat.get("status") if isinstance(chat,dict) else None,"chat_started_at":datetime.fromtimestamp(chat["startedAt"]/1000,timezone.utc).isoformat() if isinstance(chat,dict) and type(chat.get("startedAt")) is int else None,"generation_id_exposed":isinstance(chat,dict) and "generationId" in chat,"generation_id_matches":isinstance(chat,dict) and chat.get("generationId")==GEN,"execution_enabled":state.get("executionEnabled") is True}
    else:out["core"]={"http_error":err}
    print("RECEIPT_READ_ONLY "+json.dumps(out,ensure_ascii=False))
if __name__=="__main__":main()
