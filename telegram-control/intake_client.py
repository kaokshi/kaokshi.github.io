"""Intake-only client for the existing editorial_zen_core.Core; no paid calls.
Run from a private, persistent executor host with the PR85 Core installed.
"""
import argparse
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def accept(core, envelope, expected_id):
    if (not isinstance(envelope, dict)
            or envelope.get("schema") != "zen-core/intake-v1"
            or envelope.get("mode") != "intents_only"
            or any(envelope.get(k) is not False for k in
                   ("executionEnabled", "publicationAuthorized", "rssEnabled"))):
        raise ValueError("unsafe_intake_envelope")
    intent = envelope.get("intent")
    if not isinstance(intent, dict) or set(intent) != {"id", "plan", "releaseAt"} or intent["id"] != expected_id:
        raise ValueError("intent_id_or_schema_mismatch")
    return core.submit(intent)


def pull(endpoint, secret, job_id):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/intake"):
        raise ValueError("invalid_intake_endpoint")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret or ""):
        raise ValueError("missing_intake_secret")
    if not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}", job_id):
        raise ValueError("invalid_job_id")
    request = urllib.request.Request(endpoint, data=json.dumps({"id": job_id}).encode(),
                                     headers={"Authorization": "Bearer " + secret,
                                              "Content-Type": "application/json"}, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("intake_too_large")
        return json.loads(data)


def main():
    from src.editorial_zen_core import Core
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    # The database must be on the existing executor's protected persistent volume.
    core = Core(args.db)
    if any(core.policy.get(k) is not False for k in ("execution_enabled", "publication_enabled", "rss_enabled")):
        raise ValueError("core_not_in_intake_only_mode")
    envelope = pull(args.endpoint, os.environ.get("ZEN_CORE_INTAKE_SECRET"), args.job)
    print(json.dumps({"id": accept(core, envelope, args.job), "mode": "intents_only",
                      "executed": False, "publicationAuthorized": False}))


if __name__ == "__main__":
    main()
