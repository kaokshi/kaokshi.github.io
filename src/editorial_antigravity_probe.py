"""Isolated Antigravity API experiment; never part of production editorial routing.

Default: no inference. The one-shot live mode additionally requires explicit
external confirmation that this Google project is on an unbilled Free tier.
The agent has no tools, no mounted environment, no publication access.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://generativelanguage.googleapis.com/v1beta"
AGENT = "antigravity-preview-09-2026"
BASE_MODEL = "gemini-3.6-flash"
MAX_TOTAL_TOKENS = 2400

SYNTHETIC_TEXT = (
    "В выдуманном городе Маяковске открыли подземный музей. "
    "Организаторы утверждают, что музей одновременно работает круглосуточно "
    "и закрывается каждый день в 18:00. "
    "В зале установили 12 ламп, но в следующем абзаце сказано, "
    "что там всего 8 ламп. Никаких реальных источников не приводится."
)
PROMPT = (
    "Это ИЗОЛИРОВАННЫЙ тест независимого редактора русскоязычного канала. "
    "Не вызывай инструменты, не выполняй код и не обращайся в интернет. "
    "Не переписывай целиком. Верни короткий русский текст: "
    "1) найденные логические противоречия, 2) неподтверждённые факты, "
    "3) какие конкретные исправления предложить автору. "
    "Сюжет полностью вымышленный.\n\n" + SYNTHETIC_TEXT
)


class ProbeError(RuntimeError):
    """Safe, credential-free diagnostic."""


def model_catalog(api_key: str, opener=urlopen) -> dict:
    if not api_key:
        raise ProbeError("gemini_key_missing")
    req = Request(
        API + "/models/" + BASE_MODEL,
        headers={"x-goog-api-key": api_key, "Accept": "application/json"},
    )
    try:
        with opener(req, timeout=20) as response:
            raw = response.read(100000)
    except HTTPError as exc:
        raise ProbeError("model_catalog_http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise ProbeError("model_catalog_connection_failed") from None
    try:
        result = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ProbeError("model_catalog_invalid_json") from None
    if not isinstance(result, dict):
        raise ProbeError("model_catalog_invalid_data")
    methods = result.get("supportedGenerationMethods") or []
    return {
        "status": "read_only_check_complete",
        "base_model": BASE_MODEL,
        "model_available": result.get("name") == "models/" + BASE_MODEL,
        "generate_content_supported": isinstance(methods, list) and "generateContent" in methods,
        "agent_api_availability": "not_checked_by_model_catalog",
        "free_project_billing_status": "not_verifiable_with_gemini_api_key",
        "agent_request_sent": False,
    }


def payload() -> dict:
    return {
        "agent": AGENT,
        "input": PROMPT,
        "tools": [],  # no code_execution, web, URL, filesystem, MCP
        "agent_config": {
            "type": "antigravity",
            "model": BASE_MODEL,  # NEVER use the potentially billed default model
            "max_total_tokens": MAX_TOTAL_TOKENS,
        },
    }


def one_test(api_key: str, *, free_project_confirmed: bool,
             opener=urlopen) -> dict:
    if not api_key:
        raise ProbeError("gemini_key_missing")
    if not free_project_confirmed:
        raise ProbeError("unbilled_free_project_not_confirmed_no_inference")
    req = Request(
        API + "/interactions",
        data=json.dumps(payload(), ensure_ascii=False).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Api-Revision": "2026-05-20",
        },
        method="POST",
    )
    try:
        with opener(req, timeout=95) as response:
            raw = response.read(200000)
    except HTTPError as exc:
        raise ProbeError("antigravity_http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise ProbeError("antigravity_network_error") from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProbeError("antigravity_invalid_json") from None
    if not isinstance(result, dict):
        raise ProbeError("antigravity_invalid_response")
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    tokens = usage.get("total_tokens", usage.get("totalTokens"))
    return {
        "status": str(result.get("status", "unknown"))[:30],
        "base_model": BASE_MODEL,
        "agent": AGENT,
        "agent_request_sent": True,
        "requested_max_total_tokens": MAX_TOTAL_TOKENS,
        "reported_total_tokens": tokens if isinstance(tokens, (int, float)) and tokens >= 0 else None,
        "test_output": str(result.get("output_text", result.get("outputText", "")))[:1800],
        "publication_authorized": False,
        "note": "Best-effort agent token cap can slightly overrun; no paid/production route enabled.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["inspect", "one-test"], default="inspect")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("GEMINI_API_KEY", "")
    try:
        if args.mode == "inspect":
            result = model_catalog(key)
        else:
            result = one_test(
                key, free_project_confirmed=(
                    os.environ.get("ANTIGRAVITY_FREE_PROJECT_CONFIRMED") == "true"
                ),
            )
        exit_status = 0
    except ProbeError as exc:
        result = {"status": "blocked_or_unavailable", "reason": str(exc),
                  "agent_request_sent": False,
                  "publication_authorized": False}
        exit_status = 2
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("Antigravity isolated experiment:", json.dumps(result, ensure_ascii=False))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
