"""Groq's shared windows and the editorial agency's own token budget."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
import re

WINDOWS = {"rpd": 1000, "tpd": 200000, "rpm": 30, "tpm": 8000}
HEADER_NUMBERS = {f"x-ratelimit-{kind}-{unit}" for kind in ("limit", "remaining")
                  for unit in ("requests", "tokens")}
HEADER_TIMES = {"x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "retry-after"}
DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")
SECOND = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
SECONDS_LIMIT = 7 * 86400


def instant(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def seconds(value, at):
    if not isinstance(value, str) or len(value) > 100:
        return None
    value = value.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        result = float(value)
    elif parts := list(DURATION.finditer(value)):
        if "".join(p[0] for p in parts) != value:
            return None
        result = sum(float(p[1]) * SECOND[p[2]] for p in parts)
    else:
        try:
            result = (parsedate_to_datetime(value) - at).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return result if 0 <= result <= SECONDS_LIMIT else None


def rate_headers(headers, at):
    snapshot = {}
    for name in HEADER_NUMBERS | HEADER_TIMES:
        value = headers.get(name, "")
        if name in HEADER_NUMBERS:
            if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value):
                snapshot[name] = int(value)
        elif (delay := seconds(value, at)) is not None:
            snapshot[name] = (at + timedelta(seconds=delay)).isoformat()
    return snapshot


def usage_fields(usage):
    usage = usage if isinstance(usage, dict) else {}
    def read(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    inp, out, total = map(read, ("prompt_tokens", "completion_tokens", "total_tokens"))
    if total is None and inp is not None and out is not None:
        total = inp + out
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def operation(stage):
    return re.sub(r"-\d+$", "", stage)


def estimate_tokens(prompt, output, rows, stage):
    # Conservative UTF-8 fallback, with source-language calibration when enough text size is known.
    size = len(prompt.encode("utf-8")) + 310
    ratios = sorted(row["input_tokens"] / row["prompt_bytes"]
                    for row in rows if row.get("operation") == operation(stage)
                    and isinstance(row.get("input_tokens"), int) and row.get("prompt_bytes", 0) >= 100)
    ratio = max(.34, ratios[math.floor(.8 * (len(ratios) - 1))] * 1.15) if ratios else .34
    return math.ceil(size * ratio), output, size


def day_rows(rows, now):
    return [r for r in rows if (at := instant(r.get("sent_at") or r.get("at")))
            and at.astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date()]


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


def organization_limits(rows, policy):
    limits = {**WINDOWS, **policy.get("organization_limits", {})}
    for row in sorted(rows, key=lambda r: r.get("completed_at") or r.get("at", "")):
        info = row.get("rate_limits", {})
        for metric, key in (("rpd", "x-ratelimit-limit-requests"), ("tpm", "x-ratelimit-limit-tokens")):
            if type(info.get(key)) is int and info[key] > 0:
                limits[metric] = info[key]
        for metric, value in row.get("reported_limits", {}).items():
            if metric in {*WINDOWS, "itpm", "otpm"} and type(value) is int and value > 0:
                limits[metric] = value
    return limits


def assessment(rows, policy, lane, now, requested, *, reserve=False):
    """Returns a terminal reason, a wait and a projected charge for a single request."""
    today = day_rows(rows, now)
    lengths = {"input_tokens": requested[0], "output_tokens": requested[1],
               "total_tokens": sum(requested[:2])}
    limits = organization_limits(rows, policy)
    info = snapshot(today, now)["rate_limits"]
    for row in rows:
        reset = instant(row.get("resets_at"))
        if row.get("limit_code") in {"rpd", "tpd", "daily"} and reset and reset > now:
            return "groq_daily_quota_reset_pending", 0
    spent = sum(charge(row, "total_tokens") for row in today)
    lane_spent = sum(charge(row, "total_tokens") for row in today if row.get("lane", "production") == lane)
    cap = (policy["experimental_daily_tokens"] if lane == "experiment" else
           policy["daily_tokens"] - (0 if reserve else policy["repair_reserve_tokens"]))
    repair_spent = sum(charge(row, "total_tokens") for row in today
                       if row.get("lane", "production") == lane and row.get("repair"))
    if lane == "production" and lane_spent + lengths["total_tokens"] > policy["daily_tokens"]:
        return "groq_editorial_budget_exhausted", 0
    if lane == "production" and not reserve:
        lane_spent -= repair_spent
    if lane_spent + lengths["total_tokens"] > cap:
        return "groq_editorial_budget_exhausted", 0
    if (spent + lengths["total_tokens"] > limits["tpd"]
            or sum(row.get("estimated_requests", 1) for row in today) >= limits["rpd"]):
        return "groq_daily_budget_exhausted", 0
    if lane == "experiment":
        remaining_reserve = max(0, policy["repair_reserve_tokens"] - sum(
            charge(row, "total_tokens") for row in today if row.get("lane") == "production" and row.get("repair")))
        if spent + lengths["total_tokens"] + remaining_reserve > limits["tpd"]:
            return "groq_editorial_reserve_protected", 0
    remaining_rpd = info.get("x-ratelimit-remaining-requests")
    reset_rpd = instant(info.get("x-ratelimit-reset-requests"))
    if remaining_rpd == 0 and (not reset_rpd or reset_rpd > now):
        return "groq_daily_budget_exhausted", 0
    minute = [(at, row) for row in rows if row.get("status") != "run_reservation"
              and (at := instant(row.get("completed_at") or row.get("sent_at") or row.get("at")))
              and at + timedelta(seconds=60) > now]
    wait = 0
    metric_limits = {"tpm": "total_tokens", "itpm": "input_tokens", "otpm": "output_tokens"}
    for metric, key in metric_limits.items():
        if metric not in limits:
            continue
        if lengths[key] > limits[metric]:
            return "groq_request_exceeds_window", 0
        used = sum(charge(row, key) for _, row in minute)
        for at, row in sorted(minute, key=lambda pair: pair[0]):
            if used + lengths[key] <= limits[metric]:
                break
            wait = max(wait, (at + timedelta(seconds=60) - now).total_seconds() + .1)
            used -= charge(row, key)
    if len(minute) >= limits["rpm"]:
        at = sorted(x for x, _ in minute)[len(minute) - limits["rpm"]]
        wait = max(wait, (at + timedelta(seconds=60) - now).total_seconds() + .1)
    remaining = info.get("x-ratelimit-remaining-tokens")
    reset = instant(info.get("x-ratelimit-reset-tokens"))
    if remaining is not None and lengths["total_tokens"] > remaining and reset and reset > now:
        wait = max(wait, (reset - now).total_seconds() + .1)
    for row in rows:
        if (row.get("limit_code") in {"rpm", "tpm", "itpm", "otpm", "minute"}
                and (restore := instant(row.get("resets_at"))) and restore > now):
            wait = max(wait, (restore - now).total_seconds() + .1)
    return None, wait


def forecast(rows, policy, lane, now, seconds_left, articles, pending):
    def cost(candidates, field, fallback):
        observed = sorted(value for row in candidates
                          if (value := row.get(field, usage_fields(row.get("usage"))[field])) is not None)
        return (observed[math.floor(.8 * (len(observed) - 1))], True) if observed else (fallback, False)

    costs = {}
    for name, count in pending.items():
        candidates = [row for row in rows if row.get("operation") == name and row.get("status") == "responded"]
        expected, observed = cost(candidates, "total_tokens", policy["forecast_tokens"][name])
        inp, _ = cost(candidates, "input_tokens", math.ceil(expected * .5))
        out, _ = cost(candidates, "output_tokens", math.ceil(expected * .5))
        costs[name] = {"pending": count, "tokens_each": expected, "estimated_input_each": inp,
                       "estimated_output_each": out, "basis": "observed" if observed else "estimate"}
    base = sum(row["tokens_each"] * row["pending"] for name, row in costs.items() if name != "repair")
    extra = costs.get("repair", {}).get("tokens_each", 0) * pending.get("repair", 0)
    limits = organization_limits(rows, policy)
    quotas = [(base + extra) / limits["tpm"], sum(pending.values()) / limits["rpm"]]
    for key, metric in (("estimated_input_each", "itpm"), ("estimated_output_each", "otpm")):
        if metric in limits:
            quotas.append(sum(row[key] * row["pending"] for row in costs.values()) / limits[metric])
    minutes = math.ceil(max(quotas))
    account = snapshot(rows, now)
    today = [row for row in day_rows(rows, now) if row.get("lane", "production") == lane]
    lane_used = sum(charge(row, "total_tokens") for row in today)
    regular = sum(charge(row, "total_tokens") for row in today if not row.get("repair"))
    cap = policy["experimental_daily_tokens"] if lane == "experiment" else policy["daily_tokens"]
    base_cap = cap if lane == "experiment" else cap - policy["repair_reserve_tokens"]
    other_pipeline = max(articles, 1) * policy["forecast_non_groq_seconds"] if articles else 0
    available_requests = min(max(0, limits["rpd"] - account["requests"]),
                             account["rate_limits"].get("x-ratelimit-remaining-requests", limits["rpd"]))
    return {"lane": lane, "operations": costs, "estimated_base_tokens": base,
            "estimated_repair_tokens": extra, "remaining_editorial_tokens": max(0, cap - lane_used),
            "fits_editorial_budget": lane_used + base + extra <= cap and regular + base <= base_cap,
            "fits_observed_organization_headroom": (account["accounted_tokens"] + base + extra <= limits["tpd"]
                                                    and sum(pending.values()) <= available_requests),
            "effective_limits": limits, "minimum_groq_minutes_at_capacity": minutes,
            "deadline_estimate_fits": minutes * 60 + other_pipeline <= seconds_left, "groq_usage": account}
