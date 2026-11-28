import os,json,urllib.request
from decimal import Decimal
key=os.environ["CLAUDY_OPUS_API_KEY"]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a): return None
try:
    req=urllib.request.Request("https://api.claudy.shop/v1/me",headers={"Authorization":"Bearer "+key,"Accept":"application/json"})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as r:
        d=json.load(r)
    k=d["key"]
    spent=Decimal(str(k["spent_this_month"]))
    left=Decimal(str(k["left"]))
    if not spent.is_finite() or not left.is_finite() or spent<0 or left<0:raise ValueError()
    print("OPUS_READ_ONLY "+json.dumps({"status":"ok","monthly_spent_rub":str(spent),"monthly_left_rub":str(left),"delta_since_failed_request_rub":str(spent-Decimal("11.58")),"per_request_receipt_verified":False,"paid_requests":0}))
except Exception as e:
    print("OPUS_READ_ONLY "+json.dumps({"status":"unavailable","http":getattr(e,"code",None),"paid_requests":0}))
    raise SystemExit(1)
