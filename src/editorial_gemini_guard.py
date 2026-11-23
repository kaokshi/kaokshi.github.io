"""Fail-closed, per-process Gemini free-tier editorial admission guard.

Google applies limits per project, not per key. This local guard is NOT an official
balance and cannot see calls from other clients. Production needs an owner-verified
dedicated free-tier project, current AI Studio limits, and a valid model preflight.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

MODEL = "gemini-3.5-flash-lite"
PACIFIC = ZoneInfo("America/Los_Angeles")
_minute_lock = Lock()
_minute_events = deque()


def quota_day(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(PACIFIC).date().isoformat()


def safe_status(config, *, free_only=False, model_available=False, now=None):
    """No network calls, no inferred free-tier numbers."""
    guard = config.get("gemini_quota_guard") or {}
    model = guard.get("model")
    if not guard.get("automated_generation_enabled"):
        return "gemini_editor_not_activated"
    if model != MODEL:
        return "gemini_model_not_allowlisted"
    if not guard.get("exclusive_free_project_confirmed") or not free_only:
        return "gemini_free_project_not_confirmed"
    if not model_available:
        return "gemini_model_availability_unverified"
    stamp = guard.get("limits_confirmed_until")
    try:
        valid_until = datetime.fromisoformat(stamp).date()
    except (TypeError, ValueError, AttributeError):
        return "gemini_limits_not_verified"
    if (now or datetime.now(timezone.utc)).date() > valid_until:
        return "gemini_limits_verification_expired"
    checks = ("verified_free_daily_requests", "verified_free_rpm", "verified_free_tpm")
    if any(type(guard.get(k)) is not int or guard[k] <= 0 for k in checks):
        return "gemini_limits_not_verified"
    if (guard["verified_free_daily_requests"] <
            guard.get("max_calls_per_cycle", 4) + guard.get("reserve_daily_requests", 2)):
        return "gemini_verified_rpd_insufficient"
    if guard["verified_free_rpm"] < 2:
        return "gemini_verified_rpm_insufficient"
    return "ready"


def ensure_minute_budget(guard, prompt, output_tokens, *, clock=monotonic):
    """Record a conservative input+output token envelope before a request.

    UTF-8 bytes provide a deliberately oversized input estimate for most text.
    Concurrent calls within this Python process share the same reservation list.
    """
    now = clock()
    estimate = len(prompt.encode("utf-8")) + output_tokens + 500
    rpm = guard["verified_free_rpm"]
    tpm = guard["verified_free_tpm"]
    with _minute_lock:
        while _minute_events and now - _minute_events[0][0] >= 60:
            _minute_events.popleft()
        if len(_minute_events) >= rpm:
            return "gemini_local_rpm_headroom_exhausted"
        if sum(item[1] for item in _minute_events) + estimate > tpm:
            return "gemini_local_tpm_headroom_exhausted"
        _minute_events.append((now, estimate))
    return "ready"


def preflight(config, report, *, free_only=False, now=None):
    guard = config.get("gemini_quota_guard", {})
    model_visible = (report.get("status") == "models_accessible" and
                     report.get("configured_model") == guard.get("model") and
                     report.get("configured_model_available") is True)
    return safe_status(config, free_only=free_only, model_available=model_visible, now=now)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", type=Path, required=True)
    parser.add_argument("--model-report", type=Path, required=True)
    args = parser.parse_args()
    config_path = Path(__file__).resolve().parents[1] / "config/editorial_production.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        report = json.loads(args.model_report.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        report = {}
    state = preflight(
        config, report, free_only=os.environ.get("EDITORIAL_GEMINI_FREE_ONLY") == "true"
    )
    # Always write a definite denial first; no secrets or private usage counters.
    with args.github_env.open("a", encoding="utf-8") as output:
        output.write("EDITORIAL_GEMINI_EDITOR_ALLOWED=" + str(state == "ready").lower() + "\n")
    print("Gemini editor free-quota preflight:", state)
    return 0  # Gemini optional; a blocked editor is never falsely marked completed.


if __name__ == "__main__":
    raise SystemExit(main())
