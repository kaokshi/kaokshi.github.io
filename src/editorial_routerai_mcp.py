"""Read-only RouterAI MCP diagnostic. Never invokes inference or changes ledger."""
import json, os, urllib.request, urllib.error
token = ""
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None
opener = urllib.request.build_opener(NoRedirect)
session = None
version = "2025-03-26"
def rpc(method, params, ident=None):
    global session
    data = {"jsonrpc":"2.0","method":method,"params":params}
    if ident is not None: data["id"] = ident
    headers = {"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json, text/event-stream","MCP-Protocol-Version":version}
    if session: headers["Mcp-Session-Id"] = session
    req = urllib.request.Request("https://routerai.ru/api/v1/mcp", json.dumps(data).encode(), headers)
    try:
        with opener.open(req, timeout=20) as res:
            session = res.headers.get("Mcp-Session-Id") or session
            if ident is None: return {}
            if "text/event-stream" in res.headers.get("Content-Type",""):
                for line in res:
                    if line.startswith(b"data:"):
                        obj=json.loads(line[5:])
                        if obj.get("id")==ident: break
                else: raise SystemExit("No matching MCP response")
            else: obj=json.loads(res.read(2000000))
    except urllib.error.HTTPError as exc:
        raise SystemExit("MCP HTTP status "+str(exc.code)) from None
    except Exception:
        raise SystemExit("MCP transport or parsing failure") from None
    if "error" in obj: raise SystemExit("MCP RPC rejected: "+method)
    return obj.get("result",{})

def main():
    global token, version
    import argparse
    from datetime import date
    p=argparse.ArgumentParser()
    p.add_argument("mode", choices=["balance","statement"])
    p.add_argument("--from-date", default="2026-11-25")
    p.add_argument("--to-date", default="2026-11-27")
    args=p.parse_args()
    token=os.environ.get("ROUTERAI_MCP_TOKEN","")
    if not token.startswith("rmcp_"):
        raise SystemExit("MCP token absent or invalid format")
    init=rpc("initialize", {"protocolVersion":version,"capabilities":{},"clientInfo":{"name":"zen-finance-readonly","version":"1.0"}},1)
    version=init.get("protocolVersion",version)
    rpc("notifications/initialized", {})
    names={t["name"] for t in rpc("tools/list",{},2).get("tools",[])}
    name={"balance":"get_balance","statement":"get_statement"}[args.mode]
    if name not in names: raise SystemExit("Required tool unavailable")
    params={}
    if args.mode=="statement":
        start,end=date.fromisoformat(args.from_date),date.fromisoformat(args.to_date)
        if not 0 <= (end-start).days <= 7: raise SystemExit("Date range invalid")
        params={"from":start.isoformat(),"to":end.isoformat(),"limit":100}
    result=rpc("tools/call",{"name":name,"arguments":params},3)
    if result.get("isError"): raise SystemExit("MCP read rejected")
    # Only schema metadata goes to public logs. No balance, transactions or IDs.
    obj=result.get("structuredContent")
    if obj is None:
        for item in result.get("content",[]):
            if item.get("type")=="text":
                try: obj=json.loads(item.get("text",""))
                except (ValueError,TypeError): continue
                break
    def shape(x,depth=0):
        if depth>3:return type(x).__name__
        if isinstance(x,dict):
            # Only documented-looking field names; never arbitrary map keys.
            fields={"data","items","entries","transactions","operations","records","result","balance","amount","currency","type","date","created_at","createdAt","id","generation_id","generationId","status","next_cursor","nextCursor","cursor","has_more","hasMore","total","limit","cost","description","model","timestamp","period","from","to","pagination"}
            return {k:shape(v,depth+1) for k,v in x.items() if k in fields}
        if isinstance(x,list):return {"count":len(x),"first_shape":shape(x[0],depth+1) if x else None}
        return type(x).__name__
    print(json.dumps({"mode":args.mode,"status":"read_ok","shape":shape(obj),"ledgerChanged":False}))
if __name__=="__main__":
    main()
