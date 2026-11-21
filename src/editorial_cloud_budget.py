"""Reserve a real request before transmission, including ambiguous failures."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3

from editorial_writer import private_path


class BudgetUnavailable(ValueError):
    """No request was sent. The caller can resume after correcting the budget."""


def reserve(env):
    today = datetime.now(timezone.utc).date().isoformat()
    if env.get("ZEN_CF_BUDGET_DATE") != today:
        raise BudgetUnavailable("Free request budget missing or expired (UTC)")
    if env.get("GITHUB_ACTIONS") == "true":
        if not env.get("GITHUB_RUN_ID") or env.get("ZEN_CF_APPROVED_RUN") != env.get("GITHUB_RUN_ID") or env.get("GITHUB_RUN_ATTEMPT") != "1":
            raise BudgetUnavailable("This Actions run has no single-use request allowance")
    limit = env.get("ZEN_CF_MAX_REQUESTS", "")
    ledger = env.get("ZEN_CF_LEDGER", "")
    if not limit.isascii() or not limit.isdigit() or not 1 <= int(limit) <= 32 or not Path(ledger).is_absolute():
        raise BudgetUnavailable("Set a private absolute ledger and a budget of 1 to 32 requests")
    path = private_path(Path(ledger))
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS usage(account TEXT, day TEXT, n INTEGER, PRIMARY KEY(account,day))")
        key = (env["CLOUDFLARE_ACCOUNT_ID"].lower(), today)
        row = db.execute("SELECT n FROM usage WHERE account=? AND day=?", key).fetchone()
        if row and row[0] >= int(limit):
            raise BudgetUnavailable("Cloudflare request budget exhausted")
        db.execute("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(account,day) DO UPDATE SET n=n+1", key)


def bounded_post(env, limit):
    import requests

    def send(url, **kwargs):
        reserve(env)
        with requests.post(url, stream=True, allow_redirects=False, **kwargs) as response:
            data = bytearray()
            if response.status_code == 200:
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("Cloudflare response too large")
            response._content = bytes(data)
            response._content_consumed = True
            return response

    return send
