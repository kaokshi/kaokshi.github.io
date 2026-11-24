"""Private GitHub storage for ZEN CORE. Finance updates use blob compare-and-swap."""
import base64
from contextlib import closing
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener

from .editorial_http import NoRedirect
from .editorial_zen_core import Core, CoreBlocked

REPO = "kaokshi/zen-editorial-review"
ROOT = "zen-core/v1"
STATE = "ledger.sqlite"
MAX_BYTES = 20_000_000


class Conflict(CoreBlocked):
    pass


def blob_sha(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


class GitHubStore:
    def __init__(self, token, branch="main"):
        if not token or not re.fullmatch(r"[a-zA-Z0-9_-]+", branch):
            raise CoreBlocked("invalid_cloud_storage_config")
        self.branch = branch
        self.token = token
        self.opener = build_opener(NoRedirect)

    def api(self, route, payload=None, *, missing=False):
        req = Request("https://api.github.com/repos/" + REPO + route,
                      method="PUT" if payload is not None else "GET",
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github.object+json",
                               "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener.open(req, timeout=45) as response:
                raw = response.read(30_000_001)
                if len(raw) > 30_000_000:
                    raise CoreBlocked("cloud_response_too_large")
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 404 and missing:
                return None
            if payload is not None and exc.code in (409, 422):
                raise Conflict("cloud_compare_and_swap_conflict") from None
            raise CoreBlocked(f"cloud_http_{exc.code}") from None
        except (URLError, OSError, ValueError):
            raise CoreBlocked("cloud_storage_unavailable") from None

    def verified_path(self, name):
        if not re.fullmatch(r"[a-zA-Z0-9_./-]+", name) or ".." in name or name.startswith("/"):
            raise CoreBlocked("invalid_cloud_artifact_path")
        repo = self.api("")
        if repo.get("full_name") != REPO or repo.get("visibility") != "private" or repo.get("private") is not True:
            raise CoreBlocked("cloud_repository_not_private")
        return ROOT + "/" + name

    def read(self, name):
        path = self.verified_path(name)
        obj = self.api("/contents/" + path + "?ref=" + quote(self.branch), missing=True)
        if obj is None:
            return None, None
        sha = obj.get("sha", "")
        if (obj.get("path") != path or not re.fullmatch(r"[0-9a-f]{40}", sha)
                or type(obj.get("size")) is not int or not 0 <= obj["size"] <= MAX_BYTES):
            raise CoreBlocked("cloud_artifact_metadata_invalid")
        encoded = obj if obj.get("encoding") == "base64" else self.api("/git/blobs/" + sha)
        try:
            data = base64.b64decode("".join(encoded["content"].split()), validate=True)
        except (KeyError, ValueError, TypeError, AttributeError):
            raise CoreBlocked("cloud_artifact_encoding_invalid") from None
        if len(data) != obj["size"] or blob_sha(data) != sha:
            raise CoreBlocked("cloud_artifact_integrity_failed")
        return data, sha

    def write(self, name, data, previous):
        path = self.verified_path(name)
        if len(data) > MAX_BYTES:
            raise CoreBlocked("cloud_artifact_too_large")
        payload = {"message": "Checkpoint private ZEN CORE state",
                   "content": base64.b64encode(data).decode(), "branch": self.branch}
        if previous is not None:
            payload["sha"] = previous
        receipt = self.api("/contents/" + path, payload).get("content") or {}
        if receipt.get("path") != path or receipt.get("sha") != blob_sha(data):
            raise CoreBlocked("cloud_write_receipt_invalid")

    def immutable(self, name, data):
        try:
            self.write(name, data, None)
        except Conflict:
            existing, _ = self.read(name)
            if existing != data:
                raise CoreBlocked("cloud_immutable_artifact_conflict") from None


class CloudCore:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock

    def access(self, operation, *, change=False):
        for _ in range(5):
            raw, previous = self.store.read(STATE)
            core = Core(":memory:", clock=self.clock, checkpoint=raw)
            with closing(core.db):
                result = operation(core)
                if not change:
                    return result
                data = core.db.serialize()
            if data == raw:
                return result
            try:
                self.store.write(STATE, data, previous)
                return result
            except Conflict:
                # Only retry local state changes. No provider calls inside this transaction.
                continue
        raise CoreBlocked("cloud_ledger_busy")
