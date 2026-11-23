"""Read-only provider quota snapshot for the Zen editorial project.

One cheap Groq chat completion may be used solely to expose its rate-limit headers.
Gemini GET models and Cloudflare GraphQL do not invoke any image/text generation.
No secrets or raw response bodies are ever written to the report/logs.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path("quota-monitor-out")
OUT.mkdir(parents=True, exist_ok=True)
NOW = datetime.now(timezone.utc)
H = {
    "date_utc": NOW.isoformat(),
    "note": "Rate headers measure a point in time; concurrent calls can change limits.",
    "groq": {"status": "not_probed"},
    "gemini": {"status": "not_probed"},
    "cloudflare": {"status": "not_probed"},
}
RATE_FIELDS = ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
               "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens",
               "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens", "retry-after")


def get_json(req, limit=300000):
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read(limit)
            try:
                result = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                result = {}
            return response.status, response.headers, result
    except HTTPError as e:
        try:
            body = json.loads(e.read(3000))
        except (ValueError, UnicodeDecodeError):
            body = {}
        return e.code, e.headers, body


def groq():
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return {"status": "missing_key"}
    headers = {"Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "User-Agent": "ZenEditorialReadOnlyQuotaProbe/1.0"}
    catalog_req = Request("https://api.groq.com/openai/v1/models", headers=headers)
    try:
        status, h, _ = get_json(catalog_req)
        found = {k: h[k] for k in RATE_FIELDS if h.get(k)}
        report = {"catalog_http_status": status, "rate_headers": found}
        if found.get("x-ratelimit-remaining-requests"):
            report["status"] = "rate_headers_from_catalog"
            report["token_window"] = "per_minute"
            report["request_window"] = "per_day"
            return report
        # Groq official rate limit docs say to inspect chat completion response headers.
        # A single tiny completion gives account/model RPD + TPM. It does not
        # expose the true remaining tokens-per-day and costs 1 request.
        post = Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps({
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_completion_tokens": 8,
                "temperature": 0,
            }).encode(),
            headers=headers,
            method="POST"
        )
        code, h, body = get_json(post)
        report.update({
            "status": "inference_headers_captured",
            "inference_http_status": code,
            "rate_headers": {k: h[k] for k in RATE_FIELDS if h.get(k)},
            "request_window": "per_day",
            "token_window": "per_minute",
            "note": "One minimal request was used; daily token balance is not exposed in these headers.",
        })
        if code == 429:
            msg = str((body.get("error") or {}).get("message") or "")
            report["429_limit_class"] = "daily" if "per day" in msg or "TPD" in msg else (
                "minute" if "per minute" in msg or "TPM" in msg else "unknown")
        return report
    except (OSError, URLError, TimeoutError, ValueError) as e:
        return {"status": "probe_error", "error_type": type(e).__name__}


def gemini():
    key = next((os.environ.get(n) for n in
                ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_AI_API_KEY",
                 "GOOGLE_API_KEY", "GEMINI_KEY")
                if os.environ.get(n)), None)
    if not key:
        return {"status": "missing_key"}
    req = Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        headers={"x-goog-api-key": key, "User-Agent": "ZenEditorialReadOnlyQuotaProbe/1.0"},
    )
    try:
        code, h, body = get_json(req)
        # Google AI Studio is the authoritative quota source for this API key.
        result = {"status": "model_catalog_accessible" if code == 200 else "catalog_http_error",
                  "http_status": code,
                  "remaining_daily_requests": None,
                  "reason": "Gemini model catalog does not expose project-specific remaining daily quota."}
        quota_fields = {k: v for k, v in h.items()
                        if "ratelimit" in k.lower() or "quota" in k.lower()}
        if quota_fields:
            result["rate_headers"] = quota_fields
        return result
    except (OSError, URLError, TimeoutError, ValueError) as e:
        return {"status": "probe_error", "error_type": type(e).__name__}


def cloudflare():
    acc, key = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_ANALYTICS_API_TOKEN")
    if not acc or not key:
        return {"status": "read_only_analytics_token_not_configured"}
    start = NOW.strftime("%Y-%m-%dT00:00:00Z")
    query = (
        'query { viewer { accounts(filter: {accountTag: "' + acc + '"}) { '
        'aiInferenceAdaptiveGroups(limit: 100, '
        'filter: {datetime_geq: "' + start + '"}) '
        '{ sum {totalNeurons} } } } }'
    )
    req = Request(
        "https://api.cloudflare.com/client/v4/graphql",
        data=json.dumps({"query": query}).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "ZenEditorialReadOnlyQuotaProbe/1.0"
        },
    )
    try:
        code, _, body = get_json(req)
        report = {"http_status": code, "free_daily_neurons": 10000,
                  "source": "Cloudflare GraphQL aiInferenceAdaptiveGroups.sum.totalNeurons",
                  "since_utc": start,
                  "status": "analytics_unavailable"}
        if code != 200 or body.get("errors"):
            errors = body.get("errors", [])
            report["error_codes"] = [x.get("extensions", {}).get("code", "unknown") for x in errors][:3]
            report["explanation"] = ("Current Workers AI API token may lack "
                                     "Account Analytics: Read permission.")
            return report
        accounts = (body.get("data") or {}).get("viewer", {}).get("accounts") or []
        groups = accounts[0].get("aiInferenceAdaptiveGroups", []) if accounts else []
        used = sum(float((row.get("sum") or {}).get("totalNeurons") or 0) for row in groups)
        report.update({
            "status": "analytics_observed",
            "used_neurons_today_utc": round(used, 2),
            "estimated_remaining_neurons": max(0, round(10000 - used, 2)),
            "analytics_rows": len(groups),
            "caveat": "Analytics may lag and is not a guaranteed real-time enforced balance.",
        })
        return report
    except (OSError, URLError, TimeoutError, ValueError) as e:
        return {"status": "probe_error", "error_type": type(e).__name__}



def gcp_gemini_metrics():
    """Optional Google Cloud read-only monitoring, not a promise of AI Studio balance.

    Google free tier may block metric access or omit data. Never silently equate
    the sum of UTC-day deltas to a per-model RPD quota or daily remaining balance.
    """
    token = os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN", "")
    project = os.environ.get("GOOGLE_QUOTA_MONITOR_PROJECT", "")
    if not token or not project:
        return {"status": "not_configured",
                "note": "Optional GitHub OIDC + GCP Monitoring Viewer; see docs."}
    start = NOW.strftime("%Y-%m-%dT00:00:00Z")
    end = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {"status": "not_available", "daily_remaining": None,
              "period_utc": [start, end],
              "source": "Google Cloud Monitoring (not Google AI Studio)"}
    types = {
        "free_tier_requests_usage":
            "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/usage",
        "free_tier_requests_limit":
            "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/limit",
    }
    for field, metric in types.items():
        q = urlencode({"filter": 'metric.type="' + metric + '"',
                       "interval.startTime": start, "interval.endTime": end,
                       "pageSize": 1000})
        url = "https://monitoring.googleapis.com/v3/projects/" + quote(project, safe="") + "/timeSeries?" + q
        req = Request(url, headers={"Authorization": "Bearer " + token,
                                     "User-Agent": "ZenEditorialQuotaReadOnly/1.0"})
        try:
            code, _, body = get_json(req)
            if code != 200:
                result[field] = {"status": "http_error", "code": code}
                continue
            series = body.get("timeSeries", []) if isinstance(body, dict) else []
            values = {}
            for item in series:
                model = (item.get("metric") or {}).get("labels", {}).get("model", "unknown")
                points = item.get("points", [])
                if field.endswith("_usage"):
                    count = sum(int((p.get("value") or {}).get("int64Value") or 0) for p in points)
                    values[model] = values.get(model, 0) + count
                elif points:
                    values[model] = max(values.get(model, 0),
                                        int((points[0].get("value") or {}).get("int64Value") or 0))
            result[field] = {"status": "observed" if series else "no_data",
                             "per_model": values,
                             "note": "Model and quota limit_name scopes can differ; not an account-wide balance."}
            result["status"] = "partial_or_observed"
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            result[field] = {"status": "read_error", "error_type": type(exc).__name__}
    return result


def private_store(path, content):
    """Write only sanitized JSON to the existing private owner-review repo."""
    token = os.environ.get("EDITORIAL_PRIVATE_TOKEN")
    if not token:
        return {"status": "missing_private_write_token"}
    url = "https://api.github.com/repos/kaokshi/zen-editorial-review/contents/" + quote(path, safe="/")
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "Content-Type": "application/json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "ZenEditorialQuotaReadOnly/1.0"}
    import base64
    for attempt in range(3):
        sha = None
        try:
            code, _, meta = get_json(Request(url, headers=headers))
            if code == 200:
                sha = meta.get("sha")
            elif code != 404:
                return {"status": "private_get_failed", "http_status": code}
        except (OSError, TimeoutError, ValueError) as exc:
            return {"status": "private_get_failed", "error_type": type(exc).__name__}
        payload = {"message": "Private quota monitoring snapshot",
                   "content": base64.b64encode(content).decode("ascii"),
                   "branch": "main"}
        if sha:
            payload["sha"] = sha
        try:
            with urlopen(Request(url, data=json.dumps(payload).encode(), headers=headers,
                                 method="PUT"), timeout=45) as resp:
                if resp.status in (200, 201):
                    return {"status": "saved_private"}
        except HTTPError as exc:
            if exc.code in (409, 422) and attempt < 2:
                continue
            return {"status": "private_write_failed", "http_status": exc.code}
        except (OSError, TimeoutError, ValueError) as exc:
            return {"status": "private_write_failed", "error_type": type(exc).__name__}
    return {"status": "conflict_after_retries"}


def main():
    H["groq"] = groq()
    H["gemini"] = gemini()
    H["gemini"]["google_cloud_monitoring"] = gcp_gemini_metrics()
    H["cloudflare"] = cloudflare()
    body = json.dumps(H, indent=2, ensure_ascii=False).encode("utf-8")
    (OUT / "quota-snapshot.json").write_bytes(body)
    # GitHub private repository is the only persistent report destination.
    H["private_archive"] = private_store("quota-monitor/history/" + NOW.strftime("%Y-%m-%dT%H%M%SZ") + ".json", body)
    H["private_latest"] = private_store("quota-monitor/latest.json", body)
    print(json.dumps({"date_utc": H["date_utc"],
                      "groq": H["groq"],
                      "gemini": H["gemini"],
                      "cloudflare": H["cloudflare"],
                      "private_archive": H["private_archive"],
                      "private_latest": H["private_latest"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
