"""Offline-only fault injection for cloud checkpoints and model transport."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.editorial_zen_cloud import CloudCore, Conflict, STATE, blob_sha
from src.editorial_zen_core import CoreBlocked
from src.editorial_zen_executor import MODELS, classify, MeteredRouter, Pause, Session, seed_rights_verified, verify_seed_route


class Store:
    def __init__(self):
        self.data = {}
        self.conflict = False

    def read(self, path):
        raw = self.data.get(path)
        return raw, blob_sha(raw) if raw is not None else None

    def write(self, path, raw, previous):
        if self.conflict:
            self.conflict = False
            raise Conflict("synthetic_conflict")
        if self.read(path)[1] != previous:
            raise Conflict("synthetic_conflict")
        self.data[path] = raw

    def immutable(self, path, raw):
        if path not in self.data:
            self.write(path, raw, None)
        elif self.data[path] != raw:
            raise CoreBlocked("immutable_conflict")


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.cloud = CloudCore(self.store)
        self.article = "fixture/legends/1"
        self.intent = {"id": "fixture", "mode": "autonomous", "plan": {
            "items": [{"channel": "legends", "count": 1}], "day": None, "instructions": ""}}
        self.cloud.access(lambda c: c.submit(self.intent), change=True)
        self.calls = []
        self.fail = False

    def transport(self, path, _, payload=None, **kwargs):
        if path == "/models":
            return {"data": [{"id": model, "pricing_units": {"image_output": "image"},
                              "pricing": {"image_output": "1.92"}} for model in MODELS.values()]}
        self.calls.append((path, payload))
        if self.fail:
            raise TimeoutError("no billing receipt")
        return {"usage": {"cost": "1.25"}, "choices": [{"message": {"content": "synthetic"},
                                                       "finish_reason": "stop"}]}

    def router(self):
        return MeteredRouter(Session(CloudCore(self.store), self.article), "fixture",
                            transport=self.transport, balance=lambda _: 100)

    def plan(self, router, message="fixture"):
        return router.request("legends", "plan", "brain", "/chat/completions", {"prompt": message}, 2)

    def test_account_hold_closes_after_settlement_and_recovers_lost_close(self):
        events = []
        def account(action, job_id, operation, outcome=None):
            events.append((action, operation, outcome))
            if action == "close":
                self.assertEqual(self.cloud.access(lambda c: c.snapshot())["budget"]["meteredRub"], "1.25")
                if len(events) == 2:
                    raise CoreBlocked("synthetic_close_receipt_lost")
        def router():
            return MeteredRouter(Session(CloudCore(self.store), self.article, account=account), "fixture",
                                 transport=self.transport, balance=lambda _: 100)
        with self.assertRaisesRegex(CoreBlocked, "close_receipt_lost"):
            self.plan(router())
        self.plan(router())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([e[0] for e in events], ["claim", "close", "close"])
        self.assertEqual(events[0][1], self.article + "/plan")

    def test_cloud_conflict_retries_reservation_but_response_replays_once(self):
        self.store.conflict = True
        first = self.plan(self.router())
        self.assertEqual(self.plan(self.router()), first)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["model"], MODELS["brain"])
        self.assertEqual(self.cloud.access(lambda c: c.snapshot())["budget"]["meteredRub"], "1.25")
        with self.assertRaisesRegex(CoreBlocked, "input_changed"):
            self.plan(self.router(), "changed")

    def test_duplicate_film_is_replaced_once_from_the_remaining_frozen_catalogue(self):
        cinema_job = {"id": "cinema-fixture", "mode": "autonomous", "plan": {
            "items": [{"channel": "cinema", "count": 2}], "day": None, "instructions": ""}}
        self.cloud.access(lambda c: c.submit(cinema_job), change=True)
        first, second = ("cinema-fixture/cinema/1", "cinema-fixture/cinema/2")
        already_used = {"original_title": "First synthetic film", "year": 2025}
        alternative = {"original_title": "Second independent film", "year": 2025}
        options = [already_used, alternative]
        session = Session(self.cloud, second)
        self.assertIsNone(session.cinema_correction_options(already_used, options))
        self.cloud.access(lambda c: c.register_title(first, "cinema", "First synthetic film (2025)"),
                          change=True)
        self.assertEqual(session.cinema_correction_options(already_used, options), [alternative])
        self.assertEqual(session.cinema_correction_options(already_used, []), [alternative])
        self.assertEqual(classify("film-plan-correction", "brain"), ("plan", "required_correction", None))

    def test_image_attempt_replays_verified_source_bytes(self):
        from src.editorial_memory import digest

        session = Session(self.cloud, self.article)
        metadata = {"source": "Synthetic fixture"}
        name = "legends/attempts/image-00-attempt-1"
        self.assertIsNone(session.read_attempt_asset(name + ".source", name + ".json"))
        session.save(name + ".json", json.dumps({"asset": metadata, "original_sha256": digest(b"fixture")}).encode())
        with self.assertRaisesRegex(CoreBlocked, "image_attempt_artifact_integrity_failed"):
            session.read_attempt_asset(name + ".source", name + ".json")
        session.save(name + ".source", b"fixture")
        self.assertEqual(session.read_attempt_asset(name + ".source", name + ".json"), (b"fixture", metadata))

    def test_image_plan_correction_is_a_metered_visual_check(self):
        self.assertEqual(classify("image-plan-correction", "brain"), ("visual_plan", "required_check", None))

    def test_deadline_blocks_new_charge_but_replays_saved_response(self):
        router = self.router()
        router.session.paid_call_cutoff = 0
        with self.assertRaisesRegex(Pause, "pilot_paid_call_deadline"):
            self.plan(router)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.cloud.access(lambda c: c.snapshot())["budget"]["unsettledRequest"])

        saved = self.plan(self.router())
        self.assertEqual(self.plan(router), saved)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_response_never_calls_provider_again(self):
        self.fail = True
        with self.assertRaises(TimeoutError):
            self.plan(self.router())
        self.fail = False
        with self.assertRaisesRegex(CoreBlocked, "request_already_seen"):
            self.plan(self.router())
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.cloud.access(lambda c: c.snapshot())["budget"]["unsettledRequest"])

    def test_saved_response_recovers_crash_before_settlement(self):
        from src.editorial_zen_core import Core
        with patch.object(Core, "settle", side_effect=RuntimeError("fixture crash")):
            with self.assertRaises(RuntimeError):
                self.plan(self.router())
        self.plan(self.router())
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.cloud.access(lambda c: c.snapshot())["budget"]["unsettledRequest"])

    def test_seed_catalog_does_not_establish_commercial_rights(self):
        transport = lambda *_: {"data": {"endpoints": [{"tag": "seed", "country": "sg"}]}}
        verify_seed_route("fixture", transport)
        self.assertFalse(seed_rights_verified(self.store))
        wrong_route = lambda *_: {"data": {"endpoints": [{"tag": "other", "country": "sg"}]}}
        with self.assertRaisesRegex(CoreBlocked, "seedream_route_unverified"):
            verify_seed_route("fixture", wrong_route)

    def test_four_models_and_seedream_without_rights_document(self):
        import base64

        self.plan(self.router())
        session = Session(self.cloud, self.article)
        session.pass_stage("plan")
        router = self.router()
        router.request("legends", "draft", "draft", "/chat/completions", {}, 2)
        session.pass_stage("draft")
        router.request("legends", "edit", "edit", "/chat/completions", {}, 2)
        session.pass_stage("edit")
        session.pass_stage("visual_plan")
        raw = b"synthetic paid response"
        def transport(path, token, payload=None, **kwargs):
            if path.endswith("/endpoints"):
                return {"data": {"endpoints": [{"tag": "seed", "country": "sg"}]}}
            if path == "/images":
                self.calls.append((path, payload))
                return {"usage": {"cost": "1.92"},
                        "data": [{"b64_json": base64.b64encode(raw).decode()}]}
            return self.transport(path, token, payload, **kwargs)
        def generation():
            data = []
            MeteredRouter(Session(CloudCore(self.store), self.article), "fixture",
                          transport=transport, balance=lambda _: 100).generate(
                              "legends", "image-04-attempt-1", "synthetic", 0, data.append)
            return data
        self.assertEqual(generation(), [raw])
        self.assertEqual(generation(), [raw])
        self.assertEqual([body["model"] for _, body in self.calls], list(MODELS.values()))

    def test_semi_autonomous_approval_is_persistent(self):
        self.intent.update(id="semi", mode="semi_autonomous")
        self.cloud.access(lambda c: c.submit(self.intent), change=True)
        article = "semi/legends/1"
        for stage in ("plan", "draft", "edit", "visual_plan"):
            self.cloud.access(lambda c: c.stage_result(article, stage, {"status": "passed"}), change=True)
        with self.assertRaisesRegex(CoreBlocked, "human_stage"):
            Session(self.cloud, article).permit("images")
        self.cloud.access(lambda c: c.approve_stage(article, "images"), change=True)
        Session(CloudCore(self.store), article).permit("images")

    def test_image_rehydration_uses_saved_artifact_without_provider(self):
        from src.editorial_memory import digest
        session = Session(self.cloud, self.article)
        for stage in ("plan", "draft", "edit", "visual_plan"):
            session.pass_stage(stage)
        raw = b"synthetic retained asset"
        row = {"file": "image-04.jpg", "sha256": digest(raw), "original_sha256": digest(raw),
               "perceptual_hash": "ffffffffffffffff", "match_model": "fixture",
               "image_embedding": [1.0], "model": MODELS["image"], "rights_status": "rights_review_pending"}
        session.save("legends/images/image-04.jpg", raw)
        session.accept_image(4, row, raw)
        with tempfile.TemporaryDirectory() as directory:
            restored, data = Session(CloudCore(self.store), self.article).image(4, Path(directory))
            self.assertEqual(restored, row)
            self.assertEqual(data, raw)
            self.assertEqual((Path(directory) / row["file"]).read_bytes(), raw)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
