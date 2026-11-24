"""Bounded RouterAI transport shared by private evaluation and ZEN CORE."""
from decimal import Decimal, InvalidOperation
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .editorial_http import NoRedirect

BASE = "https://routerai.ru/api/v1"
MODELS = {
    "brain": "qwen/qwen3.6-plus",
    "draft": "anthropic/claude-sonnet-5.5",
    "edit": "deepseek/deepseek-v4-pro-0813",
    "image": "bytedance-seed/seedream-5-0-flash",
}
READ_PATHS = {"/models", "/credits", "/models/" + MODELS["image"] + "/endpoints"}
WRITE_MODELS = {
    "/chat/completions": {MODELS[role] for role in ("brain", "draft", "edit")},
    "/images": {MODELS["image"]},
}


class UnsafeToTest(RuntimeError):
    pass


def call(path, token, payload=None, *, limit=350000, timeout=None, exact_numbers=False):
    if payload is None:
        if path not in READ_PATHS:
            raise UnsafeToTest("routerai_request_not_allowlisted")
    elif path not in WRITE_MODELS or not isinstance(payload, dict):
        raise UnsafeToTest("routerai_request_not_allowlisted")
    elif payload.get("model") not in WRITE_MODELS[path]:
        raise UnsafeToTest("routerai_model_not_allowlisted")
    if not token:
        raise UnsafeToTest("routerai_api_key_missing")
    request = Request(BASE + path,
                      data=(json.dumps(payload, ensure_ascii=False).encode("utf-8")
                            if payload is not None else None),
                      method="POST" if payload is not None else "GET",
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/json",
                               "Content-Type": "application/json"},
                      )
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout or (75 if payload else 20)) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise UnsafeToTest("routerai_response_too_large")
    except HTTPError as exc:
        raise UnsafeToTest("routerai_http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise UnsafeToTest("routerai_connection_failed") from None
    try:
        answer = json.loads(raw, parse_float=Decimal if exact_numbers else float)
    except (UnicodeError, ValueError):
        raise UnsafeToTest("routerai_non_json_response") from None
    if not isinstance(answer, dict):
        raise UnsafeToTest("routerai_unexpected_schema")
    return answer


def credits(token):
    result = call("/credits", token, exact_numbers=True)
    data = result.get("data") or {}
    if not isinstance(data, dict):
        raise UnsafeToTest("balance_schema_not_recognized")
    # Response variants from RouterAI own examples. Never use missing => zero.
    value = data.get("credits", data.get("total_credits"))
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise UnsafeToTest("balance_unavailable") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("10000000"):
        raise UnsafeToTest("balance_invalid")
    return amount
