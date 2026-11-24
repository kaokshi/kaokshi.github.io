"""Local durable ZEN CORE contract. No model transport or release side effects."""
import argparse
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

CONFIG = Path(__file__).resolve().parents[1] / "config/zen_core.json"
STAGES = ("plan", "draft", "edit", "visual_plan", "images", "qa", "package")
CHANNELS = {"legends", "science"}
RESERVE_REASONS = {"images": {"missing_image", "image_correction"},
                   "edit": {"required_correction"},
                   "qa": {"required_check"}, "package": {"finish_started_article"}}


class CoreBlocked(ValueError):
    pass


def amount(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise CoreBlocked("invalid_rub_amount") from None
    if not number.is_finite() or number < 0:
        raise CoreBlocked("invalid_rub_amount")
    return number


def validate_plan(plan, release_at):
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
        if (not rows or len(set(channels)) != len(rows) or set(channels) - CHANNELS
                or any(type(count) is not int or count < 1 for count in counts)
                or sum(counts) > 9 or not isinstance(instructions, str) or len(instructions) > 1000):
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        raise CoreBlocked("invalid_core_intent") from None
    return {"items": [{"channel": channel, "count": count} for channel, count in zip(channels, counts)],
            "day": day, "instructions": instructions, "releaseAt": release_at}


class Core:
    def __init__(self, database, *, clock=None):
        self.db = sqlite3.connect(database, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy = json.loads(CONFIG.read_text())
        self.main = amount(self.policy["main_daily_rub"])
        self.reserve = amount(self.policy["reserve_daily_rub"])
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, intent TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stages (
                article TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(article, stage));
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
        intent = validate_plan(job.get("plan"), job.get("releaseAt"))
        encoded = json.dumps(intent, sort_keys=True, ensure_ascii=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?)", (job_id, encoded))
            if self.db.execute("SELECT intent FROM jobs WHERE id=?", (job_id,)).fetchone()[0] != encoded:
                raise CoreBlocked("job_id_conflict")
        return job_id

    @staticmethod
    def articles(job_id, plan):
        return [f"{job_id}/{row['channel']}/{index}"
                for row in plan["items"] for index in range(1, row["count"] + 1)]

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
            if previous.get("status") == "passed" and rows[stage] != encoded:
                raise CoreBlocked("passed_stage_immutable")
            if stage == "images" and any(
                    status == "passed" and key.startswith("slot_") and checks.get(key) != "passed"
                    for key, status in previous.get("checks", {}).items()):
                raise CoreBlocked("passed_image_slot_immutable")
            self.db.execute("INSERT OR REPLACE INTO stages VALUES (?, ?, ?)", (article, stage, encoded))

    def budget(self, day):
        rows = self.db.execute("SELECT * FROM spend WHERE day=? ORDER BY rowid", (day,)).fetchall()
        main_used = sum((amount(r["main"]) for r in rows), Decimal(0))
        reserve_used = sum((amount(r["reserve"]) for r in rows), Decimal(0))
        return {
            "day": day, "timezone": self.policy["timezone"], "mainDailyRub": str(self.main),
            "mainRemainingRub": str(max(Decimal(0), self.main - main_used)),
            "reserveDailyRub": str(self.reserve), "reserveRemainingRub": str(max(Decimal(0), self.reserve - reserve_used)),
            "reserveUses": [{"requestId": r["id"], "articleId": r["article"], "reason": r["reason"],
                             "rub": r["reserve"], "pending": r["actual"] is None}
                            for r in rows if amount(r["reserve"])],
            "unsettledRequest": self.db.execute("SELECT id FROM spend WHERE actual IS NULL").fetchone() is not None,
            "meteredRub": str(sum((amount(r["actual"]) for r in rows if r["actual"] is not None), Decimal(0))),
        }

    def reserve_call(self, request_id, article, stage, reason, estimate, *, slot=None):
        """Commit a reservation before any paid call. Unknown charges stop the lane."""
        if (not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9:_/-]{1,180}", request_id)
                or not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", reason)):
            raise CoreBlocked("invalid_reservation")
        bound = amount(estimate)
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
            if budget["unsettledRequest"]:
                raise CoreBlocked("unsettled_request")
            if stage == "images":
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
            if quoted_reserve > amount(budget["reserveRemainingRub"]):
                raise CoreBlocked("daily_budget_exhausted")
            self.db.execute("INSERT INTO spend VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                            (request_id, day, article, stage, reason, slot, str(quoted_main), str(quoted_reserve)))

    def settle(self, request_id, actual):
        actual = amount(actual)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM spend WHERE id=?", (request_id,)).fetchone()
            if not row or row["actual"] is not None:
                raise CoreBlocked("request_not_pending")
            reserved_main, reserved_extra = amount(row["main"]), amount(row["reserve"])
            if actual > reserved_main + reserved_extra:
                # Keep the reservation pending and block all later calls until reconciliation.
                raise CoreBlocked("actual_exceeds_reservation")
            main = min(actual, reserved_main)
            self.db.execute("UPDATE spend SET main=?, reserve=?, actual=? WHERE id=?",
                            (str(main), str(actual - main), str(actual), request_id))

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
                    articles.append({"id": article, "meteredRub": str(sum((amount(r[0]) for r in expenses), Decimal(0))),
                                     "stages": [{"id": stage, **json.loads(statuses.get(stage, '{"status":"not_started"}'))}
                                                for stage in STAGES]})
                # Owner instructions and private result bodies are not a control-plane payload.
                jobs.append({"id": row["id"], "items": plan["items"], "day": plan["day"],
                             "releaseAt": plan["releaseAt"], "articles": articles})
            budget = self.budget(self.day())
        return {"schema": "zen-core/v1", "executionMode": "intents_only", "executionEnabled": False,
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
