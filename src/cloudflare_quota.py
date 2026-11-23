"""Read-only Cloudflare Workers AI daily usage probe.

Uses GraphQL analytics; it is a diagnostic estimate and NOT billing authority.
No inference calls, writes, or token/account identifiers in output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
from urllib import error, request

API_BASE = "https://api.cloudflare.com/client/v4"
FREE_DAILY_NEURONS = 10_000
GRAPHQL_QUERY = """
query ($account: string!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      aiInferenceAdaptiveGroups(
        limit: 10
        filter: {datetime_geq: $start, datetime_lt: $end}
      ) {
        count
        sum { totalNeurons }
      }
    }
  }
}
"""


class QuotaCheckError(Exception):
    """A sanitized error, safe to show in GitHub Actions logs."""


def api_json(url: str, token: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with request.urlopen(req, timeout=25) as response:
            raw = response.read(1024 * 1024)
    except error.HTTPError as exc:
        raise QuotaCheckError("Cloudflare API HTTP " + str(exc.code)) from None
    except (error.URLError, TimeoutError):
        raise QuotaCheckError("Cloudflare API network error") from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise QuotaCheckError("Cloudflare API returned invalid JSON") from None
    if not isinstance(result, dict):
        raise QuotaCheckError("Cloudflare API returned unexpected data")
    return result


def extract_neurons(data: dict) -> tuple[float, int]:
    """Return aggregated neurons and requests, validating the response shape."""
    if data.get("errors"):
        raise QuotaCheckError("Cloudflare GraphQL query rejected (permissions or schema)")
    try:
        accounts = data["data"]["viewer"]["accounts"]
        if len(accounts) != 1:
            raise QuotaCheckError("Cloudflare account is not accessible with read-only token")
        rows = accounts[0]["aiInferenceAdaptiveGroups"]
        if not isinstance(rows, list):
            raise ValueError()
        neurons = sum(float(row["sum"]["totalNeurons"]) for row in rows)
        calls = sum(int(row["count"]) for row in rows)
        if not math.isfinite(neurons) or neurons < 0 or calls < 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        raise QuotaCheckError("Cloudflare GraphQL analytics response is incomplete") from None
    return neurons, calls


def check_quota(token: str, account_id: str, now: dt.datetime | None = None) -> dict:
    if not token:
        raise QuotaCheckError("Missing GitHub secret CLOUDFLARE_QUOTA_TOKEN")
    if not account_id:
        raise QuotaCheckError("Missing GitHub secret CLOUDFLARE_ACCOUNT_ID")
    verified = api_json(API_BASE + "/user/tokens/verify", token)
    if not verified.get("success") or verified.get("result", {}).get("status") != "active":
        raise QuotaCheckError("Cloudflare read-only token is not active")
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)
    beginning = now.replace(hour=0, minute=0, second=0, microsecond=0)
    def stamp(value: dt.datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
    analytics = api_json(
        API_BASE + "/graphql",
        token,
        {"query": GRAPHQL_QUERY, "variables": {
            "account": account_id, "start": stamp(beginning), "end": stamp(now)
        }},
    )
    used, requests = extract_neurons(analytics)
    remaining = max(0.0, FREE_DAILY_NEURONS - used)
    return {
        "status": "completed",
        "source": "Cloudflare GraphQL AI inference adaptive analytics",
        "billing_authoritative": False,
        "period_utc": beginning.date().isoformat(),
        "checked_at_utc": stamp(now),
        "free_daily_neurons": FREE_DAILY_NEURONS,
        "estimated_used_neurons": round(used, 2),
        "estimated_free_remaining_neurons": round(remaining, 2),
        "observed_inferences": requests,
        "note": "Analytics may be sampled or delayed; this is not the billing balance.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = check_quota(
            os.environ.get("CLOUDFLARE_QUOTA_TOKEN", ""),
            os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
        )
        print("Workers AI daily estimate (UTC):")
        print("Date:", result["period_utc"])
        print("Used neurons:", result["estimated_used_neurons"])
        print("Estimated free remaining:", result["estimated_free_remaining_neurons"])
        print("Inference count:", result["observed_inferences"])
        print("Caution: analytics is not an exact billable quota.")
        exit_code = 0
    except QuotaCheckError as exc:
        result = {"status": "unavailable", "reason": str(exc)}
        print("Cloudflare quota status: unavailable (" + str(exc) + ")")
        exit_code = 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        if exit_code == 0:
            msg = (
                "## Cloudflare Workers AI quota estimate\n"
                + f"UTC day: {result['period_utc']}\n\n"
                + f"Used: **{result['estimated_used_neurons']:,.2f}** / {FREE_DAILY_NEURONS:,} free neurons\n\n"
                + f"Estimated remaining: **{result['estimated_free_remaining_neurons']:,.2f}** neurons\n\n"
                + "Cloudflare GraphQL analytics may lag or use sampling; not a billing figure.\n"
            )
        else:
            msg = "## Cloudflare quota unavailable\n" + result["reason"] + "\n"
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(msg)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
