"""Fail-closed eligibility check for a zero-cost OpenRouter-only editorial run.

This module never calls a text model. It checks the authenticated model catalog
through the existing free-pricing guard before a production fallback is enabled.
"""
import argparse
import os
from pathlib import Path

from .editorial_providers import (Blocked, authorization, parse_json, request,
                                  require_free_router)


def verify_fallback(check= require_free_router):
    """Return a safe status string; do not expose API credentials or response bodies."""
    try:
        check()
    except Blocked:
        return "free_route_unavailable"
    except (OSError, ValueError, TypeError):
        return "free_route_unavailable"
    return "free_route_confirmed"


def free_remaining(fetch=request):
    """Read authenticated remaining free calls; no paid model is contacted."""
    reply = parse_json(fetch("https://openrouter.ai/api/v1/key",
                             ("openrouter.ai",),
                             headers=authorization("OPENROUTER_API_KEY")))
    data = reply.get("data") if isinstance(reply, dict) else None
    row = data.get("free_model_daily_requests") if isinstance(data, dict) else None
    if not isinstance(row, dict):
        raise Blocked("openrouter_free_account_quota_visibility_unavailable")
    remaining = row.get("remaining")
    if type(remaining) is not int or remaining < 0:
        limit, used = row.get("limit"), row.get("used")
        if type(limit) is not int or type(used) is not int or limit < used:
            raise Blocked("openrouter_free_account_quota_visibility_unavailable")
        remaining = limit - used
    return remaining


def require_free_envelope(minimum, *, reader=free_remaining):
    if type(minimum) is not int or not 0 < minimum <= 50:
        raise Blocked("invalid_openrouter_free_envelope")
    available = reader()
    if available < minimum:
        raise Blocked("openrouter_free_daily_calls_insufficient")
    return available


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", type=Path, required=True)
    parser.add_argument("--minimum-free-remaining", type=int, default=0)
    parser.add_argument("--quota-only", action="store_true",
                        help="Check actual free request headroom without rerouting Groq")
    args = parser.parse_args()
    if verify_fallback() != "free_route_confirmed":
        print("OpenRouter free-only preflight unavailable; production remains blocked.")
        return 2
    if args.minimum_free_remaining:
        try:
            available = require_free_envelope(args.minimum_free_remaining)
        except Blocked as exc:
            print("OpenRouter three-channel test blocked: " + str(exc))
            return 2
        print(f"OpenRouter free request envelope verified: {available} remaining; "
              "no paid model calls authorized.")
    if args.quota_only:
        return 0
    # GITHUB_ENV is a trusted GitHub-created file, not a user path in public execution.
    with args.github_env.open("a", encoding="utf-8") as stream:
        stream.write("EDITORIAL_GROQ_FALLBACK_ONLY=true\n")
    print("Verified zero-cost OpenRouter backup; no Groq model calls authorized in this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
