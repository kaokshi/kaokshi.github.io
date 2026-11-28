"""Offline native Claudy contract for the editorial executor.

No credentials, network, dispatch, or ledger settlement. Native token usage
and monthly counter changes cannot be treated as a RouterAI usage.cost receipt.
"""
from decimal import Decimal, ROUND_CEILING
import json
import re
from types import MappingProxyType

RATES = MappingProxyType({
    "anthropic/claude-sonnet-5.5": ("claude-sonnet-5-5", 30, 150),
    "anthropic/claude-opus-5.5": ("claude-opus-5-5", 45, 225),
})
ROUTER_TEXT = frozenset({"qwen/qwen3.6-plus", "deepseek/deepseek-v4-pro-0813"})
ROUTER_IMAGE = "bytedance-seed/seedream-5-0-flash"

def target_provider(model):
    if model in RATES:
        return "claudy"
    if model in ROUTER_TEXT or model == ROUTER_IMAGE:
        return "routerai"
    raise ValueError("provider_route_unconfigured")

def native_request(model, messages, reasoning, max_tokens):
    if model not in RATES or reasoning not in ("off", "low", "medium", "high"):
        raise ValueError("claudy_configuration_invalid")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 32000:
        raise ValueError("claudy_output_cap_invalid")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("claudy_messages_invalid")
    if any(not isinstance(m, dict) or m.get("role") != role or not isinstance(m.get("content"), str)
           or not m["content"].strip() for m, role in zip(messages, ("system", "user"))):
        # Multimodal content is explicitly unsupported in this preview.
        raise ValueError("claudy_text_only_preview")
    result = {"model": RATES[model][0], "system": messages[0]["content"],
              "messages": [{"role": "user", "content": messages[1]["content"]}],
              "max_tokens": max_tokens,
              "thinking": {"type": "disabled" if reasoning == "off" else "adaptive"}}
    if reasoning != "off":
        result["output_config"] = {"effort": reasoning}
    return result

def request_bound(model, payload):
    native, inp, out = RATES[model]
    maximum = payload.get("max_tokens")
    if payload.get("model") != native or type(maximum) is not int or not 1 <= maximum <= 32000:
        raise ValueError("claudy_configuration_invalid")
    count = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) + 1024
    upper = (Decimal(count * inp * 2 + maximum * out) / 1000000).quantize(Decimal(".01"), rounding=ROUND_CEILING)
    return {"input_bound": count, "output_bound": maximum, "estimated_upper_rub": str(upper),
            "estimate_only": True, "dispatch_authorized": False}

def response_evidence(response, request_id=None):
    def identifier(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value) else None
    row = response if isinstance(response, dict) else {}
    u = row.get("usage")
    u = u if isinstance(u, dict) else {}
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    usage = {k: u.get(k) if type(u.get(k)) is int and u[k] >= 0 else None for k in fields}
    return {"provider": "claudy", "request_id": identifier(request_id), "message_id": identifier(row.get("id")),
            "usage": usage, "cost_verified": False, "settlement_source": None}

def inspect_response(model, payload, response, request_id=None):
    evidence = response_evidence(response, request_id)
    def rejected(reason):
        return {"accepted": False, "reason": reason, "evidence": evidence, "settlement_verified": False}
    bound = request_bound(model, payload)
    if not isinstance(response, dict) or response.get("model") != RATES[model][0]:
        return rejected("claudy_model_mismatch")
    u = evidence["usage"]
    raw = response.get("usage")
    if u["input_tokens"] is None or u["output_tokens"] is None:
        return rejected("claudy_usage_invalid")
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if key in raw and u[key] is None:
            return rejected("claudy_usage_invalid")
    read, write = u["cache_read_input_tokens"] or 0, u["cache_creation_input_tokens"] or 0
    if u["input_tokens"] + read + write > bound["input_bound"] or u["output_tokens"] > bound["output_bound"]:
        return rejected("claudy_usage_over_bound")
    content = response.get("content")
    if not isinstance(content, list) or any(not isinstance(x, dict) for x in content):
        return rejected("claudy_content_invalid")
    parts = [x.get("text") for x in content if x.get("type") == "text"]
    if any(not isinstance(p, str) for p in parts):
        return rejected("claudy_content_invalid")
    text = "".join(parts).strip()
    if not text or response.get("stop_reason") != "end_turn":
        return rejected("claudy_incomplete_response")
    _, inp, out = RATES[model]
    estimate = (Decimal(inp) * (u["input_tokens"] + Decimal(".1") * read + 2 * write)
                + out * u["output_tokens"]) / 1000000
    return {"accepted": True, "text": text, "estimated_rub": str(estimate), "evidence": evidence,
            "settlement_verified": False, "dispatch_authorized": False}

def preview_editorial_routes(models):
    roles = ("plan", "draft", "edit", "visual_plan", "images")
    if not isinstance(models, dict) or set(models) != set(roles):
        raise ValueError("invalid_editorial_roles")
    routes = {}
    for role in roles:
        model = models[role]
        provider = target_provider(model)
        if (role == "images") != (model == ROUTER_IMAGE):
            raise ValueError("wrong_model_capability")
        routes[role] = {"model": model, "target_provider": provider,
                        "dispatch_authorized": False,
                        "reason": "provider_settlement_unavailable" if provider == "claudy" else "preview_only"}
    return routes
