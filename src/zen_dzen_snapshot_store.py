"""Local private snapshot storage; no network, publication or paid calls."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from zen_dzen_xlsx_import import parse_report

def import_snapshot(db_path, xlsx_path, channel):
    report = parse_report(xlsx_path, channel)
    # Hash the logical content, excluding workbook packaging metadata.
    logical = {k:v for k,v in report.items() if k != "file_sha256"}
    logical["articles"] = sorted(logical["articles"], key=lambda a:a["url"])
    payload = json.dumps(logical, ensure_ascii=False, sort_keys=True, separators=(",",":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS dzen_snapshots(
            digest TEXT PRIMARY KEY, channel TEXT NOT NULL, period_start TEXT NOT NULL,
            period_end TEXT NOT NULL, imported_at TEXT NOT NULL, file_sha256 TEXT NOT NULL,
            payload TEXT NOT NULL)""")
        result = db.execute("""INSERT INTO dzen_snapshots VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(digest) DO NOTHING""",
            (digest, channel, report["period_start"], report["period_end"],
             datetime.now(timezone.utc).isoformat(), report["file_sha256"], payload))
        return {"status":"inserted" if result.rowcount else "duplicate",
                "digest":digest, "channel":channel, "period_start":report["period_start"],
                "period_end":report["period_end"]}
