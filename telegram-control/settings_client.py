"""Authenticated, read-only model policy handshake; no inference or release."""
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request

from src.editorial_zen_core import CoreBlocked, validate_editorial_settings
from src.editorial_zen_transport import bridge_opener
from src.editorial_routerai import MODELS, WRITE_MODELS


def exchange(endpoint, secret, payload):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/intake"):
        raise CoreBlocked("invalid_intake_endpoint")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret or ""):
        raise CoreBlocked("missing_intake_secret")
    request = Request(endpoint.removesuffix("/intake") + "/executor-settings",
                      data=json.dumps(payload).encode(),
                      headers={"Authorization": "Bearer " + secret,
                               "Content-Type": "application/json"}, method="POST")
    with bridge_opener().open(request, timeout=15) as response:
        raw = response.read(16385)
        if len(raw) > 16384:
            raise CoreBlocked("settings_response_too_large")
        return json.loads(raw)


def synchronize(core, endpoint, secret, *, transport=exchange):
    envelope = transport(endpoint, secret, {"action": "read"})
    if (not isinstance(envelope, dict) or envelope.get("schema") != "zen-core/settings-v1"
            or any(envelope.get(k) is not False for k in
                   ("executionEnabled", "publicationAuthorized", "rssEnabled"))):
        raise CoreBlocked("unsafe_settings_envelope")
    settings = validate_editorial_settings(envelope.get("settings"))
    spec = settings["models"]
    selected = spec["value"]
    if (selected["plan"] != selected["visual_plan"] or selected["images"] != MODELS["image"]
            or any(selected[r] not in WRITE_MODELS["/chat/completions"]
                   for r in ("plan", "draft", "edit"))):
        raise CoreBlocked("unsupported_pinned_model_route")
    # Persist before acknowledging; compare-and-set prevents concurrent revision loss.
    name = "editorial-model-settings.json"
    raw, version = core.store.read(name)
    if raw is not None:
        old = validate_editorial_settings(json.loads(raw))["models"]
        if old["revision"] > spec["revision"] or (old["revision"] == spec["revision"]
                                                   and old["hash"] != spec["hash"]):
            raise CoreBlocked("stale_settings_revision")
    if raw is None or old["revision"] != spec["revision"]:
        core.store.write(name, json.dumps(settings, sort_keys=True).encode(), version)
    report = {"models": {"revision": spec["revision"], "hash": spec["hash"]}}
    ack = transport(endpoint, secret, {"action": "ack", "report": report})
    received = (ack.get("acks") or {}).get("models", {}) if isinstance(ack, dict) else {}
    if (not isinstance(ack, dict) or ack.get("ok") is not True
            or received.get("revision") != spec["revision"] or received.get("hash") != spec["hash"]):
        raise CoreBlocked("settings_ack_not_confirmed")
    return report
