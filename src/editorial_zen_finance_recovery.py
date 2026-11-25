"""Read-only account audit and explicit recovery of already settled production holds.

No provider calls. No chat reset. No reconstruction of missing invoices.
"""
import argparse
import json
import os
import re
import time
from decimal import Decimal

from .editorial_zen_cloud import CloudCore, GitHubStore, STATE
from .editorial_zen_control import configured_account
from .editorial_zen_core import CoreBlocked, amount


def audit(core):
    rows = core.db.execute("SELECT id,day,main,reserve,actual FROM spend ORDER BY rowid").fetchall()
    settled = sum((amount(r["actual"]) for r in rows if r["actual"] is not None), Decimal(0))
    pending = [{"operation": r["id"], "day": r["day"],
                "reservedRub": str(amount(r["main"]) + amount(r["reserve"]))}
               for r in rows if r["actual"] is None]
    return {"entries": len(rows), "settledRub": str(settled), "pending": pending,
            "budget": core.budget(core.day())}


def inspect(cloud, account, now=None):
    now = int(time.time() * 1000) if now is None else now
    state = account("status")
    if (not isinstance(state, dict) or state.get("schema") != "zen-core/account-status-v1"
            or state.get("executionEnabled") is not False
            or type(state.get("observedAt")) is not int
            or not now - 30000 <= state["observedAt"] <= now + 5000
            or "production" not in state or "chat" not in state):
        raise CoreBlocked("account_status_invalid")
    # Never mistake a missing cloud checkpoint for an empty, reconciled ledger.
    if cloud.store.read(STATE)[0] is None:
        raise CoreBlocked("cloud_ledger_missing")
    hold = state["production"]
    if hold is not None and (not isinstance(hold, dict)
            or not isinstance(hold.get("id"), str) or not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}", hold["id"])
            or not isinstance(hold.get("operation"), str)
            or not re.fullmatch(r"[-a-zA-Z0-9:_/]{1,180}", hold["operation"])
            or not hold["operation"].startswith(hold["id"] + "/")):
        raise CoreBlocked("account_hold_invalid")

    def read(core):
        report = audit(core)
        if hold is None:
            report["productionRecovery"] = "no_hold"
        else:
            row = core.db.execute("SELECT article,actual FROM spend WHERE id=?",
                                  (hold["operation"],)).fetchone()
            if row is None:
                report["productionRecovery"] = "missing_reservation_requires_evidence"
            elif row["article"].split("/", 1)[0] != hold["id"]:
                raise CoreBlocked("ledger_operation_mismatch")
            elif row["actual"] is None:
                report["productionRecovery"] = "unsettled_requires_evidence"
            else:
                report["productionRecovery"] = "settled_hold_can_close"
                report["confirmedRub"] = str(amount(row["actual"]))
        return report
    report = cloud.access(read)
    return {**report, "productionHold": hold, "chatHold": state["chat"],
            "executionEnabled": False, "publicationAuthorized": False}


def recover(cloud, account, *, confirm=False, expected_operation=None, now=None):
    if confirm is not True or not isinstance(expected_operation, str):
        raise CoreBlocked("explicit_recovery_confirmation_required")
    report = inspect(cloud, account, now=now)
    hold = report["productionHold"]
    if (not hold or hold["operation"] != expected_operation
            or report["productionRecovery"] != "settled_hold_can_close"):
        raise CoreBlocked("recovery_requires_settled_matching_operation")
    # Server checks the exact operation atomically, even if another hold appeared.
    account("close", hold["id"], hold["operation"], "settled")
    return {"closedOperation": expected_operation, "confirmedRub": report["confirmedRub"],
            "executed": False, "publicationAuthorized": False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("audit", "close-settled"))
    p.add_argument("--operation")
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args()
    try:
        cloud = CloudCore(GitHubStore(os.getenv("EDITORIAL_PRIVATE_TOKEN")))
        account = configured_account()
        result = (inspect(cloud, account) if args.command == "audit" else
                  recover(cloud, account, confirm=args.confirm, expected_operation=args.operation))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except CoreBlocked as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "publicationAuthorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
