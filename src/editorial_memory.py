"""Private editorial state, restartable tasks and durable daily reservations."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Lock, RLock
from time import monotonic, sleep
from uuid import uuid4

from .editorial_groq_budget import assessment, estimate_tokens, operation, usage_fields
from .editorial_providers import Blocked, GROQ, ProviderError


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def write_json(path, value):
    write(path, json_bytes(value))


class StorageError(RuntimeError):
    """Private storage could not be verified or synchronized."""


class PrivateGit:
    """Verify the exact destination's visibility before every transfer."""
    def __init__(self, name, path, token):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", name or "") or not token:
            raise StorageError("private_storage_credentials_missing")
        self.name = name
        self.path = path
        self.env = {**os.environ, "GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"}
        self.git = ["git", "-c", "credential.helper=", "-c",
                    "credential.helper=!gh auth git-credential"]
        self.verify()
        self.branch = self.command("gh", "api", f"repos/{self.name}", "-q", ".default_branch")
        if not path.exists():
            self.command(*self.git, "clone", "--depth", "1", "--no-tags",
                         "--branch", self.branch, "https://github.com/" + name + ".git", str(path))
        actual = self.command(*self.git, "-C", str(path), "remote", "get-url", "origin")
        if actual != "https://github.com/" + name + ".git":
            raise StorageError("private_storage_remote_mismatch")
        self.command(*self.git, "-C", str(path), "pull", "--ff-only")

    def command(self, *args):
        try:
            proc = subprocess.run(args, env=self.env, text=True, capture_output=True,
                                  timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise StorageError("private_storage_connection_failed") from None
        if proc.returncode:
            raise StorageError("private_storage_command_failed")
        return proc.stdout.strip()

    def verify(self):
        if self.command("gh", "api", f"repos/{self.name}", "-q", ".visibility") != "private":
            raise StorageError("storage_repository_must_be_private")

    def restore(self, root, day):
        from .editorial_delivery import owner_key, restore_blob, state_key
        self.state_root, self.day = root, day
        owner_key()
        if not state_key():
            raise StorageError("storage_configuration_pending: private_checkpoint_key_required")
        checkpoints = sorted((self.path / "editorial-sealed").glob("????-??-??/checkpoint.enc"), reverse=True)
        for path in checkpoints:
            if path.parent.name <= day:
                restore_blob(path.read_bytes(), root, path.parent.name)
                break

    def checkpoint(self):
        from .editorial_delivery import save_outputs
        sealed = self.path / "editorial-sealed" / self.day
        save_outputs(self.state_root, self.day, sealed)
        command = [*self.git, "-C", str(self.path)]
        self.command(*command, "add", "--", "editorial-sealed")
        if self.command(*command, "status", "--porcelain", "--", "editorial-sealed"):
            self.command(*command, "-c", "user.name=Editorial Workflow",
                         "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                         "commit", "-m", "Checkpoint editorial state")
        if self.command(*command, "rev-list", f"origin/{self.branch}..HEAD"):
            self.verify()
            self.command(*command, "push", "origin", f"HEAD:refs/heads/{self.branch}")

    def owner_packet(self, day):
        self.verify()
        base = self.path / "editorial-sealed"
        path = base / day / "owner-review.enc"
        if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)
                or not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(base.resolve())):
            raise StorageError("delivery_integration_pending: private_edition_not_found")
        return path

    def review_url(self, day):
        return f"https://github.com/{self.name}/tree/{self.branch}/editorial-sealed/{day}"

    def notify(self, day, status):
        # The private issue is an owner notification. A matching issue prevents
        # resends after a runner stops between GitHub accepting it and checkpointing.
        self.verify()
        title = f"Дзен — согласование за {day}"
        query = f'.[] | select(.title == "{title}") | .html_url'
        prior = self.command("gh", "api", "--paginate",
                             f"repos/{self.name}/issues?state=all", "-q", query)
        if prior:
            return prior.splitlines()[0]
        owner = self.name.split("/")[0]
        return self.command("gh", "api", "-X", "POST", f"repos/{self.name}/issues",
                            "-f", f"title={title}", "-f", f"assignees[]={owner}",
                            "-f", f"body=Статус комплекта: {status}. Расшифровка выполняется в авторизованной среде.\n\n{self.review_url(day)}",
                            "-q", ".html_url")


