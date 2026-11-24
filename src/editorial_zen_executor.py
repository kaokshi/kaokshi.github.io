"""Opt-in private executor for the RouterAI editorial chain."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
from types import SimpleNamespace

from .editorial_images import ImageIndex
from .editorial_memory import digest
from .editorial_providers import Blocked
from .editorial_review import qa, sources
from .editorial_routerai_smoke import UnsafeToTest, call, credits
from .editorial_routerai_two_articles import MODELS, ROOT, Spend, evaluate_channel, json_bytes, money, safe_reason
from .editorial_zen_cloud import CloudCore, GitHubStore
from .editorial_zen_core import CONFIG, STAGES, CoreBlocked

SEED_ROUTE = "/models/" + MODELS["image"] + "/endpoints"


class Pause(CoreBlocked):
    pass


def classify(step, role):
    if {"plan": "brain", "draft": "draft", "edit": "edit"}.get(step) == role:
        return step, "new_article" if step != "edit" else "required_correction", None
    if step == "editorial_and_image_plan" and role == "brain":
        return "visual_plan", "required_check", None
    if step == "article_visual_review" and role == "brain":
        return "qa", "required_check", None
    image = re.fullmatch(r"image-(\d{2})-attempt-([123])(?:-(qas|scene-review|correction|retry-check))?", step)
    if image and role == "image" and not image[3]:
        return "images", "missing_image" if image[2] == "1" else "image_correction", int(image[1])
    if image and role == "brain" and image[3]:
        return "images", "required_check", None
    raise CoreBlocked("unknown_routerai_operation")


def verify_seed_route(token, attestation, store, transport=call):
    endpoints = transport(SEED_ROUTE, token).get("data", {}).get("endpoints", [])
    actual = {(row.get("tag"), row.get("country")) for row in endpoints}
    if actual != {("seed", "sg")} or len(endpoints) != 1:
        raise CoreBlocked("seedream_route_unverified")
    if (attestation.get("model") != MODELS["image"] or attestation.get("provider") != "seed"
            or attestation.get("country") != "sg" or attestation.get("via") != "RouterAI"
            or attestation.get("commercial_editorial_use") is not True
            or not isinstance(attestation.get("evidence_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", attestation["evidence_sha256"])):
        raise CoreBlocked("seedream_commercial_rights_unverified")
    evidence, _ = store.read("rights/seedream-commercial-evidence")
    if not evidence or digest(evidence) != attestation["evidence_sha256"]:
        raise CoreBlocked("seedream_commercial_evidence_missing")
    return attestation["evidence_sha256"]


class MeteredRouter(Spend):
    """Use the shared provider payloads, with durable reservations and response replay."""
    def __init__(self, session, token, *, transport=call, balance=credits):
        if not token:
            raise CoreBlocked("routerai_api_key_missing")
        self.session, self.token, self.transport, self.balance = session, token, transport, balance
        self.report = {"calls": []}
        catalog = transport("/models", token, limit=8_000_000, exact_numbers=True).get("data", [])
        self.prices = {row["id"]: row for row in catalog if row.get("id") in MODELS.values()}
        if set(self.prices) != set(MODELS.values()):
            raise CoreBlocked("required_model_missing_in_catalog")
        self.image_price = self.price("image", "image_output", "image")
        if self.image_price <= 0:
            raise CoreBlocked("seedream_price_unverified")

    def request(self, channel, stage, role, path, payload, bound, *, reserve=0, retain_image=None):
        phase, reason, slot = classify(stage, role)
        if role == "image":
            self.session.seed_rights(self.token, self.transport)
        operation = self.session.article + "/" + stage
        body = {"model": MODELS[role], **payload}
        fingerprint = digest(json_bytes({"path": path, "body": body}))
        name = f"requests/{stage}.json"
        response = self.session.read(name)
        if response is None:
            self.session.permit(phase)
            if self.balance(self.token) < bound + reserve:
                raise CoreBlocked("routerai_balance_insufficient")
            self.session.core.access(
                lambda core: core.reserve_call(operation, self.session.article, phase, reason, bound, slot=slot,
                                               hold=reserve), change=True)
            # From this point an absent response means uncertain billing. Never resend.
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
        self.report["calls"].append({"operation": operation, "model": MODELS[role],
                                     "cost_rub": str(actual), "replayed": response is not None})
        return answer


class Session:
    def __init__(self, core, article, *, target=None):
        self.core, self.article, self.target = core, article, target
        self.plan, self.next_stage = core.access(lambda c: c.article_work(article))
        self.channel = article.split("/")[1]
        self.prefix = "articles/" + digest(article) + "/"
        self.instructions = self.plan["instructions"]
        self.mode = self.plan.get("mode", "manual")
        self.rights = None
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
        if filename.endswith("/qwen-plan.json"):
            plan = json.loads(json.loads(data)["output"])
            self.core.access(lambda c: c.register_title(self.article, self.channel, plan["title"]), change=True)

    def permit(self, stage):
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

    def seed_rights(self, token, transport):
        raw = self.core.store.read("rights/seedream.json")[0]
        self.rights = verify_seed_route(token, json.loads(raw) if raw else {}, self.core.store, transport)

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
        if row.get("model") == MODELS["image"]:
            self.seed_rights(self.token, self.transport)
            if row.get("rights_evidence_sha256") != self.rights:
                raise CoreBlocked("accepted_image_rights_evidence_changed")
        self.core.access(lambda c: c.register_image(self.article, slot, row), change=True)
        path.mkdir(parents=True, exist_ok=True)
        (path / row["file"]).write_bytes(raw)
        (path / row["file"]).with_suffix(".json").write_bytes(prior)
        return row, raw

    def accept_image(self, slot, row, raw):
        if digest(raw) != row["sha256"]:
            raise CoreBlocked("accepted_image_integrity_failed")
        if row.get("model") == MODELS["image"]:
            if not self.rights:
                raise CoreBlocked("seedream_commercial_rights_unverified")
            row.update(rights_status="route_license_verified", rights_evidence_sha256=self.rights)
        self.core.access(lambda c: c.register_image(self.article, slot, row), change=True)
        self.save(f"accepted/{slot}.json", json_bytes(row))

    def complete_images(self, rows):
        if len(rows) != 7:
            raise CoreBlocked("image_slots_incomplete")
        self.pass_stage("images", {f"slot_{n}": "passed" for n in range(len(rows))})

    def quality(self, raw, audit, records, history, rows):
        report = qa(raw, self.channel, history, records, lambda *_: audit, lambda: 3600)
        encoded = json_bytes(report)
        self.save("quality/" + digest(encoded) + ".json", encoded)
        detectors_ok = (report["model_a"]["status"] == "completed"
                        and not report["model_a"]["exceeds_owner_review_threshold"])
        checks = {"model_a": detectors_ok,
                  "language_tool": report["grammar"]["status"] == "completed",
                  "editorial": report["editorial"]["status"] == "completed" and not report["issues"],
                  "facts": report["facts"]["status"] in {"completed", "not_applicable"},
                  "rights": all(row.get("rights_status") in {"license_metadata_verified",
                                                            "original_programmatic_graphic",
                                                            "route_license_verified"} for row in rows),
                  "image_review": True}
        statuses = {key: "passed" if ok else "blocked" for key, ok in checks.items()}
        if not all(checks.values()):
            self.core.access(lambda c: c.stage_result(
                self.article, "qa", {"status": "blocked", "reason": "quality_gate_incomplete",
                                     "checks": statuses}), change=True)
            raise CoreBlocked("quality_gate_incomplete")
        self.pass_stage("qa", statuses)
        self.permit("package")


def execute(core, article, token, *, target=None, transport=call, balance=credits, pipeline=evaluate_channel):
    if target is not None and target not in STAGES:
        raise CoreBlocked("unknown_target_stage")
    session = Session(core, article, target=target)
    if session.next_stage is None:
        return
    session.token, session.transport = token, transport
    router = MeteredRouter(session, token, transport=transport, balance=balance)
    from .editorial_brain import archive

    report = {"articles": {}}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        titles, prose, photos = archive(path)
        raw = session.read("inputs/history.json")
        if raw is None:
            others = core.access(lambda c: c.title_history(article, session.channel))
            raw = json_bytes({"titles": titles[session.channel] + others,
                              "prose": [(name, sorted(terms)) for name, terms in prose]})
            session.save("inputs/history.json", raw)
        history = json.loads(raw)
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
    p.add_argument("command", choices=("init", "submit", "snapshot", "approve", "advance", "reconcile"))
    p.add_argument("value", nargs="?")
    p.add_argument("--stage", choices=STAGES)
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
        elif args.command == "advance":
            if json.loads(CONFIG.read_text()).get("execution_enabled") is not True:
                raise CoreBlocked("zen_execution_disabled")
            execute(core, args.value, os.getenv("ROUTERAI_API_KEY"), target=args.stage)
        print(json.dumps(core.access(lambda c: c.snapshot()), ensure_ascii=False))
    except (Blocked, CoreBlocked, UnsafeToTest) as exc:
        print(json.dumps({"status": "blocked", "reason": safe_reason(exc),
                          "publicationAuthorized": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
