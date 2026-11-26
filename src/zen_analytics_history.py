"""Immutable source-separated analytics snapshots. No network or provider calls."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

CHANNELS = frozenset(("science", "legends", "cinema"))

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def metrika_snapshot(report):
    if report.get("schema") != "zen-metrika/v1" or set(report.get("channels", {})) != CHANNELS:
        raise ValueError("invalid_metrika_report")
    stamp = datetime.fromisoformat(report["collected_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timezone_required")
    rows = []
    for channel, entry in sorted(report["channels"].items()):
        if entry.get("channel") != channel or entry.get("source") != "yandex_metrika":
            raise ValueError("channel_source_mismatch")
        rows.append({"schema": "zen-analytics-snapshot/v1", "channel": channel,
                     "source": "yandex_metrika", "confirmation": "unverified",
                     "usable_for_editorial_advice": False,
                     "collected_at": stamp.astimezone(timezone.utc).isoformat(),
                     "timezone": "Europe/Moscow", "granularity": "report_snapshot",
                     "payload": entry})
    return rows

def append_snapshot(root, snapshot):
    """Local private-storage adapter. Existing immutable records are never overwritten."""
    if snapshot.get("schema") != "zen-analytics-snapshot/v1":
        raise ValueError("invalid_schema")
    channel = snapshot.get("channel")
    if channel not in CHANNELS or snapshot.get("source") != "yandex_metrika":
        raise ValueError("unsupported_source_or_channel")
    if snapshot.get("confirmation") != "unverified" or snapshot.get("usable_for_editorial_advice") is not False:
        raise ValueError("unverified_proxy")
    raw = canonical(snapshot)
    key = hashlib.sha256(raw).hexdigest()
    folder = Path(root) / "yandex_metrika" / channel
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (key + ".json")
    try:
        with target.open("xb") as f:
            f.write(raw)
    except FileExistsError:
        if target.read_bytes() != raw:
            raise ValueError("snapshot_collision")
        return {"id": key, "created": False}
    return {"id": key, "created": True}
