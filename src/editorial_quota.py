"""Cross-run Groq quota gate. Only the explicit bounded probe calls a model."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .editorial_memory import read_json, write
from .editorial_providers import Blocked, ProviderError, text_call
from .editorial_schedule import CONFIG


def quota_status(config, day, markers):
    known = config.get("groq_quota_last_known_block", {})
    values = set(markers)
    if day <= known.get("utc_day", "") or "blocked" in values:
        return "blocked"
    if "ready" in values:
        return "ready"
    return "probe_attempted" if "attempted" in values else "unverified"


def recorded_markers(day):
    from .editorial_delivery import artifact_rows

    for state in ("blocked", "ready", "attempted"):
        if next(artifact_rows(f"editorial-groq-{state}-", refs={"main", "codex/editorial-production"},
                              day=day), None):
            yield state


def current_status(day):
    config = read_json(CONFIG)
    if quota_status(config, day, ()) == "blocked":
        return "blocked"
    return quota_status(config, day, recorded_markers(day))


def probe():
    # Clean input, one short request, same account/model as production, no fallback.
    result = text_call("groq", "Reply with the single word OK.", max_tokens=128)
    if not result["text"].strip():
        raise ProviderError("empty_visible_content")


def publish_marker(folder, kind):
    if kind not in {"blocked", "attempted", "ready"}:
        raise ValueError("invalid_marker")
    write(folder / (kind + ".txt"), (kind + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "require-ready", "probe", "record"))
    parser.add_argument("--markers", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        if args.command == "record":
            if args.state:
                for path in args.state.glob("????-??-??/state.json"):
                    state = read_json(path)
                    if (state.get("provider_stops", {}).get("groq") == "http_429_groq_daily_quota"
                            and any(row.get("error") == "http_429_groq_daily_quota" and
                                    row.get("at", "").startswith(day) for row in state.get("calls", []))):
                        publish_marker(args.markers, "blocked")
            return 0
        if args.command == "probe":
            if os.environ.get("EDITORIAL_QUOTA_PROBE_RESERVED") != "true" or not args.markers:
                raise Blocked("groq_probe_not_reserved")
            try:
                probe()
            except ProviderError as exc:
                if str(exc) == "http_429_groq_daily_quota":
                    publish_marker(args.markers, "blocked")
                raise
            publish_marker(args.markers, "ready")
            print(json.dumps({"groq_quota": "ready", "utc_day": day}))
            return 0
        state = current_status(day)
        print(json.dumps({"groq_quota": state, "utc_day": day}))
        if args.command == "require-ready" and state != "ready":
            return 2
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"state={state}\nday={day}\n")
    except (Blocked, OSError, ValueError) as exc:
        print(json.dumps({"groq_quota": "unavailable", "reason": str(exc) if isinstance(exc, Blocked) else type(exc).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
