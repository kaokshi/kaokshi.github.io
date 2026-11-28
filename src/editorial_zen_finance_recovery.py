"""Account audit and explicit recovery of production holds with reviewed billing evidence.

No provider calls. No chat reset. No reconstruction of missing invoices.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time
from decimal import Decimal

from .editorial_zen_cloud import CloudCore, GitHubStore, STATE
from .editorial_zen_control import configured_account
from .editorial_zen_core import CoreBlocked, amount
from .editorial_memory import digest


def audit(core):
    rows = core.db.execute("SELECT id,day,main,reserve,actual FROM spend ORDER BY rowid").fetchall()
    settled = sum((amount(r["actual"]) for r in rows if r["actual"] is not None), Decimal(0))
    bounded = {r[0] for r in core.db.execute("SELECT request FROM bounded_uncertain_charges")}
    pending = [{"operation": r["id"], "day": r["day"],
                "reservedRub": str(amount(r["main"]) + amount(r["reserve"])),
                "status": "bounded_uncertain" if r["id"] in bounded else "unsettled"}
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
                bounded = next(item for item in report["pending"] if item["operation"] == hold["operation"])
                report["productionRecovery"] = ("bounded_hold_can_close" if bounded["status"] == "bounded_uncertain"
                                                else "unsettled_requires_evidence")
                report["reservedRub"] = bounded["reservedRub"]
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


def recover_bounded(cloud, account, evidence, *, confirm=False, now=None):
    """Use reviewed evidence of a final provider-side maximum, never a local estimate."""
    if confirm is not True:
        raise CoreBlocked("explicit_recovery_confirmation_required")
    try:
        row = json.loads(evidence)
        operation = row["requestId"]
        maximum = amount(row["maxFinalRub"])
        reference = row["providerEvidence"]
        if (row["schema"] != "zen-core/billing-upper-bound-v1"
                or row["provider"] != "RouterAI" or row["billingFinal"] is not True
                or not isinstance(reference, str) or len(reference.strip()) < 8):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise CoreBlocked("invalid_billing_evidence") from None
    report = inspect(cloud, account, now=now)
    hold = report["productionHold"]
    if (not hold or hold["operation"] != operation or report["productionRecovery"] not in
            {"unsettled_requires_evidence", "bounded_hold_can_close"}):
        raise CoreBlocked("recovery_requires_matching_pending_operation")
    if maximum > amount(report["reservedRub"]):
        raise CoreBlocked("billing_bound_exceeds_reservation")
    sha = digest(evidence)
    cloud.store.immutable("billing-evidence/" + sha + ".json", evidence)
    cloud.access(lambda core: core.retain_bounded_uncertain(operation, maximum, sha), change=True)
    account("close", hold["id"], operation, "bounded_uncertain")
    return {"closedOperation": operation, "status": "bounded_uncertain", "confirmedRub": None,
            "reservedRub": report["reservedRub"], "executed": False, "publicationAuthorized": False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("audit", "close-settled", "close-bounded"))
    p.add_argument("--operation")
    p.add_argument("--evidence", type=Path)
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args()
    try:
        cloud = CloudCore(GitHubStore(os.getenv("EDITORIAL_PRIVATE_TOKEN")))
        account = configured_account()
        if args.command == "close-bounded":
            if not args.confirm or args.evidence is None:
                raise CoreBlocked("explicit_recovery_confirmation_required")
            result = recover_bounded(cloud, account, args.evidence.read_bytes(), confirm=True)
        elif args.command == "close-settled":
            result = recover(cloud, account, confirm=args.confirm, expected_operation=args.operation)
        else:
            result = inspect(cloud, account)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except CoreBlocked as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "publicationAuthorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
