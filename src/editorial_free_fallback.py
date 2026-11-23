"""Fail-closed eligibility check for a zero-cost OpenRouter-only editorial run.

This module never calls a text model. It checks the authenticated model catalog
through the existing free-pricing guard before a production fallback is enabled.
"""
import argparse
import os
from pathlib import Path

from .editorial_providers import Blocked, require_free_router


def verify_fallback(check= require_free_router):
    """Return a safe status string; do not expose API credentials or response bodies."""
    try:
        check()
    except Blocked:
        return "free_route_unavailable"
    except (OSError, ValueError, TypeError):
        return "free_route_unavailable"
    return "free_route_confirmed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args()
    if verify_fallback() != "free_route_confirmed":
        print("OpenRouter free-only preflight unavailable; production remains blocked.")
        return 2
    # GITHUB_ENV is a trusted GitHub-created file, not a user path in public execution.
    with args.github_env.open("a", encoding="utf-8") as stream:
        stream.write("EDITORIAL_GROQ_FALLBACK_ONLY=true\n")
    print("Verified zero-cost OpenRouter backup; no Groq model calls authorized in this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
