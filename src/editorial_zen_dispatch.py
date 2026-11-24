"""Select one durable next stage. Never infer permission from a receipt or a free-text instruction."""
import json
import time
from .editorial_zen_recovery import recovery_status, retry_window, claim_retry
from .editorial_zen_core import CoreBlocked, STAGES

def select_stage(core, article, *, now=None):
    now = int(time.time() * 1000) if now is None else now
    plan, stage = core.article_work(article)
    if stage is None:
        return {"status": "complete"}
    budget = core.budget(core.day())
    if budget["unsettledRequest"] or budget["overrun"] or budget["rollingOverrun"]:
        return {"status": "blocked", "reason": "financial_reconciliation_required"}
    if not core.policy.get("execution_enabled") is True:
        return {"status": "blocked", "reason": "execution_disabled"}
    recovery = recovery_status(core, article)
    retry_reason = None
    if recovery["status"] != "ready":
        if recovery["status"] == "recheck_required" and retry_window(core, article, stage, recovery["reason"], now):
            retry_reason = recovery["reason"]
        else:
            return {"status": "blocked", "reason": "recovery_review_required", "recovery": recovery}
    mode, revision = plan["mode"], plan["modeRevision"]
    needs_command = mode == "manual" or mode == "semi_autonomous" and stage in ("images", "package")
    command_id = f"stage:{revision}:{article}:{stage}"
    if needs_command:
        row = core.db.execute("SELECT payload FROM stage_intents WHERE id=? AND job=?",
                              (command_id, article.split("/", 1)[0])).fetchone()
        if not row:
            return {"status": "blocked", "reason": "owner_stage_intent_required"}
        command = json.loads(row[0])
        if command["mode"] != mode or command["modeRevision"] != revision:
            raise CoreBlocked("stale_stage_intent")
    return {"status": "ready", "article": article, "stage": stage, "mode": mode,
            "retryReason": retry_reason,
            "modeRevision": revision, "approvalRequired": needs_command and mode == "semi_autonomous"}

def dispatch_one(cloud, article, token, *, guard, execute):
    # Select again inside the CAS operation before any persisted approval.
    now = int(time.time() * 1000)
    plan = cloud.access(lambda c: select_stage(c, article, now=now))
    if plan["status"] != "ready":
        return plan
    job = article.split("/", 1)[0]
    if guard(job, plan["mode"]) != plan["modeRevision"]:
        raise CoreBlocked("control_mode_revision_changed")
    def confirm(c):
        if select_stage(c, article, now=now) != plan:
            raise CoreBlocked("dispatch_stage_changed")
        if plan["retryReason"]:
            claim_retry(c, article, plan["stage"], plan["modeRevision"], plan["retryReason"], now)
        if plan["approvalRequired"]:
            c.approve_stage(article, plan["stage"])
    cloud.access(confirm, change=bool(plan["approvalRequired"] or plan["retryReason"]))
    # Execution has its own per-request budget, account and control guards.
    if guard(job, plan["mode"]) != plan["modeRevision"]:
        raise CoreBlocked("control_mode_revision_changed")
    def pinned_guard(job_id, mode):
        if job_id != job or mode != plan["mode"] or guard(job_id, mode) != plan["modeRevision"]:
            raise CoreBlocked("control_mode_revision_changed")
        return plan["modeRevision"]
    execute(cloud, article, token, target=plan["stage"], guard=pinned_guard)
    return {"status": "attempt_finished", "stage": plan["stage"]}
