"""Explicit, synthetic encrypted read/write probe of the owner-only GitHub store.

No real drafts or secrets are written, and no production schedules are enabled.
Run only in a trusted environment with explicit authorization to write the probe.
"""
import base64
import json
import os
import re
import subprocess
from uuid import uuid4

from .editorial_delivery import owner_key, seal_state, unseal_state
from .editorial_memory import digest
from .editorial_http import Blocked

EXPECTED_REPOSITORY = "kaokshi/zen-editorial-review"


def api(token, endpoint, *, method="GET", data=None):
    env = {**os.environ, "GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
    argv = ["gh", "api", "-X", method, endpoint]
    if data is not None:
        argv += ["--input", "-"]
    try:
        response = subprocess.run(argv, input=json.dumps(data) if data is not None else None,
                                  env=env, capture_output=True, text=True, timeout=35, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("private_probe_transport_unavailable") from None
    if response.returncode:
        # Do not leak token-adjacent CLI diagnostics or response bodies to logs.
        raise Blocked("private_probe_request_failed_" + method.lower())
    try:
        return json.loads(response.stdout)
    except ValueError:
        raise Blocked("private_probe_invalid_json") from None


def probe(repository, token, *, call=api):
    if repository != EXPECTED_REPOSITORY or not token:
        raise Blocked("private_probe_repository_or_token_missing")
    owner_key()
    plaintext = ("zen-editorial-storage-test:" + uuid4().hex).encode("ascii")
    ciphertext = seal_state(plaintext)
    if ciphertext == plaintext or unseal_state(ciphertext) != plaintext:
        raise Blocked("private_probe_encryption_failed")
    metadata = call(token, f"repos/{repository}")
    if metadata.get("visibility") != "private" or metadata.get("full_name") != repository:
        raise Blocked("private_probe_repository_not_private")
    path = "editorial-sealed/probes/" + uuid4().hex + ".enc"
    endpoint = f"repos/{repository}/contents/{path}"
    created_sha = None
    try:
        created = call(token, endpoint, method="PUT", data={
            "message": "Temporary encrypted editorial storage connectivity probe",
            "content": base64.b64encode(ciphertext).decode("ascii")})
        created_sha = (created.get("content") or {}).get("sha")
        if not isinstance(created_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", created_sha):
            raise Blocked("private_probe_write_unconfirmed")
        fetched = call(token, endpoint)
        try:
            returned = base64.b64decode(fetched["content"], validate=False)
        except (KeyError, TypeError, ValueError):
            raise Blocked("private_probe_read_invalid") from None
        if digest(returned) != digest(ciphertext) or unseal_state(returned) != plaintext:
            raise Blocked("private_probe_ciphertext_mismatch")
    finally:
        if created_sha:
            call(token, endpoint, method="DELETE", data={
                "message": "Remove encrypted editorial connectivity probe",
                "sha": created_sha})
    return {"status": "private_encrypted_roundtrip_verified", "repository_visibility": "private",
            "test_payload_only": True, "cleanup_confirmed": True,
            "publication_authorized": False}


def main():
    repository = os.environ.get("EDITORIAL_DAILY_REPOSITORY", "")
    token = os.environ.get("EDITORIAL_PRIVATE_TOKEN", "")
    result = probe(repository, token)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
