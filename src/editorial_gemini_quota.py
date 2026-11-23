"""Safe read-only Gemini Developer API access check.

The models endpoint does not return account/project-level quota usage.
Never confuse model availability or locally observed calls with real remaining quota.
No generation requests, no prices, no billing change.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


class GeminiQuotaError(Exception):
    pass


def inspect_models(reply: dict, configured_model: str = "") -> dict:
    if not isinstance(reply, dict) or not isinstance(reply.get("models"), list):
        raise GeminiQuotaError("gemini_models_response_invalid")
    models = reply["models"]
    names = {
        item.get("name", "").removeprefix("models/")
        for item in models
        if isinstance(item, dict) and "generateContent" in item.get("supportedGenerationMethods", [])
        and isinstance(item.get("name"), str)
    }
    if not names:
        raise GeminiQuotaError("gemini_no_generation_models_visible")
    if configured_model and configured_model not in names:
        return {"status": "model_not_available", "generation_models_visible": len(names),
                "configured_model": configured_model, "configured_model_available": False,
                "remaining_requests": None, "live_quota_available": False,
                "reason": "model_not_accessible_for_key"}
    return {"status": "models_accessible", "generation_models_visible": len(names),
            "configured_model": configured_model or None,
            "configured_model_available": bool(configured_model),
            "remaining_requests": None, "live_quota_available": False,
            "reason": "Gemini models:list has no request usage, RPD, RPM or TPM balances"}


def read_gemini(token: str, model: str = "", fetch=urlopen) -> dict:
    if not token:
        raise GeminiQuotaError("missing_gemini_api_key")
    req = Request(MODELS_ENDPOINT, headers={"x-goog-api-key": token, "Accept": "application/json"})
    try:
        with fetch(req, timeout=25) as response:
            raw = response.read(1024 * 1024)
    except HTTPError as exc:
        raise GeminiQuotaError("gemini_http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise GeminiQuotaError("gemini_api_unreachable") from None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise GeminiQuotaError("gemini_invalid_json") from None
    report = inspect_models(payload, model)
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["usage_source"] = "Gemini Developer API models:list (not a quota endpoint)"
    report["automated_generation_enabled"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=os.environ.get("GEMINI_QUOTA_MODEL", ""))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = read_gemini(os.environ.get("GEMINI_API_KEY", ""), args.model)
        rc = 0 if report["status"] == "models_accessible" else 2
    except GeminiQuotaError as exc:
        report = {"status": "unavailable", "reason": str(exc),
                  "remaining_requests": None, "live_quota_available": False,
                  "automated_generation_enabled": False}
        rc = 2
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Gemini diagnostics:", report["status"])
    print("Visible generation models:", report.get("generation_models_visible", "unavailable"))
    print("Actual RPM/TPM/RPD remaining: unknown (Google AI Studio required)")
    if report["status"] != "models_accessible":
        print("Reason:", report.get("reason", "unknown"))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Gemini API read-only diagnostic\n"
                         + "Status: **" + report["status"] + "**\n\n"
                         + "Actual remaining RPM/TPM/RPD: **unknown**. "
                         + "View active limits and usage in Google AI Studio.\n\n"
                         + "Gemini generation is disabled in this editorial route.\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
