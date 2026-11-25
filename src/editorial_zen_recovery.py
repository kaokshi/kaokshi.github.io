"""Conservative recovery decisions for the existing durable executor. No paid transport."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from .editorial_zen_core import CoreBlocked

AUTO_RETRY = {"cloud_ledger_busy", "cloud_storage_unavailable", "control_unavailable"}
MAX_RETRIES = 2
COOLDOWN_MS = 15 * 60 * 1000

# Only explicit, known temporary failures are eligible. Unknown errors, rights,
# quality gates, and ambiguous billing never become automatic retries.
TRANSIENT = {"cloud_ledger_busy", "cloud_storage_unavailable", "control_unavailable",
             "daily_budget_exhausted", "rolling_budget_exhausted", "routerai_balance_insufficient",
             "human_stage_approval_required", "control_blocked", "control_mode_revision_changed"}

def recovery_status(core, article):
    plan, stage = core.article_work(article)
    if stage is None:
        return {"status": "complete"}
    budget = core.budget(core.day())
    if budget["unsettledRequest"] or budget["overrun"] or budget["rollingOverrun"]:
        return {"status": "blocked", "reason": "financial_reconciliation_required", "stage": stage}
    rows = core.check_stage(article, stage)
    result = json.loads(rows.get(stage, "{}"))
    slots = [r[0] for r in core.db.execute("SELECT slot FROM images WHERE article=? ORDER BY slot", (article,))]
    missing = [i for i in range(7) if i not in slots]
    base = {"stage": stage, "modeRevision": plan["modeRevision"], "acceptedImageSlots": slots, "missingImageSlots": missing}
    if result.get("status") not in ("failed", "blocked"):
        return {**base, "status": "ready", "reason": None}
    reason = result.get("reason")
    if reason not in TRANSIENT:
        return {**base, "status": "blocked", "reason": reason or "unknown_failure_requires_review"}
    return {**base, "status": "recheck_required", "reason": reason}

def confirmed_retry(core, article, expected_stage, expected_revision, expected_reason):
    """Explicit retry only after the caller verified the blocker is resolved.
    Preserve all immutable results and spending; never reset operation IDs.
    """
    state = recovery_status(core, article)
    if (state.get("status") != "recheck_required" or state.get("stage") != expected_stage
            or state.get("modeRevision") != expected_revision or state.get("reason") != expected_reason):
        raise CoreBlocked("recovery_state_changed")
    # Do not erase diagnostic evidence or turn a failed stage into a passed stage.
    # A separate dispatch permit must be provided by the future execution host.
    return state

def retry_window(core, article, stage, reason, now):
    # No automatic recovery for budget, balance, quality, rights, or unknown charges.
    if reason not in AUTO_RETRY:
        return False
    row = core.db.execute("SELECT count,last_at FROM recovery_attempts WHERE article=? AND stage=?", (article, stage)).fetchone()
    if row is not None:
        return row["count"] < MAX_RETRIES and now - row["last_at"] >= COOLDOWN_MS
    previous = core.db.execute("SELECT result FROM stages WHERE article=? AND stage=?", (article, stage)).fetchone()
    blocked_at = json.loads(previous[0]).get("blockedAt") if previous else None
    return blocked_at is None or now - blocked_at >= COOLDOWN_MS

# Reasons that stop the lane but have neither an automatic nor a claimable path.
# They require an explicit owner release after the blocker is verifiably gone.
OWNER_RELEASE = TRANSIENT - AUTO_RETRY

def release_blocked_stage(core, article, stage, reason, *, confirm=False):
    """Explicit owner release of a transient blocker that nothing else can clear.

    No provider call, no charge and no pass: the stage becomes unattempted again
    while every checkpoint, charge and accepted artifact is preserved.
    """
    if confirm is not True:
        raise CoreBlocked("explicit_release_confirmation_required")
    if reason not in OWNER_RELEASE:
        raise CoreBlocked("recovery_reason_not_releasable")
    state = recovery_status(core, article)
    if (state.get("status") != "recheck_required" or state.get("stage") != stage
            or state.get("reason") != reason):
        raise CoreBlocked("recovery_state_changed")
    advisories = _budget_advisories(core, article, stage, reason)
    released = core.clear_blocked_stage(article, stage, reason)
    return {**released, "advisories": advisories}

def _budget_advisories(core, article, stage, reason):
    """Local evidence the blocker may still be present. Reported, never guessed away.

    A release stays permitted for every OWNER_RELEASE reason: budget, balance and
    control state can only be proven by the provider or the current day, and the
    executor rechecks balance, budget and control before any reservation or call.
    These codes tell the operator what to verify first.
    """
    found = []
    row = core.db.execute("SELECT result FROM stages WHERE article=? AND stage=?", (article, stage)).fetchone()
    blocked_at = json.loads(row[0]).get("blockedAt") if row else None
    zone = ZoneInfo(core.policy["timezone"])
    if reason == "daily_budget_exhausted":
        if type(blocked_at) is not int:
            found.append("release_evidence_missing")
        elif datetime.fromtimestamp(blocked_at / 1000, timezone.utc).astimezone(zone).date().isoformat() >= core.day():
            found.append("daily_budget_not_reset")
    elif reason == "rolling_budget_exhausted":
        if Decimal(core.budget(core.day())["rollingRemainingRub"]) <= 0:
            found.append("rolling_budget_not_available")
    return found

def claim_retry(core, article, stage, revision, reason, now):
    confirmed_retry(core, article, stage, revision, reason)
    with core.db:
        core.db.execute("BEGIN IMMEDIATE")
        if not retry_window(core, article, stage, reason, now):
            raise CoreBlocked("recovery_retry_not_due")
        core.db.execute("INSERT INTO recovery_attempts VALUES (?,?,1,?) ON CONFLICT(article,stage) DO UPDATE SET count=count+1,last_at=excluded.last_at", (article, stage, now))