class Memory:
    def __init__(self, root, day, cutoff, config, private_git=None, retry=False, lane="production"):
        self.root, self.day, self.cutoff, self.config = root, day, cutoff, config
        self.folder = root / day
        self.file = self.folder / "state.json"
        self.data = read_json(self.file, {"day": day, "tasks": {}, "articles": {}, "calls": [],
                                        "provider_stops": {}, "created_at": now()})
        self.private_git, self.retry = private_git, retry
        self.lock = RLock()
        self.groq_lock = Lock()
        self.groq_lane = lane
        self.groq_prior = {}
        if ledger := os.environ.get("EDITORIAL_GROQ_LEDGER"):
            self.groq_prior.update({row["request_id"]: row for row in read_json(Path(ledger), [])})
        if probe := os.environ.get("EDITORIAL_GROQ_CURRENT_PROBE"):
            self.groq_prior.update({row["request_id"]: row for row in read_json(Path(probe), [])})
        for file in sorted(root.glob("????-??-??/state.json"))[-14:]:
            if file != self.file:
                for row in read_json(file).get("calls", []):
                    if row.get("provider") == "groq" and row.get("request_id"):
                        self.groq_prior[row["request_id"]] = row

    def groq_rows(self):
        with self.lock:
            rows = dict(self.groq_prior)
            for index, row in enumerate(self.data["calls"]):
                if row["provider"] == "groq":
                    rows[row.get("request_id", f"{self.day}-legacy-{index}")] = row
            return list(rows.values())

    def call_groq(self, task, article, stage, prompt, max_tokens, callback, *, repair=False):
        with self.groq_lock:
            with self.lock:
                if any(row["task"] == task and row["provider"] == "groq" and row["status"] == "reserved"
                       for row in self.data["calls"]):
                    raise Blocked("groq_previous_request_outcome_unknown")
            rows = self.groq_rows()
            estimate = estimate_tokens(prompt, max_tokens, rows, stage)
            mode = self.config["groq_budget"]
            while True:
                utc_now = datetime.now(timezone.utc)
                reason, delay = assessment(rows, mode, self.groq_lane, utc_now, estimate, reserve=repair)
                if reason:
                    raise ProviderError(reason)
                if self.time_left() < delay + 105:
                    raise ProviderError("groq_quota_wait_exceeds_deadline")
                if not delay:
                    break
                sleep(min(delay, 30))
            metrics = {"request_id": uuid4().hex, "model": GROQ, "lane": self.groq_lane,
                       "operation": operation(stage), "repair": repair, "prompt_bytes": estimate[2],
                       "estimated_input_tokens": estimate[0], "estimated_output_tokens": estimate[1],
                       "estimated_total_tokens": sum(estimate[:2]), "sent_at": now()}
            return self.call(task, article, "groq", callback, metrics=metrics)

    def save(self, durable=True):
        with self.lock:
            write_json(self.file, self.data)
            if durable and self.private_git:
                self.private_git.checkpoint()

    def state(self, task, status, *, durable=True, **fields):
        with self.lock:
            task.update(status=status, **fields)
            event = {"at": now(), "status": status}
            task["events"].append(event)
            if os.environ.get("GITHUB_ACTIONS") == "true" and status in {
                    "running", "completed", "blocked", "failed", "retry_pending"}:
                from .editorial_chat_status import code
                details = {**event, "operation": task["operation"], "article": task["article"],
                           "executor": task["executor"], "attempt": task["attempts"]}
                if status in {"blocked", "failed", "retry_pending"}:
                    details["reason_code"] = code(task.get("error"))
                print(json.dumps(details), flush=True)
            if status in {"completed", "failed", "blocked"}:
                task["used_calls"] = sum(call["task"] == task["id"] for call in self.data["calls"])
            self.save(durable)

    def time_left(self):
        return (self.cutoff - datetime.now(timezone.utc)).total_seconds()

    def task(self, article, stage, executor, inputs, action, *, minimum_seconds=55, validator=None, reusable=None):
        input_sha = digest(json_bytes(inputs))
        task_id = f"{article}.{stage}.{input_sha[:16]}"
        with self.lock:
            task = self.data["tasks"].get(task_id)
            if task and task["status"] == "completed":
                if ("result" not in task or task.get("result_sha256") != digest(json_bytes(task["result"]))):
                    raise Blocked("completed_task_result_integrity_failed")
                if (not self.retry or not reusable or reusable(task["result"]) or task["attempts"] >= 2
                        or self.time_left() < minimum_seconds):
                    return task["result"]
            if not task:
                task = self.data["tasks"][task_id] = {
                    "id": task_id, "article": article, "channel": article.split("-")[0],
                    "operation": stage, "executor": executor, "attempts": 0,
                    "input_sha256": input_sha, "inputs": inputs,
                    "deadline": self.cutoff.isoformat(), "priority": 1, "events": [],
                }
                self.state(task, "created", durable=False)
            if task["attempts"] >= 2 or (task["attempts"] and not self.retry):
                raise Blocked(task.get("error", "interrupted_use_retry"))
            if self.time_left() < minimum_seconds:
                self.state(task, "retry_pending", error="production_deadline")
                raise Blocked("production_deadline")
            self.state(task, "assigned", durable=False, attempts=task["attempts"] + 1)
            self.state(task, "running")
        started = monotonic()
        try:
            result = action(task_id)
            self.state(task, "validating", durable=False)
            if validator:
                validator(result)
        except (Blocked, ValueError, OSError) as exc:
            if isinstance(exc, Blocked):
                error = str(exc)
            else:
                error = type(exc).__name__
            status = "blocked" if isinstance(exc, Blocked) else "failed"
            self.state(task, status, error=error, elapsed_seconds=round(monotonic() - started, 2))
            raise Blocked(error) from None
        self.state(task, "completed", result=result, error=None, result_sha256=digest(json_bytes(result)),
                   result_ref=f"state.json#tasks/{task_id}", elapsed_seconds=round(monotonic() - started, 2))
        return result

    def call(self, task_id, article, provider, callback, *, metrics=None, editorial_edit=False):
        with self.lock:
            # A runner can stop after the external provider accepted a request but
            # before a response is saved. Never send an untraceable duplicate on retry.
            # A persisted "responded" call is reused by Article.model -> response().
            if any(row["task"] == task_id and row["provider"] == provider
                   and row["status"] == "reserved" for row in self.data["calls"]):
                raise ProviderError("previous_provider_request_outcome_unknown", stop=True)
            stopped = self.data["provider_stops"].get(provider)
            if stopped:
                raise ProviderError("provider_stopped_" + stopped, stop=True)
            count = Counter(row["provider"] for row in self.data["calls"])
            for_article = Counter(row["provider"] for row in self.data["calls"]
                                  if row["article"] == article)
            if (count[provider] >= self.config["daily_calls"][provider]
                    or for_article[provider] >= self.config["per_article_calls"][provider]):
                raise ProviderError("call_budget_exhausted", stop=True)
            if editorial_edit:
                edits = sum(row["article"] == article and row["provider"] == "openrouter"
                            and self.data["tasks"].get(row["task"], {}).get("operation", "").startswith(
                                ("full-edit", "repair-")) for row in self.data["calls"])
                if edits >= 2:
                    raise ProviderError("openrouter_two_edit_requests_per_article_exhausted")
            reservation = {"task": task_id, "article": article, "provider": provider,
                           "at": now(), "status": "reserved", **(metrics or {})}
            self.data["calls"].append(reservation)
            self.save()
        try:
            answer = callback()
        except ProviderError as exc:
            with self.lock:
                reservation.update(status="failed", error=str(exc), completed_at=now(), **exc.telemetry)
                if exc.stop:
                    self.data["provider_stops"][provider] = str(exc)
                self.save()
            raise
        with self.lock:
            reservation.update(status="responded", completed_at=now())
            if isinstance(answer, dict):
                reservation["usage"] = answer.get("usage", {})
                if provider == "groq":
                    reservation.update(usage_fields(reservation["usage"]))
                    reservation.update(answer.get("telemetry", {}))
                # Persist the completed response before downstream validation.
                reservation["answer"] = answer
                self.save()
        return answer

    def response(self, task_id):
        with self.lock:
            return next((x["answer"] for x in reversed(self.data["calls"])
                         if x["task"] == task_id and "answer" in x), None)

    def article(self, article, **fields):
        with self.lock:
            self.data["articles"].setdefault(article, {}).update(fields)
            self.save()
