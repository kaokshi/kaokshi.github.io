"""Read and validate historical Groq accounting, without inference or new reservations."""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .editorial_groq_budget import HEADER_NUMBERS, HEADER_TIMES, instant
from .editorial_memory import read_json, write_json
from .editorial_http import Blocked

HISTORICAL_MODEL = "openai/gpt-oss-120b"

NUMBERS = {"estimated_input_tokens", "estimated_output_tokens", "estimated_total_tokens",
           "input_tokens", "output_tokens", "total_tokens", "http_status", "estimated_requests"}
DATES = {"at", "sent_at", "completed_at", "resets_at"}
CODES = {"tpm", "tpd", "rpm", "rpd", "itpm", "otpm", "minute", "daily", "rate_limit_type_unknown"}
OPERATIONS = {"topics", "draft", "repair", "editorial-audit", "editorial-audit-retry", "image-plan", "quota-probe"}


def clean(row):
    if (not re.fullmatch(r"[a-f0-9]{32}", row.get("request_id", ""))
            or row.get("status") not in {"responded", "failed", "reserved", "run_reservation"}
            or row.get("lane") not in {"production", "experiment"}
            or not instant(row.get("sent_at") or row.get("at"))):
        raise Blocked("invalid_groq_ledger_row")
    out = {"request_id": row["request_id"], "status": row["status"], "lane": row["lane"],
           "model": HISTORICAL_MODEL, "repair": row.get("repair") is True,
           "operation": row["operation"] if row.get("operation") in OPERATIONS else "other"}
    for name in NUMBERS:
        value = row.get(name)
        if type(value) is int and 0 <= value <= 10**8:
            out[name] = value
    out.update({name: instant(row[name]).isoformat() for name in DATES if instant(row.get(name))})
    out["limit_code"] = row.get("limit_code") if row.get("limit_code") in CODES else None
    out["reported_limits"] = {key: value for key, value in (row.get("reported_limits") or {}).items()
                              if key in {"rpd", "tpd", "rpm", "tpm", "itpm", "otpm"}
                              and type(value) is int and 0 < value <= 10**8}
    limits = row.get("rate_limits") or {}
    out["rate_limits"] = {name: value for name, value in limits.items()
                          if (name in HEADER_NUMBERS and type(value) is int and 0 <= value <= 10**8)
                          or (name in HEADER_TIMES and instant(value))}
    return out


def collection(records):
    """Settled runs replace their pessimistic allocations, even when they used zero calls."""
    final = {entry["run"] for entry in records if entry["kind"] == "usage"}
    result = {}
    for entry in records:
        if entry["kind"] == "reservation" and entry["run"] in final:
            continue
        for row in entry["rows"]:
            item = clean(row)
            prior = result.get(item["request_id"])
            if prior is None or (not prior.get("completed_at") and item.get("completed_at")):
                result[item["request_id"]] = item
    return list(result.values())


def archive_document(kind, run, rows):
    if not re.fullmatch(r"[0-9]+\.[0-9]+", run or ""):
        raise Blocked("invalid_groq_ledger_run_id")
    return {"kind": kind, "run": run, "rows": [clean(row) for row in rows]}


def collect(day):
    from .editorial_delivery import artifact_rows, download_artifact

    end = datetime.fromisoformat(day).date()
    records = []
    with TemporaryDirectory(prefix="groq-usage-") as work:
        for kind in ("reservation", "usage"):
            prefix = f"editorial-groq-ledger-{kind}-"
            for row in artifact_rows(prefix, refs={"main", "codex/editorial-production"}, run_attempt_suffix=True):
                date = datetime.fromisoformat(row["name"].removeprefix(prefix)[:10]).date()
                if not end - timedelta(days=14) <= date <= end:
                    continue
                run = str(row["workflow_run"]["id"])
                folder = Path(work) / str(row["id"])
                download_artifact(row, folder)
                path = folder / (kind + ".json")
                if not path.is_file() or path.stat().st_size > 1024 * 1024:
                    raise Blocked("invalid_groq_ledger_archive")
                data = read_json(path)
                if data["kind"] != kind or data["run"].split(".")[0] != run:
                    raise Blocked("groq_ledger_provenance_mismatch")
                if data["run"] != f'{os.environ.get("GITHUB_RUN_ID")}.{os.environ.get("GITHUB_RUN_ATTEMPT")}':
                    records.append(data)
    return collection(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect",))
    parser.add_argument("--day", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.file, collect(args.day))


if __name__ == "__main__":
    main()
