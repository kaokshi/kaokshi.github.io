"""Minimal safe diagnostics for private status transfer to the connected ChatGPT GitHub app.

This module deliberately never exposes draft text, source URLs, image files or
private keys; it provides only whitelisted error codes and lifecycle states.
"""
import json
from pathlib import Path
import re

ARTICLE_IDS = ("science-01", "legends-01", "cinema-01")
STATES = {"missing", "running", "blocked", "partial_review_pending",
          "rights_review_pending", "review_pending", "package_failed"}
CODES = re.compile(r"^[a-z][a-z0-9_]{2,85}$")
STAGES = re.compile(r"^[a-z][a-z0-9_-]{2,85}$")
SAFE_PREFIXES = ("film_", "movie_", "cinema_", "qa_", "editorial_", "image_",
                 "layout_", "groq_", "openrouter_", "provider_", "http_",
                 "call_", "production_", "revision_", "source_", "rights_",
                 "independent_", "document_", "topic_", "unique_", "invalid_",
                 "model_", "storage_", "fact_", "substantive_", "previous_",
                 "language_", "grammar_", "connection_", "truncated_",
                 "empty_", "request_", "response_", "film")


def code(value):
    if not isinstance(value, str):
        return "unclassified_block"
    stripped = value.strip()
    if CODES.fullmatch(stripped) and stripped.startswith(SAFE_PREFIXES):
        return stripped
    if stripped.startswith("Film source API unavailable:"):
        return "film_source_api_unavailable"
    if stripped.startswith("independent_edit_unavailable: "):
        return "independent_edit_unavailable"
    if stripped.startswith("document_export_failed: "):
        return "document_export_failed"
    return "unclassified_block"


def public_report(data, day):
    if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("invalid_report_day")
    articles = data.get("articles", {})
    tasks = data.get("tasks", {})
    entries = {}
    for ident in ARTICLE_IDS:
        entry = articles.get(ident, {})
        state = entry.get("status", "missing")
        rows = [task for task in tasks.values() if task.get("article") == ident
                and task.get("status") in {"blocked", "failed", "retry_pending"}]
        entries[ident] = {
            "status": state if state in STATES else "blocked",
            "blockers": sorted(set(code(b) for b in entry.get("blockers", [])))[:20],
            "failed_tasks": sorted([{"stage": task.get("operation") if isinstance(task.get("operation"), str) and STAGES.fullmatch(
                task["operation"]) else "unknown",
                "code": code(task.get("error"))} for task in rows],
                key=lambda row: (row["stage"], row["code"]))[:35],
        }
    calls = {}
    for row in data.get("calls", []):
        provider = row.get("provider")
        if provider in {"groq", "gemini", "openrouter", "klein", "kilo", "kilo-review", "ovh", "ovh-gptoss"}:
            calls[provider] = calls.get(provider, 0) + 1
    return {"schema": "zen-editorial-chat-status-v1", "day": day,
            "articles": entries, "provider_calls": calls,
            "ready_count": sum(x["status"] in {"review_pending", "rights_review_pending"}
                               for x in entries.values()),
            "publication_authorized": False, "contents_included": False}


def from_state(root, day):
    path = Path(root) / day / "state.json"
    if not path.is_file():
        raise ValueError("private_state_not_available")
    return public_report(json.loads(path.read_text(encoding="utf-8")), day)
