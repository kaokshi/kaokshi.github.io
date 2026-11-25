"""Historical Groq accounting snapshots; no admission, inference, or reservation."""
from datetime import datetime, timezone

HEADER_NUMBERS = {f"x-ratelimit-{kind}-{unit}" for kind in ("limit", "remaining")
                  for unit in ("requests", "tokens")}
HEADER_TIMES = {"x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "retry-after"}


def instant(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def day_rows(rows, now):
    return [r for r in rows if (at := instant(r.get("sent_at") or r.get("at")))
            and at.astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date()]


def usage_fields(usage):
    usage = usage if isinstance(usage, dict) else {}
    def read(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    inp, out, total = map(read, ("prompt_tokens", "completion_tokens", "total_tokens"))
    if total is None and inp is not None and out is not None:
        total = inp + out
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def charge(row, count):
    value = row.get(count)
    if value is None and count == "total_tokens":
        value = usage_fields(row.get("usage", {}))["total_tokens"]
    if value is not None:
        return value
    return row.get("estimated_" + count, 0)


def snapshot(rows, now):
    today = day_rows(rows, now)
    headers = {}
    for row in sorted(today, key=lambda r: r.get("completed_at") or r.get("sent_at") or r.get("at", "")):
        headers.update(row.get("rate_limits", {}))
    unknown = sum(row.get("total_tokens") is None and
                  usage_fields(row.get("usage", {}))["total_tokens"] is None for row in today)
    return {"requests": sum(row.get("estimated_requests", 1) for row in today), "accounted_tokens": sum(charge(row, "total_tokens") for row in today),
            "unknown_token_requests": unknown,
            "known_tokens": sum(row.get("total_tokens") or usage_fields(row.get("usage", {}))["total_tokens"] or 0
                                for row in today),
            "rate_limits": headers, "organization_total_exact": False}
