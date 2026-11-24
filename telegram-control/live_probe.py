"""Live isolated Worker -> real SQLite Core -> Worker receipt. No provider imports or calls."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.editorial_zen_core import Core
from intake_client import NoRedirect, accept, build_receipt

def main():
    run = os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    secret = os.environ["ZEN_CORE_INTAKE_SECRET"]
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    if not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,3}", run) or not re.fullmatch(r"[a-fA-F0-9]{64}", secret) or not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise ValueError("invalid_configuration")
    opener = build_opener(NoRedirect())
    with opener.open(Request("https://api.cloudflare.com/client/v4/accounts/"+account+"/workers/subdomain", headers={"Authorization":"Bearer "+token}), timeout=15) as response:
        cf = json.load(response)
    sub = cf.get("result", {}).get("subdomain", "")
    if cf.get("success") is not True or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", sub):
        raise ValueError("subdomain_unverified")
    url = "https://zen-editorial-control."+sub+".workers.dev/internal/zen-core/probe"
    def call(action, *, authorized=True, **extra):
        data = json.dumps({"run":run,"action":action,**extra}).encode()
        headers = {"Content-Type":"application/json"}
        if authorized:
            headers["Authorization"] = "Bearer "+secret
        with opener.open(Request(url, data=data, headers=headers, method="POST"), timeout=20) as res:
            raw = res.read(16385)
            if len(raw)>16384:
                raise ValueError("probe_response_too_large")
            return json.loads(raw)
    try:
        call("start", authorized=False)
        raise ValueError("unauthorized_request_accepted")
    except HTTPError as exc:
        if exc.code != 401:
            raise
    print("PROBE_AUTH_GATE_OK")
    expected = "probe:"+run
    try:
        start = call("start")
        assert start["id"] == expected and start["executionEnabled"] is False
        assert call("queue")["ids"] == [expected]
        envelope = call("intake")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"probe.sqlite"
            core = Core(path)
            try:
                assert accept(core, envelope, expected) == expected
                assert accept(core, envelope, expected) == expected
            finally:
                core.db.close()
            core = Core(path)
            try:
                snapshot = core.snapshot()
                assert len(snapshot["jobs"]) == 1 and len(snapshot["jobs"][0]["articles"]) == 2
                assert snapshot["budget"]["meteredRub"] == "0"
                report = build_receipt(core, expected)
            finally:
                core.db.close()
        print("PROBE_EXECUTOR_PERSISTENCE_OK")
        assert call("receipt", receipt=report)["accepted"] is True
        assert call("receipt", receipt=report)["replay"] is True
        assert call("queue")["ids"] == []
        assert call("verify") == {"accepted":True,"articles":2,"executionEnabled":False,"publicationAuthorized":False,"rssEnabled":False}
        assert call("control")["executionEnabled"] is False
        print("PROBE_RECEIPT_REPLAY_OK")
    finally:
        assert call("cleanup")["cleaned"] is True
        print("PROBE_ISOLATED_STORAGE_CLEANED")
    print("LIVE_PROBE_OK_NO_PAID_REQUEST_NO_PUBLICATION")

if __name__ == "__main__":
    try:
        main()
    except HTTPError as exc:
        print("PROBE_FAILED_HTTP_"+str(exc.code))
        sys.exit(1)
    except Exception as exc:
        print("PROBE_FAILED_"+type(exc).__name__)
        sys.exit(1)
