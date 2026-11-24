"""Conservative recovery decisions for the existing durable executor. No paid transport."""
import json
from .editorial_zen_core import CoreBlocked, amount

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
