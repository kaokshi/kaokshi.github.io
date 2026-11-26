"""Private local SQLite storage of Studio reports; no summing overlapping windows."""
import sqlite3
import json
from datetime import datetime, timezone
from .zen_dzen_xlsx import import_report

def import_to_db(db, raw, channel, expected_channel_id):
    if not expected_channel_id:
        raise ValueError("expected_channel_binding_required")
    report = import_report(raw, channel, expected_channel_id)
    db.execute("""CREATE TABLE IF NOT EXISTS studio_snapshots (
        id TEXT PRIMARY KEY, channel TEXT NOT NULL, channel_id TEXT NOT NULL,
        start TEXT NOT NULL, end TEXT NOT NULL, imported_at TEXT NOT NULL,
        payload TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS studio_imports (
        file_sha TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES studio_snapshots(id))""")
    with db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT DISTINCT channel_id FROM studio_snapshots WHERE channel=?", (channel,)).fetchall()
        if any(row[0] != expected_channel_id for row in previous):
            raise ValueError("stored_channel_binding_mismatch")
        other = db.execute("SELECT 1 FROM studio_snapshots WHERE channel_id=? AND channel<>?", (expected_channel_id,channel)).fetchone()
        if other:
            raise ValueError("channel_id_reused")
        payload = dict(report)
        file_sha = payload.pop("file_sha256")
        key = payload["snapshot_id"]
        period = payload["all_platforms"]
        cur = db.execute("INSERT OR IGNORE INTO studio_snapshots VALUES (?,?,?,?,?,?,?)",
            (key,channel,expected_channel_id,period["start"],period["end"],
             datetime.now(timezone.utc).isoformat(),json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        created = cur.rowcount == 1
        stored = db.execute("SELECT payload FROM studio_snapshots WHERE id=?", (key,)).fetchone()
        if json.loads(stored[0]) != payload:
            raise ValueError("snapshot_collision")
        db.execute("INSERT OR IGNORE INTO studio_imports VALUES (?,?)",(file_sha,key))
        if db.execute("SELECT snapshot_id FROM studio_imports WHERE file_sha=?",(file_sha,)).fetchone()[0] != key:
            raise ValueError("file_binding_conflict")
    return {"id":key,"created":created,"channel":channel,"period":[period["start"],period["end"]]}

def history(db, channel):
    return [json.loads(r[0]) for r in db.execute(
        "SELECT payload FROM studio_snapshots WHERE channel=? ORDER BY imported_at,id",(channel,))]
