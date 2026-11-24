"""One-shot, low-cost live RouterAI diagnostic.

Never publishes content or edits the production agent. Requires a secret API key,
checks balance before and after every single small call, stops on unexpected costs.
A balance-delta guard is best-effort, not a server-enforced spending ceiling.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://routerai.ru/api/v1"
TASKS = (
    ("brain", "qwen/qwen3.6-plus",
     "Ты управляющий агент редакции. Верни ОДИН JSON с полями topic, channel, reason. "
     "Тема: почему в старом лифте иногда мигает свет. Канал: научпоп. Не выдумывай факты."),
    ("author", "anthropic/claude-sonnet-5.5",
     "Напиши 2 коротких живых абзаца вымышленной мистической истории, 70-100 слов. "
     "В подъезде слышны шаги, но никого нет. Без имён реальных людей, без ссылок."),
    ("review", "deepseek/deepseek-v4-pro-0813",
     "Найди ровно два логических противоречия и дай два исправления в тексте: "
     "'Дом был пуст с 2010 года. Вчера там поселился дворник. Никто не жил в доме после 2010 года. "
     "Часы остановились в 12:00 и пробили тринадцать раз в 12:05.' Ответ кратко."),
)


class UnsafeToTest(RuntimeError):
    pass


def call(path, token, payload=None, *, limit=350000, timeout=None, exact_numbers=False):
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
        with urlopen(request, timeout=timeout or (75 if payload else 20)) as response:
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


def smoke():
    token = os.environ.get("ROUTERAI_API_KEY", "")
    initial = credits(token)
    if initial < Decimal("30"):
        raise UnsafeToTest("requires_30_rub_balance_to_keep_reserve")
    rows = []
    previous = initial
    cap = Decimal("10")
    for role, model, instruction in TASKS:
        if initial - previous > cap - Decimal("3"):
            raise UnsafeToTest("estimated_session_budget_stop")
        response = call("/chat/completions", token, {
            "model": model,
            "messages": [
                {"role": "system", "content": "Выполняй только редакционное задание; никаких инструментов и внешних действий."},
                {"role": "user", "content": instruction},
            ],
            "max_tokens": 250,
            "temperature": 0.3,
        })
        try:
            choice = response["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str):
                raise TypeError
        except (TypeError, IndexError, KeyError, AttributeError):
            raise UnsafeToTest("model_invalid_visible_response") from None
        latest = credits(token)
        spent = previous - latest
        if spent < Decimal("0"):
            raise UnsafeToTest("balance_increased_or_delayed_review_required")
        usage = response.get("usage") or {}
        rows.append({
            "role": role, "model_requested": model,
            "model_reported": str(response.get("model", "unreported"))[:100],
            "finish_reason": str(choice.get("finish_reason", "unknown"))[:30],
            "input_tokens": usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else None,
            "output_tokens": usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else None,
            "cost_from_usage_rub": usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None,
            "balance_delta_rub": str(spent),
            "sample": text[:750],
        })
        if spent > Decimal("3") or initial - latest > cap:
            raise UnsafeToTest("one_request_cost_exceeded_expected_envelope")
        previous = latest
    return {
        "status": "completed", "checked_utc": datetime.now(timezone.utc).isoformat(),
        "initial_balance_rub": str(initial), "final_balance_rub": str(previous),
        "spent_rub": str(initial - previous), "models": rows,
        "full_articles_generated": False, "publication_authorized": False,
        "note": "Three deliberately small synthetic model calls; no author-quality verdict for 3000+ char articles.",
    }


def main():
    destination = Path(os.environ.get("ROUTERAI_TEST_RESULT", "/tmp/routerai-smoke.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = smoke()
        rc = 0
    except UnsafeToTest as exc:
        result = {"status": "blocked_or_failed", "reason": str(exc),
                  "publication_authorized": False}
        rc = 2
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## RouterAI one-time diagnostic\n\nStatus: **" + result["status"] +
                      "**\n\n")
            if result["status"] == "completed":
                out.write("Session spent: **" + result["spent_rub"] + " RUB**\n\n")
                for row in result["models"]:
                    out.write("### " + row["role"] + " — " + row["model_requested"] +
                              "\nCost (balance delta): " + row["balance_delta_rub"] +
                              " RUB\n\n" + row["sample"].replace("<", "&lt;")[:750] + "\n\n")
            else:
                out.write("Reason: " + result["reason"] + "\n")
    print("RouterAI one-time smoke:", result["status"])
    if result["status"] == "completed":
        print("Total RUB spent:", result["spent_rub"])
        for item in result["models"]:
            print(item["role"], item["model_requested"], item["balance_delta_rub"])
    else:
        print("Diagnostic:", result["reason"])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
