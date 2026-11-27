"""Local durable ZEN CORE contract. No model transport or release side effects."""
import argparse
import hashlib
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from .editorial_no_repeat import topic_duplicate_reason

CONFIG = Path(__file__).resolve().parents[1] / "config/zen_core.json"
STAGES = ("plan", "draft", "edit", "visual_plan", "images", "qa", "package")
CHANNELS = {"cinema", "legends", "science"}
RESERVE_REASONS = {"images": {"missing_image", "image_correction", "required_check"},
                   "edit": {"required_correction"}, "visual_plan": {"required_check", "required_correction"},
                   "qa": {"required_check"}, "package": {"finish_started_article"}}


class CoreBlocked(ValueError):
    pass


MODEL_ROLES = ("plan", "draft", "edit", "visual_plan", "images")


def validate_editorial_settings(settings):
    if not isinstance(settings, dict) or set(settings) != {"models"}:
        raise CoreBlocked("invalid_editorial_settings")
    spec = settings["models"]
    if not isinstance(spec, dict) or set(spec) != {"revision", "hash", "value"}:
        raise CoreBlocked("invalid_editorial_settings")
    revision, sha, models = spec["revision"], spec["hash"], spec["value"]
    if (type(revision) is not int or not 0 <= revision <= 2**53-1
            or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or not isinstance(models, dict) or set(models) != set(MODEL_ROLES)
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9._:/-]{1,180}", v) for v in models.values())
            or models["draft"] == models["edit"]):
        raise CoreBlocked("invalid_editorial_settings")
    canonical = json.dumps([[r, models[r]] for r in MODEL_ROLES], separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != sha:
        raise CoreBlocked("editorial_settings_hash_mismatch")
    return {"models": {"revision": revision, "hash": sha, "value": dict(models)}}


def amount(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise CoreBlocked("invalid_rub_amount") from None
    if not number.is_finite() or number < 0:
        raise CoreBlocked("invalid_rub_amount")
    return number


def validate_plan(plan, release_at, enabled_channels=CHANNELS):
    """Accept the existing ZEN CORE intent fields without enabling its scheduler."""
    try:
        rows = plan["items"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError
        channels = [row["channel"] for row in rows]
        counts = [row["count"] for row in rows]
        day = plan["day"]
        instructions = plan["instructions"]
        if day is not None and date.fromisoformat(day).isoformat() != day:
            raise ValueError
        if release_at is not None:
            instant = datetime.fromisoformat(release_at)
            if instant.utcoffset() is None:
                raise ValueError
        if (not rows or len(set(channels)) != len(rows) or set(channels) - enabled_channels
                or any(type(count) is not int or count < 1 for count in counts)
                or sum(counts) > 9 or not isinstance(instructions, str) or len(instructions) > 1000):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        raise CoreBlocked("invalid_core_intent") from None
    return {"items": [{"channel": channel, "count": count} for channel, count in zip(channels, counts)],
            "day": day, "instructions": instructions, "releaseAt": release_at}


class Core:
    def __init__(self, database, *, clock=None, checkpoint=None):
        self.db = sqlite3.connect(database, timeout=10)
        self.db.row_factory = sqlite3.Row
        if checkpoint is not None:
            self.db.deserialize(checkpoint)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy = json.loads(CONFIG.read_text())
        if self.policy.get("private_executor_available") is True and os.getenv("ZEN_CORE_PRODUCTION_ENABLED") == "true":
            self.policy["execution_enabled"] = True
        configured_channels = self.policy.get("production_channels", sorted(CHANNELS))
        if not isinstance(configured_channels, list) or any(not isinstance(c, str) or c not in CHANNELS for c in configured_channels):
            raise CoreBlocked("invalid_production_channels")
        self.channels = set(configured_channels)
        self.main = amount(self.policy["main_daily_rub"])
        self.reserve = amount(self.policy["reserve_daily_rub"])
        self.rolling_cap = amount(self.policy.get("rolling_30_day_cap_rub", "9000"))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, intent TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS job_modes (job TEXT PRIMARY KEY, mode TEXT NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS stage_intents (id TEXT PRIMARY KEY, job TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recovery_attempts (article TEXT NOT NULL, stage TEXT NOT NULL, count INTEGER NOT NULL, last_at INTEGER NOT NULL, PRIMARY KEY(article,stage));
            CREATE TABLE IF NOT EXISTS stages (
                article TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(article, stage));
            CREATE TABLE IF NOT EXISTS approvals (article TEXT, stage TEXT, PRIMARY KEY(article, stage));
            CREATE TABLE IF NOT EXISTS titles (article TEXT PRIMARY KEY, channel TEXT, title TEXT);
            CREATE TABLE IF NOT EXISTS prose (article TEXT PRIMARY KEY, sha TEXT, terms TEXT);
            CREATE TABLE IF NOT EXISTS images (article TEXT, slot INTEGER, evidence TEXT,
                                               PRIMARY KEY(article, slot));
            CREATE TABLE IF NOT EXISTS reconciliations (request TEXT PRIMARY KEY, evidence_sha TEXT);
            CREATE TABLE IF NOT EXISTS spend (
                id TEXT PRIMARY KEY, day TEXT NOT NULL, article TEXT NOT NULL,
                stage TEXT NOT NULL, reason TEXT NOT NULL, slot INTEGER,
                main TEXT NOT NULL, reserve TEXT NOT NULL, actual TEXT);
        """)

    def day(self):
        return self.clock().astimezone(ZoneInfo(self.policy["timezone"])).date().isoformat()

    def submit(self, job):
        if not isinstance(job, dict):
            raise CoreBlocked("invalid_core_intent")
        job_id = job.get("id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,120}", job_id):
            raise CoreBlocked("invalid_job_id")
        intent = validate_plan(job.get("plan"), job.get("releaseAt"), self.channels)
        mode = job.get("mode", "manual")
        if mode not in ("manual", "semi_autonomous", "autonomous"):
            raise CoreBlocked("invalid_execution_mode")
        if mode != "manual":
            intent["mode"] = mode
        if "editorialSettings" in job:
            intent["editorialSettings"] = validate_editorial_settings(job["editorialSettings"])
        encoded = json.dumps(intent, sort_keys=True, ensure_ascii=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?)", (job_id, encoded))
            if self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()[0] != encoded:
                raise CoreBlocked("job_id_conflict")
        return job_id

    def mode_state(self, job_id, plan):
        row = self.db.execute("SELECT mode,revision FROM job_modes WHERE job=?", (job_id,)).fetchone()
        return (row["mode"], row["revision"]) if row else (plan.get("mode", "manual"), 0)

    def apply_mode(self, job_id, mode, revision):
        if mode not in ("manual", "semi_autonomous", "autonomous") or type(revision) is not int or not 0 < revision <= 2**53-1:
            raise CoreBlocked("invalid_mode_transition")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            job = self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise CoreBlocked("unknown_job")
            plan = json.loads(job[0])
            previous, current = self.mode_state(job_id, plan)
            if revision < current or revision == current and mode != previous:
                raise CoreBlocked("stale_mode_transition")
            if revision == current:
                return
            self.db.execute("INSERT OR REPLACE INTO job_modes VALUES (?,?,?)", (job_id, mode, revision))
            # Reconfirm future human boundaries after a mode change; completed stages remain immutable.
            for article in self.articles(job_id, plan):
                self.db.execute("DELETE FROM approvals WHERE article=? AND stage NOT IN (SELECT stage FROM stages WHERE article=? AND json_extract(result,'$.status')='passed')", (article, article))

    @staticmethod
    def articles(job_id, plan):
        return [f"{job_id}/{row['channel']}/{index}"
                for row in plan["items"] for index in range(1, row["count"] + 1)]

    def article_work(self, article):
        if not isinstance(article, str) or "/" not in article:
            raise CoreBlocked("unknown_article_or_stage")
        job_id = article.split("/", 1)[0]
        job = self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise CoreBlocked("unknown_article_or_stage")
        plan = json.loads(job[0])
        if article not in self.articles(job_id, plan):
            raise CoreBlocked("unknown_article_or_stage")
        if plan["day"] and plan["day"] > self.day():
            # Scheduler-created orders are arriving from an authenticated alarm.
            # Permit just the configured maximum preparation window across midnight.
            release = datetime.fromisoformat(plan["releaseAt"]) if plan["releaseAt"] else None
            if (not job_id.startswith("scheduled:") or not release
                    or release.astimezone(ZoneInfo(self.policy["timezone"])).date().isoformat() != plan["day"]
                    or not timedelta(0) < release - self.clock() <= timedelta(hours=24)):
                raise CoreBlocked("scheduled_job_not_due")
        rows = dict(self.db.execute("SELECT stage,result FROM stages WHERE article=?", (article,)))
        mode, revision = self.mode_state(job_id, plan)
        plan = {**plan, "mode": mode, "modeRevision": revision}
        return plan, next((stage for stage in STAGES
                           if json.loads(rows.get(stage, "{}")).get("status") != "passed"), None)

    def accept_stage_intent(self, job_id, command):
        # Acknowledgment is durable, but does not mark a stage passed or approve a paid call.
        if (not isinstance(command, dict) or set(command) != {"id", "article", "stage", "mode", "modeRevision", "createdAt"}
                or type(command["modeRevision"]) is not int or type(command["createdAt"]) is not int
                or command["stage"] not in STAGES or not isinstance(command["article"], str)
                or not command["article"].startswith(job_id + "/")
                or command["id"] != f"stage:{command['modeRevision']}:{command['article']}:{command['stage']}"):
            raise CoreBlocked("invalid_stage_intent")
        encoded = json.dumps(command, sort_keys=True)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            job = self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise CoreBlocked("unknown_job")
            plan = json.loads(job[0])
            mode, revision = self.mode_state(job_id, plan)
            if (mode != command["mode"] or revision != command["modeRevision"]
                    or not (mode == "manual" or mode == "semi_autonomous" and command["stage"] in ("images", "package"))):
                raise CoreBlocked("stale_stage_intent")
            old = self.db.execute("SELECT payload FROM stage_intents WHERE id=?", (command["id"],)).fetchone()
            if old:
                if old[0] != encoded:
                    raise CoreBlocked("stage_intent_conflict")
                return
            rows = self.check_stage(command["article"], command["stage"])
            next_stage = next((s for s in STAGES if json.loads(rows.get(s, "{}")).get("status") != "passed"), None)
            if next_stage != command["stage"]:
                raise CoreBlocked("stage_not_available")
            self.db.execute("INSERT INTO stage_intents VALUES (?,?,?)", (command["id"], job_id, encoded))

    def stage_acknowledgment(self, job_id, command_id):
        row = self.db.execute("SELECT payload FROM stage_intents WHERE job=? AND id=?", (job_id, command_id)).fetchone()
        if not row:
            raise CoreBlocked("stage_intent_not_recorded")
        c = json.loads(row[0])
        return {"id": c["id"], "modeRevision": c["modeRevision"], "status": "recorded_only"}

    def approve_stage(self, article, stage):
        if stage not in ("images", "package"):
            raise CoreBlocked("invalid_human_approval_stage")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check_stage(article, stage)
            self.db.execute("INSERT OR IGNORE INTO approvals VALUES (?, ?)", (article, stage))

    def check_approval(self, article, stage):
        if not self.db.execute("SELECT 1 FROM approvals WHERE article=? AND stage=?", (article, stage)).fetchone():
            raise CoreBlocked("human_stage_approval_required")

    def title_history(self, article, channel):
        return [row[0] for row in self.db.execute(
            "SELECT title FROM titles WHERE channel=? AND article<>? ORDER BY article", (channel, article))]

    def register_title(self, article, channel, title):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check_stage(article, "plan")
            previous = self.db.execute("SELECT title FROM titles WHERE article=?", (article,)).fetchone()
            if previous and previous[0] == title:
                return
            if previous or not isinstance(title, str) or not 4 <= len(title) <= 240:
                raise CoreBlocked("cloud_title_checkpoint_invalid")
            if topic_duplicate_reason(channel, title, self.title_history(article, channel)):
                raise CoreBlocked("duplicate_topic")
            self.db.execute("INSERT INTO titles VALUES (?, ?, ?)", (article, channel, title))

    def register_prose(self, article, text):
        from .editorial_memory import digest
        from .editorial_no_repeat import words

        sha = digest(text)
        terms = words(text)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check_stage(article, "visual_plan")
            previous = self.db.execute("SELECT sha FROM prose WHERE article=?", (article,)).fetchone()
            if previous:
                if previous[0] != sha:
                    raise CoreBlocked("accepted_prose_immutable")
                return
            for row in self.db.execute("SELECT sha,terms FROM prose"):
                other = set(json.loads(row[1]))
                if sha == row[0] or (terms and other and len(terms & other) / len(terms | other) > .52):
                    raise CoreBlocked("archive_overlap")
            self.db.execute("INSERT INTO prose VALUES (?, ?, ?)", (article, sha, json.dumps(sorted(terms))))

    def register_image(self, article, slot, row):
        from .editorial_images import duplicate_reason

        fields = ("sha256", "original_sha256", "perceptual_hash", "match_model", "image_embedding")
        fingerprint = {key: row[key] for key in fields}
        encoded = json.dumps(fingerprint, sort_keys=True)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.check_stage(article, "images")
            previous = self.db.execute("SELECT evidence FROM images WHERE article=? AND slot=?",
                                       (article, slot)).fetchone()
            if previous:
                if previous[0] != encoded:
                    raise CoreBlocked("accepted_image_immutable")
                return
            others = [json.loads(r[0]) for r in self.db.execute("SELECT evidence FROM images")]
            hashes = [(r[key], r["perceptual_hash"]) for r in others for key in ("sha256", "original_sha256")]
            vectors = [r["image_embedding"] for r in others if r["match_model"] == row["match_model"]]
            reason = duplicate_reason(row["sha256"], row["original_sha256"], row["perceptual_hash"],
                                      row["image_embedding"], hashes, vectors)
            if reason:
                raise CoreBlocked(reason)
            self.db.execute("INSERT INTO images VALUES (?, ?, ?)", (article, slot, encoded))

    def check_stage(self, article, stage):
        job_id = article.split("/", 1)[0]
        job = self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or article not in self.articles(job_id, json.loads(job[0])) or stage not in STAGES:
            raise CoreBlocked("unknown_article_or_stage")
        rows = dict(self.db.execute("SELECT stage, result FROM stages WHERE article=?", (article,)))
        if any(json.loads(rows.get(step, "{}")).get("status") != "passed"
               for step in STAGES[:STAGES.index(stage)]):
            raise CoreBlocked("required_stage_not_passed")
        return rows

    def stage_result(self, article, stage, result):
        """Only trusted workers record check outcomes; intake JSON cannot assert a pass."""
        if result.get("status") not in {"passed", "failed", "blocked"}:
            raise CoreBlocked("invalid_stage_result")
        reason = result.get("reason")
        checks = result.get("checks", {})
        if (reason is not None and (not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,120}", reason))
                or not isinstance(checks, dict) or any(
                    not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", key)
                    or value not in ("passed", "failed", "blocked", "pending")
                    for key, value in checks.items())):
            raise CoreBlocked("invalid_stage_checks")
        encoded = json.dumps({"status": result["status"], "reason": reason, "checks": checks},
                             sort_keys=True, ensure_ascii=False)
        if len(encoded) > 20000:
            raise CoreBlocked("stage_result_too_large")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.check_stage(article, stage)
            previous = json.loads(rows.get(stage, "{}"))
            if previous.get("status") == "passed":
                existing = {key: previous.get(key) for key in ("status", "reason", "checks")}
                if existing != json.loads(encoded):
                    raise CoreBlocked("passed_stage_immutable")
                return
            if stage == "images" and any(
                    status == "passed" and key.startswith("slot_") and checks.get(key) != "passed"
                    for key, status in previous.get("checks", {}).items()):
                raise CoreBlocked("passed_image_slot_immutable")
            if result["status"] in ("failed", "blocked"):
                payload = json.loads(encoded)
                payload["blockedAt"] = (previous.get("blockedAt") if previous.get("reason") == reason else None)
                if payload["blockedAt"] is None:
                    payload["blockedAt"] = int(self.clock().timestamp() * 1000)
                encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            payload = json.loads(encoded)
            now = int(self.clock().timestamp() * 1000)
            payload["firstRecordedAt"] = previous.get("firstRecordedAt", now)
            payload["recordedAt"] = now
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            self.db.execute("INSERT OR REPLACE INTO stages VALUES (?, ?, ?)", (article, stage, encoded))

    def budget(self, day):
        rows = self.db.execute("SELECT * FROM spend WHERE day=? ORDER BY rowid", (day,)).fetchall()
        main_used = sum((amount(r["main"]) for r in rows), Decimal(0))
        reserve_used = sum((amount(r["reserve"]) for r in rows), Decimal(0))
        start = (date.fromisoformat(day) - timedelta(days=29)).isoformat()
        period = self.db.execute("SELECT main,reserve,actual FROM spend WHERE day BETWEEN ? AND ?", (start, day)).fetchall()
        period_used = sum((amount(r["main"]) + amount(r["reserve"]) for r in period), Decimal(0))
        period_metered = sum((amount(r["actual"]) for r in period if r["actual"] is not None), Decimal(0))
        return {
            "rollingStart": start, "rollingCapRub": str(self.rolling_cap),
            "rollingRemainingRub": str(max(Decimal(0), self.rolling_cap - period_used)),
            "rollingMeteredRub": str(period_metered), "rollingOverrun": period_used > self.rolling_cap,
            "day": day, "timezone": self.policy["timezone"], "mainDailyRub": str(self.main),
            "mainRemainingRub": str(max(Decimal(0), self.main - main_used)),
            "reserveDailyRub": str(self.reserve), "reserveRemainingRub": str(max(Decimal(0), self.reserve - reserve_used)),
            "reserveUses": [{"requestId": r["id"], "articleId": r["article"], "reason": r["reason"],
                             "rub": r["reserve"], "pending": r["actual"] is None}
                            for r in rows if amount(r["reserve"])],
            "overrun": main_used > self.main or reserve_used > self.reserve,
            "unsettledRequest": self.db.execute("SELECT id FROM spend WHERE actual IS NULL").fetchone() is not None,
            "meteredRub": str(sum((amount(r["actual"]) for r in rows if r["actual"] is not None), Decimal(0))),
        }

    def reserve_call(self, request_id, article, stage, reason, estimate, *, slot=None, hold=0):
        """Commit a reservation before any paid call. Unknown charges stop the lane."""
        if (not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9:_/-]{1,180}", request_id)
                or not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", reason)):
            raise CoreBlocked("invalid_reservation")
        bound = amount(estimate)
        hold = amount(hold)
        if bound <= 0:
            raise CoreBlocked("invalid_reservation")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            day = self.day()
            results = self.check_stage(article, stage)
            if json.loads(results.get(stage, "{}")).get("status") == "passed":
                raise CoreBlocked("stage_already_passed")
            if self.db.execute("SELECT 1 FROM spend WHERE id=?", (request_id,)).fetchone():
                raise CoreBlocked("request_already_seen")
            budget = self.budget(day)
            if budget["overrun"]:
                raise CoreBlocked("daily_budget_overrun")
            if budget["unsettledRequest"]:
                raise CoreBlocked("unsettled_request")
            if budget["rollingOverrun"] or bound + hold > amount(budget["rollingRemainingRub"]):
                raise CoreBlocked("rolling_budget_exhausted")
            if stage == "images" and reason != "required_check":
                if type(slot) is not int or not 0 <= slot <= 7:
                    raise CoreBlocked("invalid_image_slot")
                previous = json.loads(results.get(stage, "{}")).get("checks", {})
                if previous.get(f"slot_{slot}") == "passed":
                    raise CoreBlocked("image_slot_already_passed")
                prior = self.db.execute("SELECT count(*) FROM spend WHERE article=? AND stage='images' AND slot=?",
                                        (article, slot)).fetchone()[0]
                if prior >= 1 + self.policy["max_image_retries"]:
                    raise CoreBlocked("image_retry_limit")
                if reason != ("image_correction" if prior else "missing_image"):
                    raise CoreBlocked("invalid_image_attempt_reason")
            elif slot is not None:
                raise CoreBlocked("unexpected_image_slot")
            quoted_main = min(bound, amount(budget["mainRemainingRub"]))
            quoted_reserve = bound - quoted_main
            if quoted_reserve and (reason not in RESERVE_REASONS.get(stage, set())
                                   or json.loads(results.get("draft", "{}")).get("status") != "passed"):
                raise CoreBlocked("reserve_only_for_started_articles")
            if (quoted_reserve > amount(budget["reserveRemainingRub"]) or
                    bound + hold > amount(budget["mainRemainingRub"]) + amount(budget["reserveRemainingRub"])):
                raise CoreBlocked("daily_budget_exhausted")
            self.db.execute("INSERT INTO spend VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                            (request_id, day, article, stage, reason, slot, str(quoted_main), str(quoted_reserve)))

    def release_unsent(self, request_id):
        """Only call after a definite pre-transport denial."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT actual FROM spend WHERE id=?", (request_id,)).fetchone()
            if not row or row["actual"] is not None:
                raise CoreBlocked("request_not_pending")
            self.db.execute("DELETE FROM spend WHERE id=?", (request_id,))

    def settle(self, request_id, actual):
        actual = amount(actual)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM spend WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise CoreBlocked("request_not_pending")
            if row["actual"] is not None:
                if amount(row["actual"]) == actual:
                    return
                raise CoreBlocked("settlement_amount_conflict")
            reserved_main, reserved_extra = amount(row["main"]), amount(row["reserve"])
            if actual > reserved_main + reserved_extra:
                # Keep the reservation pending and block all later calls until reconciliation.
                raise CoreBlocked("actual_exceeds_reservation")
            main = min(actual, reserved_main)
            self.db.execute("UPDATE spend SET main=?, reserve=?, actual=? WHERE id=?",
                            (str(main), str(actual - main), str(actual), request_id))

    def clear_blocked_stage(self, article, stage, expected_reason):
        """Remove one blocked/failed stage record so the same stage may run again.

        Trusted-operator action only: it never marks a stage passed, never sends a
        call and never touches spend, images, approvals, titles, prose or any
        immutable artifact. Passed per-slot checks survive as a 'released' record
        so paid image slots cannot be regenerated. The removed record is returned
        as evidence.
        """
        if stage not in STAGES:
            raise CoreBlocked("unknown_article_or_stage")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            budget = self.budget(self.day())
            if budget["unsettledRequest"] or budget["overrun"] or budget["rollingOverrun"]:
                raise CoreBlocked("financial_reconciliation_required")
            rows = self.check_stage(article, stage)
            previous = json.loads(rows.get(stage, "{}"))
            if previous.get("status") not in ("failed", "blocked"):
                raise CoreBlocked("stage_not_blocked")
            if previous.get("reason") != expected_reason:
                raise CoreBlocked("released_reason_mismatch")
            # Passed per-slot checks guard against paid regeneration of accepted
            # images (reserve_call, stage_result). They must survive the release.
            kept = {key: value for key, value in previous.get("checks", {}).items()
                    if value == "passed" and key.startswith("slot_")}
            if kept:
                now = int(self.clock().timestamp() * 1000)
                self.db.execute("INSERT OR REPLACE INTO stages VALUES (?, ?, ?)", (article, stage, json.dumps(
                    {"status": "released", "reason": None, "releasedReason": expected_reason, "checks": kept,
                     "firstRecordedAt": previous.get("firstRecordedAt", now), "recordedAt": now},
                    sort_keys=True)))
            else:
                self.db.execute("DELETE FROM stages WHERE article=? AND stage=?", (article, stage))
        return {"article": article, "stage": stage, "releasedReason": expected_reason,
                "previousStatus": previous["status"]}

    def reconcile(self, request_id, actual, evidence_sha):
        """Book a provider-confirmed charge without reopening its operation key."""
        if not isinstance(evidence_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", evidence_sha):
            raise CoreBlocked("invalid_billing_evidence")
        actual = amount(actual)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM spend WHERE id=?", (request_id,)).fetchone()
            if not row or row["actual"] is not None:
                raise CoreBlocked("request_not_pending")
            main, reserve = amount(row["main"]), amount(row["reserve"])
            # Unapproved overages are charged to main. They cannot expand the reserve.
            extra = min(max(Decimal(0), actual - main), reserve)
            self.db.execute("UPDATE spend SET main=?, reserve=?, actual=? WHERE id=?",
                            (str(actual - extra), str(extra), str(actual), request_id))
            self.db.execute("INSERT INTO reconciliations VALUES (?, ?)", (request_id, evidence_sha))

    def snapshot(self):
        jobs = []
        with self.db:
            self.db.execute("BEGIN")
            for row in self.db.execute("SELECT * FROM jobs ORDER BY rowid"):
                plan = json.loads(row["intent"])
                articles = []
                for article in self.articles(row["id"], plan):
                    statuses = dict(self.db.execute("SELECT stage,result FROM stages WHERE article=?", (article,)))
                    expenses = self.db.execute("SELECT actual FROM spend WHERE article=? AND actual IS NOT NULL", (article,))
                    image_slots = [r[0] for r in self.db.execute(
                        "SELECT slot FROM images WHERE article=? ORDER BY slot", (article,))]
                    articles.append({"id": article, "acceptedImageSlots": image_slots, "meteredRub": str(sum((amount(r[0]) for r in expenses), Decimal(0))),
                                     "stages": [{"id": stage, **json.loads(statuses.get(stage, '{"status":"not_started"}'))}
                                                for stage in STAGES]})
                # Owner instructions and private result bodies are not a control-plane payload.
                mode, revision = self.mode_state(row["id"], plan)
                jobs.append({"id": row["id"], "items": plan["items"], "day": plan["day"],
                             "releaseAt": plan["releaseAt"], "mode": mode, "modeRevision": revision,
                             "articles": articles})
            budget = self.budget(self.day())
        enabled = self.policy.get("execution_enabled") is True
        return {"schema": "zen-core/v1", "executionMode": "private_executor" if enabled else "intents_only",
                "executionEnabled": enabled,
                "publicationAuthorized": False, "rssEnabled": False, "budget": budget, "jobs": jobs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("command", choices=("submit", "snapshot"))
    parser.add_argument("intent", nargs="?")
    args = parser.parse_args()
    core = Core(args.db)
    if args.command == "submit":
        if not args.intent:
            parser.error("submit requires a trusted intent JSON file")
        core.submit(json.loads(Path(args.intent).read_text()))
    print(json.dumps(core.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
