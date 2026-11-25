"""Opt-in private executor for the RouterAI editorial chain."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from time import monotonic
from threading import Lock
from types import SimpleNamespace

from .editorial_cinema import (archive_film_identity, archive_source_record, eligible_year, has_usable_stills,
                               verify_original_year)
from .editorial_images import ImageIndex
from .editorial_memory import digest
from .editorial_http import Blocked
from .editorial_review import qa, sources
from .editorial_no_repeat import topic_duplicate_reason
from .editorial_routerai import UnsafeToTest, call, credits
from .editorial_routerai_two_articles import MODELS, ROOT, Spend, cinema_key, evaluate_channel, json_bytes, money, safe_reason
from .editorial_zen_cloud import CloudCore, GitHubStore
from .editorial_zen_core import CONFIG, STAGES, CoreBlocked
from .editorial_zen_recovery import release_blocked_stage
from .editorial_zen_control import configured_guard, configured_account

SEED_ROUTE = "/models/" + MODELS["image"] + "/endpoints"


class Pause(CoreBlocked):
    pass


def classify(step, role):
    if {"plan": "brain", "draft": "draft", "edit": "edit"}.get(step) == role:
        return step, "new_article" if step != "edit" else "required_correction", None
    if step == "film-plan-correction" and role == "brain":
        return "plan", "required_correction", None
    if re.fullmatch(r"edit-correction-[2-5]", step) and role == "edit":
        return "edit", "required_correction", None
    if re.fullmatch(r"audit-correction-[2-5]", step) and role == "edit":
        return "visual_plan", "required_correction", None
    if (step in {"editorial_and_image_plan", "image-plan-correction"} or re.fullmatch(r"audit-correction-[2-5]-qa", step)) and role == "brain":
        return "visual_plan", "required_check", None
    if step == "article_visual_review" and role == "brain":
        return "qa", "required_check", None
    image = re.fullmatch(r"image-(\d{2})-attempt-([123])(?:-(qas|scene-review|correction|retry-check))?", step)
    if image and role == "image" and not image[3]:
        return "images", "missing_image" if image[2] == "1" else "image_correction", int(image[1])
    if image and role == "brain" and image[3]:
        return "images", "required_check", None
    raise CoreBlocked("unknown_routerai_operation")


def verify_seed_route(token, transport=call):
    endpoints = transport(SEED_ROUTE, token).get("data", {}).get("endpoints", [])
    actual = {(row.get("tag"), row.get("country")) for row in endpoints}
    if actual != {("seed", "sg")} or len(endpoints) != 1:
        raise CoreBlocked("seedream_route_unverified")


def seed_rights_verified(store):
    raw, _ = store.read("rights/seedream.json")
    try:
        attestation = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        return False
    if (not isinstance(attestation, dict) or attestation.get("model") != MODELS["image"] or attestation.get("provider") != "seed"
            or attestation.get("country") != "sg" or attestation.get("via") != "RouterAI"
            or attestation.get("commercial_editorial_use") is not True
            or not isinstance(attestation.get("evidence_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", attestation["evidence_sha256"])):
        return False
    evidence, _ = store.read("rights/seedream-commercial-evidence")
    return bool(evidence and digest(evidence) == attestation["evidence_sha256"])


class MeteredRouter(Spend):
    """Use the shared provider payloads, with durable reservations and response replay."""
    def __init__(self, session, token, *, transport=call, balance=credits, use_seedream=True):
        if not token:
            raise CoreBlocked("routerai_api_key_missing")
        self.session, self.token, self.transport, self.balance = session, token, transport, balance
        self.report = {"calls": []}
        catalog = transport("/models", token, limit=8_000_000, exact_numbers=True).get("data", [])
        self.prices = {row["id"]: row for row in catalog if row.get("id") in MODELS.values()}
        required = set(MODELS.values()) if use_seedream else set(MODELS.values()) - {MODELS["image"]}
        if not required <= self.prices.keys():
            raise CoreBlocked("required_model_missing_in_catalog")
        self.image_price = self.price("image", "image_output", "image") if use_seedream else 0
        if use_seedream and self.image_price <= 0:
            raise CoreBlocked("seedream_price_unverified")

    def request(self, channel, stage, role, path, payload, bound, *, reserve=0, retain_image=None):
        if role == "image" and not self.image_price:
            raise CoreBlocked("paid_image_generation_disabled")
        phase, reason, slot = classify(stage, role)
        operation = self.session.article + "/" + stage
        body = {"model": MODELS[role], **payload}
        fingerprint = digest(json_bytes({"path": path, "body": body}))
        name = f"requests/{stage}.json"
        response = self.session.read(name)
        if response is None:
            if self.session.paid_call_cutoff is not None and monotonic() >= self.session.paid_call_cutoff:
                raise Pause("pilot_paid_call_deadline")
            self.session.permit(phase)
            if role == "image":
                verify_seed_route(self.token, self.transport)
            if self.balance(self.token) < bound + reserve:
                raise CoreBlocked("routerai_balance_insufficient")
            self.session.core.access(
                lambda core: core.reserve_call(operation, self.session.article, phase, reason, bound, slot=slot,
                                               hold=reserve), change=True)
            try:
                self.session.account_call("claim", operation)
            except CoreBlocked as exc:
                if str(exc) == "account_claim_rejected":
                    self.session.core.access(lambda core: core.release_unsent(operation), change=True)
                raise
            # After taking the shared charge lock recheck balance and owner control.
            # If either fails before the POST, retain the lock for explicit reconciliation.
            if self.session.account is not None and self.balance(self.token) < bound + reserve:
                raise CoreBlocked("routerai_balance_insufficient")
            self.session.check_control()
            answer = self.transport(path, self.token, body,
                                    limit=12_000_000 if role == "image" else 350000,
                                    timeout=180, exact_numbers=True)
            self.session.save(name, json.dumps({"fingerprint": fingerprint, "answer": answer},
                                               ensure_ascii=False, default=str).encode())
        else:
            saved = json.loads(response)
            if saved["fingerprint"] != fingerprint:
                raise CoreBlocked("paid_checkpoint_input_changed")
            answer = saved["answer"]

        usage = answer.get("usage")
        if not isinstance(usage, dict):
            raise CoreBlocked("provider_usage_unavailable")
        actual = money(usage.get("cost"))
        if retain_image:
            import base64
            try:
                images = answer["data"]
                if len(images) != 1:
                    raise ValueError
                raw = base64.b64decode(images[0]["b64_json"], validate=True)
                if not raw:
                    raise ValueError
            except (KeyError, IndexError, TypeError, ValueError):
                raise CoreBlocked("seedream_invalid_image_response") from None
            retain_image(raw)
        self.session.core.access(lambda core: core.settle(operation, actual), change=True)
        # A saved response can be settled and closed again without another provider call.
        self.session.account_call("close", operation, "settled")
        self.report["calls"].append({"operation": operation, "model": MODELS[role],
                                     "cost_rub": str(actual), "replayed": response is not None})
        return answer


class Session:
    def __init__(self, core, article, *, target=None, guard=None, account=None):
        self.core, self.article, self.target = core, article, target
        self._assets = {}
        self.paid_call_cutoff = None
        self.guard = guard
        self.account = account
        self.plan, self.next_stage = core.access(lambda c: c.article_work(article))
        self.channel = article.split("/")[1]
        self.prefix = "articles/" + digest(article) + "/"
        self.instructions = self.plan["instructions"]
        self.mode = self.plan.get("mode", "manual")
        self.mode_revision = self.plan.get("modeRevision", 0)
        if self.next_stage and self.mode == "manual" and self.target != self.next_stage:
            raise CoreBlocked("manual_next_stage_required")

    def read(self, name):
        return self.core.store.read(self.prefix + name)[0]

    def save(self, name, data):
        self.core.store.immutable(self.prefix + name, data)

    def evidence(self, filename, data):
        # Stage artifacts are immutable. Diagnostic records can contain nondeterministic metadata.
        if filename.endswith("-qa.json"):
            filename = "reports/" + digest(data) + "/" + filename
        self.save(filename, data)
        if filename.endswith("/qwen-plan.json") and self.channel == "legends":
            plan = json.loads(json.loads(data)["output"])
            self.core.access(lambda c: c.register_title(self.article, self.channel, plan["title"]), change=True)

    def account_call(self, action, operation, outcome=None):
        if self.account is not None:
            return self.account(action, self.article.split("/", 1)[0], operation, outcome)

    def check_control(self):
        if self.guard is not None:
            revision = self.guard(self.article.split("/", 1)[0], self.mode)
            if revision != self.mode_revision:
                raise CoreBlocked("control_mode_revision_changed")

    def permit(self, stage):
        self.check_control()
        if self.target and STAGES.index(stage) > STAGES.index(self.target):
            raise Pause("requested_stage_boundary")
        if self.mode == "semi_autonomous" and stage in ("images", "package"):
            self.core.access(lambda c: c.check_approval(self.article, stage))

    def pass_stage(self, stage, checks=None):
        # Replaying the pipeline must not reapprove an already completed step.
        def finish(c):
            rows = c.check_stage(self.article, stage)
            if json.loads(rows.get(stage, "{}")).get("status") != "passed":
                c.stage_result(self.article, stage, {"status": "passed", "checks": checks or {}})
        self.core.access(finish, change=True)
        if stage == self.target:
            raise Pause("requested_stage_completed")

    def approve_visual_plan(self, text):
        self.core.access(lambda c: c.register_prose(self.article, text), change=True)
        self.pass_stage("visual_plan")

    def source_records(self, urls, domains):
        prior = self.read("inputs/sources.json")
        if prior is not None:
            return json.loads(prior)
        records = sources(urls, domains)
        if len(records) >= 2 and all(row["status"] == "accessible" for row in records):
            self.save("inputs/sources.json", json_bytes(records))
        return records

    def register_topic(self, title):
        self.core.access(lambda c: c.register_title(self.article, self.channel, title), change=True)

    def shared_asset(self, path):
        if path not in self._assets:
            self._assets[path] = self.core.store.read(path)[0]
        return self._assets[path]

    def cinema_registry(self):
        raw = self.read("inputs/cinema-sources.json")
        if raw is not None:
            return {qid: row for qid, row in json.loads(raw).items() if eligible_year(row.get("year"))}
        raw = self.core.store.read("sources/cinema.json")[0]
        if raw is None:
            raw = (ROOT / "config/editorial_cinema.json").read_bytes()
        registry = json.loads(raw)
        if not isinstance(registry, dict) or any(
                not re.fullmatch(r"Q[1-9][0-9]*", qid) or not isinstance(row, dict)
                or not isinstance(row.get("title"), str) or not isinstance(row.get("original_title"), str)
                or type(row.get("year")) is not int or not isinstance(row.get("stills"), list)
                for qid, row in registry.items()):
            raise CoreBlocked("cinema_source_registry_invalid")
        for qid, row in list(registry.items()):
            if not eligible_year(row["year"]):
                registry.pop(qid)
                continue
            if row.get("source_page"):
                try:
                    identity = archive_film_identity(qid, row)["film"]
                    verify_original_year(row["year"], identity)
                    archive_source_record(row["source_page"])
                except (Blocked, KeyError, ValueError):
                    registry.pop(qid)
                    continue
            film = {"qid": qid, "original_title": row["original_title"], "year": row["year"]}
            if not has_usable_stills(film, registry, private_read=self.shared_asset):
                registry.pop(qid)
        if registry:
            self.save("inputs/cinema-sources.json", json_bytes(registry))
        return registry

    def cinema_correction_options(self, film, options):
        key = "inputs/cinema-correction-options.json"
        prior = self.read(key)
        if prior is not None:
            return json.loads(prior)
        used = self.core.access(lambda c: c.title_history(self.article, "cinema"))
        if not topic_duplicate_reason("cinema", cinema_key(film), used):
            return None
        available = [row for row in options if not topic_duplicate_reason("cinema", cinema_key(row), used)]
        if not available:
            raise CoreBlocked("cinema_no_distinct_replacement")
        self.save(key, json_bytes(available))
        return available

    def cinema_evidence(self, plan, registry, verify):
        name = "inputs/cinema-evidence.json"
        raw = self.read(name)
        result = json.loads(raw) if raw is not None else verify(plan, registry)
        verify_original_year(plan["year"], result["film"])
        identity = cinema_key(result["film"])
        self.register_topic(identity)
        if raw is None:
            self.save(name, json_bytes(result))
        return result

    def document(self, title, article, checks, rows, image_folder):
        from .editorial_package import package
        from .editorial_review import canonical

        existing = self.read("delivery/article.docx")
        if existing is None:
            normalized_hash = digest(canonical(article))
            report = self.quality_report
            if report.get("text_sha256") != normalized_hash:
                raise CoreBlocked("docx_quality_version_mismatch")
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory) / f"{self.channel}-{int(self.article.rsplit('/', 1)[1]):02d}"
                try:
                    result = package(folder, title, [], report, rows, image_folder,
                                     {"publication_authorized": False, "status": "review_pending"})
                    if result["cover"] != rows[0]["file"]:
                        raise ValueError("docx_cover_order_invalid")
                    existing = (folder / "Статья.docx").read_bytes()
                except (OSError, ValueError) as exc:
                    raise CoreBlocked("docx_packaging_failed") from exc
            self.save("delivery/article.docx", existing)
        self.save("delivery/manifest.json", json_bytes({
            "article": self.article, "sha256": digest(existing), "images": len(rows),
            "text_sha256": checks["text_sha256"], "chars_without_spaces": checks["chars_without_spaces"],
            "editorial_attempts": checks["editorial_attempts"], "publication_authorized": False,
        }))

    def image(self, slot, path):
        prior = self.read(f"accepted/{slot}.json")
        if prior is None:
            return None
        row = json.loads(prior)
        if row["file"] != f"image-{slot:02d}.jpg":
            raise CoreBlocked("accepted_image_filename_invalid")
        raw = self.read(f"{self.channel}/images/{row['file']}")
        if raw is None or digest(raw) != row["sha256"]:
            raise CoreBlocked("accepted_image_integrity_failed")
        self.core.access(lambda c: c.register_image(self.article, slot, row), change=True)
        path.mkdir(parents=True, exist_ok=True)
        (path / row["file"]).write_bytes(raw)
        (path / row["file"]).with_suffix(".json").write_bytes(prior)
        return row, raw

    def accept_image(self, slot, row, raw):
        if digest(raw) != row["sha256"]:
            raise CoreBlocked("accepted_image_integrity_failed")
        self.core.access(lambda c: c.register_image(self.article, slot, row), change=True)
        self.save(f"accepted/{slot}.json", json_bytes(row))

    def complete_images(self, rows):
        if len(rows) != 7:
            raise CoreBlocked("image_slots_incomplete")
        self.pass_stage("images", {f"slot_{n}": "passed" for n in range(len(rows))})

    def quality(self, raw, audit, records, history, rows):
        report = qa(raw, self.channel, history, records, lambda *_: audit, lambda: 3600)
        from html import escape
        from .editorial_enhancements import readability
        from .editorial_review import canonical

        self.quality_report = report
        report["readability"] = readability({"channel": self.channel,
                                               "body_html": "<p>" + escape(canonical(report["article"])) + "</p>"})
        encoded = json_bytes(report)
        self.save("quality/" + digest(encoded) + ".json", encoded)
        detectors_ok = (report["model_a"]["status"] == "completed"
                        and not report["model_a"]["exceeds_owner_review_threshold"])
        seed_verified = (any(row.get("model") == MODELS["image"] for row in rows)
                         and seed_rights_verified(self.core.store))
        checks = {"model_a": detectors_ok,
                  "language_tool": report["grammar"]["status"] == "completed",
                  "editorial": report["editorial"]["status"] == "completed" and not report["issues"],
                  "facts": report["facts"]["status"] in {"completed", "not_applicable"},
                  "rights": all(seed_verified if row.get("model") == MODELS["image"] else
                                row.get("rights_status") in {"license_metadata_verified",
                                                             "original_programmatic_graphic"} for row in rows),
                  "image_review": True}
        if self.channel == "cinema":
            del checks["rights"]
            evidence = json.loads(self.read("inputs/cinema-evidence.json"))
            checks["film_still_provenance"] = all(
                row.get("kind") == "photo" and row.get("rights_status") == "not_checked"
                and row.get("film_id") == evidence["film"]["qid"] for row in rows)
        statuses = {key: "passed" if ok else "blocked" for key, ok in checks.items()}
        if not all(checks.values()):
            self.core.access(lambda c: c.stage_result(
                self.article, "qa", {"status": "blocked", "reason": "quality_gate_incomplete",
                                     "checks": statuses}), change=True)
            raise CoreBlocked("quality_gate_incomplete")
        self.pass_stage("qa", statuses)
        self.permit("package")


def execute(core, article, token, *, target=None, transport=call, balance=credits, pipeline=evaluate_channel, guard=None, account=None, paid_call_cutoff=None):
    if target is not None and target not in STAGES:
        raise CoreBlocked("unknown_target_stage")
    session = Session(core, article, target=target, guard=guard or configured_guard())
    session.paid_call_cutoff = paid_call_cutoff
    session.check_control()
    if session.next_stage is None:
        return
    session.account = account or configured_account()
    image_policy = json.loads(CONFIG.read_bytes()).get("image_sources", "licensed_photos")
    if image_policy not in {"licensed_photos", "seedream"}:
        raise CoreBlocked("invalid_image_sources")
    if image_policy == "seedream" and not seed_rights_verified(core.store):
        raise CoreBlocked("seedream_commercial_terms_unverified")
    router = MeteredRouter(session, token, transport=transport, balance=balance,
                           use_seedream=image_policy == "seedream")
    from .editorial_archive import archive

    report = {"articles": {}}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        titles, prose, photos = archive(path)
        if session.channel == "cinema":
            catalog = json.loads((ROOT / "config/movie_reference_index.json").read_bytes())
            titles["cinema"].extend(cinema_key({"original_title": row["film_title"],
                                                "year": row["original_year"]}) for row in catalog.values())
        raw = session.read("inputs/history.json")
        if raw is None:
            others = core.access(lambda c: c.title_history(article, session.channel))
            raw = json_bytes({"titles": titles[session.channel] + others,
                              "prose": [(name, sorted(terms)) for name, terms in prose]})
            session.save("inputs/history.json", raw)
        history = json.loads(raw)
        if session.channel == "cinema":
            name = "inputs/cinema-plan-titles.json"
            frozen = session.read(name)
            if frozen is None and session.read("requests/plan.json") is None:
                current = core.access(lambda c: c.title_history(article, session.channel))
                frozen = json_bytes(list(dict.fromkeys([*history["titles"], *current])))
                session.save(name, frozen)
            if frozen is not None:
                history["titles"] = json.loads(frozen)
        index = ImageIndex(SimpleNamespace(root=path, lock=Lock(), save=lambda: None), photos)
        catalogue = json.loads((ROOT / "config/editorial_science_sources.json").read_text())
        try:
            pipeline(session.channel, router, session.evidence, report, index, path, catalogue,
                     (history["titles"], [(name, set(terms)) for name, terms in history["prose"]]),
                     None, checkpoints=session)
            if not report["articles"][session.channel].get("assembled"):
                raise CoreBlocked("article_quality_or_layout_blocked")
            session.pass_stage("package", {"private_package": "passed"})
        except Pause:
            return
        except (Blocked, CoreBlocked, UnsafeToTest) as exc:
            reason = safe_reason(exc).lower()
            def record(c):
                _, pending = c.article_work(article)
                if pending:
                    rows = c.check_stage(article, pending)
                    checks = json.loads(rows.get(pending, "{}")).get("checks", {})
                    c.stage_result(article, pending, {"status": "blocked", "reason": reason,
                                                      "checks": checks})
            core.access(record, change=True)
            raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("init", "submit", "snapshot", "approve", "advance", "reconcile",
                                       "release"))
    p.add_argument("value", nargs="?")
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--reason")
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args()
    core = CloudCore(GitHubStore(os.getenv("EDITORIAL_PRIVATE_TOKEN")))
    try:
        if args.command == "init":
            core.access(lambda c: None, change=True)
        elif args.command == "submit":
            intent = json.loads(Path(args.value).read_text())
            core.access(lambda c: c.submit(intent), change=True)
        elif args.command == "approve":
            core.access(lambda c: c.approve_stage(args.value, args.stage), change=True)
        elif args.command == "reconcile":
            evidence = Path(args.value).read_bytes()
            row = json.loads(evidence)
            sha = digest(evidence)
            core.store.immutable("billing-evidence/" + sha + ".json", evidence)
            core.access(lambda c: c.reconcile(row["requestId"], row["actualRub"], sha), change=True)
        elif args.command == "release":
            # Bookkeeping only: no provider call, no charge, no stage marked passed.
            if not args.stage or not args.reason:
                raise CoreBlocked("release_arguments_required")
            result = core.access(lambda c: release_blocked_stage(
                c, args.value, args.stage, args.reason, confirm=args.confirm), change=True)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        elif args.command == "advance":
            if json.loads(CONFIG.read_text()).get("execution_enabled") is not True:
                raise CoreBlocked("zen_execution_disabled")
            execute(core, args.value, os.getenv("ROUTERAI_API_KEY"), target=args.stage)
        print(json.dumps(core.access(lambda c: c.snapshot()), ensure_ascii=False))
    except (Blocked, CoreBlocked, UnsafeToTest) as exc:
        answer = {"status": "blocked", "reason": safe_reason(exc), "publicationAuthorized": False}
        try:
            # Operators need current stage/charge state to pick the next safe step.
            answer["snapshot"] = core.access(lambda c: c.snapshot())
        except Exception:
            answer["snapshot"] = "unavailable"
        print(json.dumps(answer, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
