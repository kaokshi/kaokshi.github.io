"""Fail-closed control observation from the owner-authenticated ZEN CORE Worker."""
import json
import os
import re
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .editorial_zen_core import CoreBlocked


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_control(row, job_id, mode, *, now=None, require_execution=True):
    now = int(time.time() * 1000) if now is None else now
    if (not isinstance(row, dict) or row.get("schema") != "zen-core/control-v1"
            or row.get("jobId") != job_id or type(row.get("observedAt")) is not int
            or not now - 30000 <= row["observedAt"] <= now + 5000
            or type(row.get("version")) is not int or row["version"] < 0
            or row.get("publicationAuthorized") is not False or row.get("rssEnabled") is not False):
        raise CoreBlocked("control_observation_invalid")
    if row.get("stopped") is not False or row.get("controlClear") is not True or row.get("reason") is not None:
        raise CoreBlocked("control_blocked")
    if mode not in ("manual", "semi_autonomous", "autonomous") or row.get("mode") != mode or row.get("jobMode") != mode:
        raise CoreBlocked("control_mode_changed")
    if require_execution and row.get("executionEnabled") is not True:
        raise CoreBlocked("control_execution_disabled")
    if not require_execution and row.get("executionEnabled") is not False:
        raise CoreBlocked("control_not_in_test_mode")
    revision = row.get("modeRevision", 0)
    if type(revision) is not int or not 0 <= revision <= row["version"]:
        raise CoreBlocked("invalid_control_mode_revision")
    return revision


def control_guard(endpoint, secret):
    parsed = urlsplit(endpoint or "")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/control"
            or not re.fullmatch(r"[a-fA-F0-9]{64}", secret or "")):
        raise CoreBlocked("control_configuration_missing")
    opener = build_opener(NoRedirect())

    def check(job_id, mode):
        req = Request(endpoint, method="POST", data=json.dumps({"id": job_id}).encode(),
                      headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=8) as response:
                raw = response.read(4097)
                if len(raw) > 4096:
                    raise ValueError
                row = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise CoreBlocked("control_unavailable") from None
        return validate_control(row, job_id, mode)

    return check


def configured_guard():
    return control_guard(os.getenv("ZEN_CORE_CONTROL_ENDPOINT"), os.getenv("ZEN_CORE_INTAKE_SECRET"))


def configured_account():
    endpoint = os.getenv("ZEN_CORE_CONTROL_ENDPOINT", "")
    secret = os.getenv("ZEN_CORE_INTAKE_SECRET", "")
    # Reuse endpoint validation; no redirect or credential fallback.
    control_guard(endpoint, secret)
    target = endpoint.removesuffix("/control") + "/account"
    opener = build_opener(NoRedirect())

    def account(action, job_id, operation, outcome=None):
        data = {"action": action, "id": job_id, "operation": operation}
        if outcome is not None:
            data["outcome"] = outcome
        req = Request(target, method="POST", data=json.dumps(data).encode(),
                      headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=8) as response:
                raw = response.read(1025)
                if len(raw) > 1024:
                    raise ValueError
                row = json.loads(raw)
        except (OSError, ValueError):
            raise CoreBlocked("account_gate_unavailable") from None
        if not isinstance(row, dict) or row.get("claimed" if action == "claim" else "closed") is not True:
            raise CoreBlocked("account_gate_denied")
        return row
    return account
