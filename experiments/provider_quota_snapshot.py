"""Read-only provider quota snapshot for the Zen editorial project.

One cheap Groq chat completion may be used solely to expose its rate-limit headers.
Gemini GET models and Cloudflare GraphQL do not invoke any image/text generation.
No secrets or raw response bodies are ever written to the report/logs.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path("quota-out")
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
    acc, key = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
    if not acc or not key:
        return {"status": "missing_credentials"}
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


def main():
    H["groq"] = groq()
    H["gemini"] = gemini()
    H["cloudflare"] = cloudflare()
    (OUT / "quota-snapshot.json").write_text(json.dumps(H, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(H, ensure_ascii=False))


if __name__ == "__main__":
    main()
