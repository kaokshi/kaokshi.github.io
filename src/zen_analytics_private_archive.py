"""Private, append-only Metrika report archives; native Studio data stays separate."""
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from .zen_analytics_history import canonical, metrika_snapshot
from .zen_studio_seed import decode_seed

REPO = "kaokshi/zen-editorial-review"
LATEST = "zen-analytics/collector-validation.json"
MAX_REPORT = 100000

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def client(token):
    if not token:
        raise ValueError("private_token_missing")
    opener = urllib.request.build_opener(NoRedirect)
    def req(path, body=None):
        r = urllib.request.Request("https://api.github.com/repos/"+REPO+path,
            headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
                     "User-Agent":"ZEN-Analytics/1.0","Content-Type":"application/json"},
            data=json.dumps(body).encode() if body is not None else None,
            method="PUT" if body is not None else "GET")
        try:
            with opener.open(r,timeout=20) as resp:
                return resp.status,json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code,None
    return req

def private_branch(req):
    status, meta = req("")
    if status!=200 or not isinstance(meta,dict) or meta.get("full_name")!=REPO or meta.get("private") is not True or meta.get("visibility")!="private":
        raise ValueError("private_repo_unverified")
    branch=meta.get("default_branch")
    if not isinstance(branch,str) or not re.fullmatch(r"[A-Za-z0-9_-]+",branch):
        raise ValueError("private_branch_invalid")
    return branch

def fetch_content(req,path,branch):
    status,data=req("/contents/"+path+"?ref="+branch)
    if status==404:
        return None,None
    if status!=200 or not isinstance(data,dict) or data.get("type")!="file" or data.get("encoding")!="base64" or not isinstance(data.get("sha"),str):
        raise ValueError("private_read_failed")
    return base64.b64decode("".join(data["content"].split()),validate=True), data["sha"]

def put_content(req,path,branch,raw,message,sha=None):
    body={"message":message,"branch":branch,"content":base64.b64encode(raw).decode()}
    if sha is not None:
        body["sha"]=sha
    return req("/contents/"+path,body)[0]

def stamp(report):
    t=datetime.fromisoformat(report["collected_at"].replace("Z","+00:00"))
    if t.tzinfo is None:
        raise ValueError("timezone_required")
    return t.astimezone(timezone.utc)

def validated_report(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_REPORT:
        raise ValueError("invalid_report_size")
    report=json.loads(raw)
    metrika_snapshot(report)
    stamp(report)
    for e in report["channels"].values():
        if (e.get("status") not in ("binding_unverified","mixed_channel_traffic")
                or e.get("binding_verified") is not False
                or e.get("usable_for_editorial_advice") is not False
                or set(e.get("periods",{}))!={"7","30"}):
            raise ValueError("collector_incomplete_or_untrusted")
    return report

def archive(req,raw,*,update_latest=False):
    report=validated_report(raw)
    branch=private_branch(req)
    data=canonical(report)
    digest=hashlib.sha256(data).hexdigest()
    # One immutable object per collection preserves the three-channel batch.
    path="zen-analytics/metrika/history/"+stamp(report).strftime("%Y/%m/%d")+"/"+digest+".json"
    old,_=fetch_content(req,path,branch)
    created=False
    if old is None:
        code=put_content(req,path,branch,data,"Archive independent Metrika snapshot")
        if code not in (200,201,409,422):
            raise ValueError("archive_write_failed")
        created=code in (200,201)
        old,_=fetch_content(req,path,branch)
    if old!=data:
        raise ValueError("archive_readback_mismatch")
    latest="unchanged"
    if update_latest:
        old,sha=fetch_content(req,LATEST,branch)
        if old is not None:
            previous=validated_report(old)
            if stamp(previous)>stamp(report):
                return {"archived":created,"latest":"newer_exists"}
            if stamp(previous)==stamp(report):
                if canonical(previous)!=data:
                    raise ValueError("conflicting_report_timestamp")
                return {"archived":created,"latest":"replay"}
        code=put_content(req,LATEST,branch,raw,"Update latest independent Metrika report",sha)
        if code not in (200,201):
            raise ValueError("latest_cas_failed")
        latest="updated"
    return {"archived":created,"latest":latest}

def verify_seed_and_archive_latest(req,seed_path):
    if seed_path == "discover":
        branch=private_branch(req)
        status, entries=req("/contents/zen-analytics/studio/owner-seeds?ref="+branch)
        if status!=200 or not isinstance(entries,list):
            raise ValueError("seed_listing_failed")
        candidates=[e.get("path","") for e in entries if e.get("type")=="file" and re.fullmatch(r"zen-analytics/studio/owner-seeds/[0-9a-f]{64}\.json",e.get("path",""))]
        if len(candidates)!=1:
            raise ValueError("seed_selection_ambiguous")
        seed_path=candidates[0]
    if not re.fullmatch(r"zen-analytics/studio/owner-seeds/[0-9a-f]{64}\.json",seed_path):
        raise ValueError("invalid_seed_path")
    branch=private_branch(req)
    raw,_=fetch_content(req,seed_path,branch)
    if raw is None:
        raise ValueError("seed_missing")
    seed=decode_seed(raw)
    if json.loads(raw)["sha256"]!=seed_path.rsplit("/",1)[1][:-5]:
        raise ValueError("seed_filename_mismatch")
    current,_=fetch_content(req,LATEST,branch)
    if current is None:
        raise ValueError("latest_missing")
    result=archive(req,current)
    return {"seed_channels":len(seed["reports"]),"metrika_archive_created":result["archived"]}

if __name__=="__main__":
    req=client(os.environ.get("EDITORIAL_PRIVATE_TOKEN"))
    if os.environ.get("ZEN_ANALYTICS_SEED_CHECK"):
        result=verify_seed_and_archive_latest(req,os.environ["ZEN_ANALYTICS_SEED_CHECK"])
    else:
        with open(os.environ["ZEN_METRIKA_OUTPUT"],"rb") as f:
            result=archive(req,f.read(MAX_REPORT+1),update_latest=True)
    print(json.dumps(result,sort_keys=True))
