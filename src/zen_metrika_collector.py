"""Read-only Metrika collector. No Dzen Studio metrics are inferred from web analytics."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

CHANNELS = {
    "cinema": (114654979, "dzen.ru/kadrzkadr"),
    "science": (114655465, "dzen.ru/sciencebox"),
    "legends": (114655507, "dzen.ru/darklegends"),
}
METRICS = "ym:s:visits,ym:s:users,ym:s:pageviews,ym:s:bounceRate,ym:s:pageDepth,ym:s:avgVisitDurationSeconds"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def get(token, path, params=None):
    url = "https://api-metrika.yandex.net" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "OAuth " + token, "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as r:
            return r.status, json.loads(r.read(2_000_001))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None

def normalize_site(value):
    return str(value or "").removeprefix("https://").removeprefix("http://").rstrip("/")

def channel_report(channel, token, get_json=get, today=None):
    cid, site = CHANNELS[channel]
    result = {"channel": channel, "counter_id": cid, "source": "yandex_metrika", "status": "unavailable",
              "binding_verified": False, "usable_for_editorial_advice": False, "periods": {}}
    if not token:
        result["reason"] = "token_missing"
        return result
    code, payload = get_json(token, "/management/v1/counter/" + str(cid))
    counter = (payload or {}).get("counter", {})
    if code != 200 or counter.get("id") != cid or normalize_site(counter.get("site")) != site:
        result["reason"] = "counter_identity_mismatch"
        return result
    result["configured_site"] = site
    for other, (other_id, _) in CHANNELS.items():
        if other != channel:
            code, _ = get_json(token, "/management/v1/counter/" + str(other_id))
            if code not in (403, 404):
                result["reason"] = "credential_isolation_unverified"
                return result
    today = today or datetime.now(timezone(timedelta(hours=3))).date()
    foreign_paths = {"/" + s.split("/", 1)[1] for k, (_, s) in CHANNELS.items() if k != channel}
    any_foreign = False
    for days in (7, 30):
        args = {"ids": cid, "date1": (today-timedelta(days=days)).isoformat(),
                "date2": (today-timedelta(days=1)).isoformat(), "accuracy": "full"}
        reports = {}
        for label, metrics, dim in (("totals", METRICS, None), ("daily", "ym:s:visits,ym:s:pageviews", "ym:s:date"),
                                    ("sources", "ym:s:visits", "ym:s:lastTrafficSource"),
                                    ("pages", "ym:pv:pageviews", "ym:pv:URL")):
            params = {**args, "metrics": metrics, "limit": 1000}
            if dim:
                params["dimensions"] = dim
            code, report = get_json(token, "/stat/v1/data", params)
            if code != 200 or not isinstance(report, dict) or report.get("sampled") is not False:
                result["reason"] = "report_unavailable_or_sampled"
                return result
            totals = report.get("totals")
            if not isinstance(totals, list) or len(totals) != len(metrics.split(",")) or any(type(x) not in (int,float) or not __import__("math").isfinite(x) or x < 0 for x in totals):
                result["reason"] = "invalid_report"
                return result
            rows = report.get("data")
            if not isinstance(rows,list):
                result["reason"] = "invalid_report"
                return result
            if label == "pages":
                if not isinstance(report.get("total_rows"),int) or report["total_rows"] > len(rows):
                    result["reason"] = "incomplete_page_evidence"
                    return result
                for row in rows:
                    for d in row.get("dimensions", []):
                        parsed = urllib.parse.urlsplit(str(d.get("name","")))
                        if parsed.hostname == "dzen.ru" and parsed.path.rstrip("/") in foreign_paths:
                            any_foreign = True
                # Do not retain raw page URLs or query strings.
                reports["page_evidence"] = {"foreign_channel_detected": any_foreign, "pageviews": totals[0]}
            else:
                reports[label] = {"totals": totals, "metrics": metrics.split(",")}
                if dim:
                    reports[label]["rows"] = [{"labels": [str(d.get("name","")) for d in r.get("dimensions",[])],
                                                 "metrics": r.get("metrics")} for r in rows]
        result["periods"][str(days)] = {"start": args["date1"], "end": args["date2"], **reports}
    # Intraday data is provisional and never used in completed-period comparisons.
    args = {"ids": cid, "date1": today.isoformat(), "date2": today.isoformat(),
            "accuracy": "full", "limit": 100}
    intraday = {"date": today.isoformat(), "status": "unavailable", "provisional": True}
    # The reporting API groups by the counter's own timezone.
    intraday["timezone"] = str(counter.get("time_zone_name") or counter.get("time_zone") or "")[:80] or None
    code, totals = get_json(token, "/stat/v1/data", {**args, "metrics": METRICS})
    def valid_report(code, payload, count):
        import math
        return (code == 200 and isinstance(payload, dict) and payload.get("sampled") is False
                and isinstance(payload.get("totals"), list) and len(payload["totals"]) == count
                and all(type(v) in (int,float) and math.isfinite(v) and v >= 0 for v in payload["totals"]))
    if valid_report(code, totals, 6):
        intraday["totals"] = totals["totals"]
        intraday["metrics"] = METRICS.split(",")
        intraday["status"] = "totals_only"
        code, hourly = get_json(token, "/stat/v1/data", {**args, "metrics": "ym:s:visits,ym:s:pageviews",
                                "dimensions": "ym:s:hour"})
        if valid_report(code, hourly, 2):
            rows = hourly.get("data")
            if isinstance(rows, list) and len(rows) <= 24 and hourly.get("total_rows") == len(rows):
                parsed, seen = [], set()
                for row in rows:
                    try:
                        label = row["dimensions"][0]["name"]
                        hour = int(label) if isinstance(label, str) and label.isdigit() else -1
                        values = row["metrics"]
                        if not 0 <= hour <= 23 or hour in seen or len(values) != 2 or any(type(v) not in (int,float) or v < 0 or not float(v).is_integer() for v in values):
                            raise ValueError()
                        seen.add(hour)
                        parsed.append({"hour":hour,"visits":int(values[0]),"pageviews":int(values[1])})
                    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
                        parsed = None
                        break
                if parsed is not None:
                    intraday["hourly"] = sorted(parsed, key=lambda x:x["hour"])
                    intraday["status"] = "available"
    result["intraday"] = intraday
    result["status"] = "mixed_channel_traffic" if any_foreign else "binding_unverified"
    result["reason"] = "foreign_channel_page_seen" if any_foreign else "studio_binding_and_live_hit_not_confirmed"
    return result

def collect():
    tokens = {k: os.environ.get("YANDEX_METRIKA_"+k.upper()+"_TOKEN","").strip() for k in CHANNELS}
    if not all(tokens.values()) or len(set(tokens.values())) != 3:
        raise ValueError("three_distinct_credentials_required")
    return {"schema": "zen-metrika/v1", "collected_at": datetime.now(timezone.utc).isoformat(),
            "channels": {k: channel_report(k,tokens[k]) for k in CHANNELS}}

if __name__ == "__main__":
    # Caller must redirect to private storage; never print report into public CI logs.
    with open(os.environ["ZEN_METRIKA_OUTPUT"], "x", encoding="utf-8") as f:
        json.dump(collect(), f, ensure_ascii=False, allow_nan=False)
