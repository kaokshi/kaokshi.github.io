"""Optional read-only Cloud Monitoring diagnostic for Gemini Developer API quotas.

Access requires GitHub OIDC + a Monitoring Viewer service account. Free projects
can return 403 or no data. Never infer official remaining quota from partial
time-series samples or from the Gemini models:list response.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
METRICS = {
    "usage": "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/usage",
    "limit": "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/limit",
}


def pacific_day_start(now=None):
    stamp = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def metric_snapshot(project, token, model, *, now=None, fetch=urlopen):
    report = {
        "status": "not_configured",
        "model": model,
        "remaining_rpd": None,
        "remaining_rpm": None,
        "remaining_tpm": None,
        "authoritative_balance": False,
    }
    if not project or not token:
        return report
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project):
        return {**report, "status": "invalid_project_identifier"}
    start = pacific_day_start(now)
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    report["day_start_utc"] = start.isoformat(timespec="seconds")
    report["status"] = "unavailable"
    for kind, metric in METRICS.items():
        q = urlencode({
            "filter": f'metric.type="{metric}"',
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.isoformat().replace("+00:00", "Z"),
            "pageSize": "1000",
        })
        url = ("https://monitoring.googleapis.com/v3/projects/" +
               quote(project, safe="") + "/timeSeries?" + q)
        req = Request(url, headers={"Authorization": "Bearer " + token,
                                   "Accept": "application/json"})
        try:
            with fetch(req, timeout=20) as response:
                payload = json.loads(response.read(2_000_000))
        except HTTPError as exc:
            report[kind] = {"status": "unavailable", "http_status": exc.code}
            continue
        except (OSError, TimeoutError, URLError, ValueError, UnicodeDecodeError):
            report[kind] = {"status": "unavailable"}
            continue
        rows = payload.get("timeSeries", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or payload.get("nextPageToken"):
            report[kind] = {"status": "incomplete_or_paginated"}
            continue
        records = []
        for row in rows:
            labels = (row.get("metric") or {}).get("labels") or {}
            if labels.get("model") != model:
                continue
            # Only safe metadata; do not sum overlapping quota samples.
            points = row.get("points") or []
            value = None
            if points:
                v = points[0].get("value") or {}
                try:
                    value = int(v["int64Value"]) if "int64Value" in v else float(v["doubleValue"])
                except (KeyError, ValueError, TypeError):
                    pass
            records.append({"latest_point": value,
                            "quota_limit_name": str(labels.get("limit_name", ""))[:100]})
        report[kind] = {"status": "observed" if records else "no_model_data",
                        "matching_series": len(records),
                        "samples": records[:20]}
        if records:
            report["status"] = "partial_observations"
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    args = parser.parse_args()
    report = metric_snapshot(os.environ.get("GOOGLE_QUOTA_MONITOR_PROJECT", ""),
                             os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN", ""), args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Google Cloud Gemini monitoring:", report["status"])
    print("Project free RPM/TPM/RPD remaining: unknown unless confirmed separately in AI Studio")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\nCloud Monitoring Gemini free-tier usage: **" + report["status"] +
                    "**. Official remaining project quota is **unknown**.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
