"""Synthetic fixture tests for the fail-closed editorial vision route."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from src import editorial_scene_qa as qa
from src.editorial_chat_plaintext import push_private_main
from src.editorial_images import ImageIndex, generated, generated_scene, select_cover
from src.editorial_offline_review_pack import require_audited_bundle
from src.editorial_memory import Memory, StorageError, digest, json_bytes
from src.editorial_providers import Blocked
from tests.editorial_scene_fixtures import certify

SOURCE = "A maintenance room held a steel lever and a switch."
PROPOSAL = {"scene_type": "object", "location": "a maintenance room", "time_of_day": "",
            "characters": [], "must_have": ["a steel lever"], "must_not_have": ["a staircase"],
            "optional_details": [], "visual_description": "A steel lever in a maintenance room.",
            "evidence_spans": {"a maintenance room": "maintenance room", "a steel lever": "a steel lever"}}
CLIP = {"passed": True, "topic": .24}
qa_judge = qa.judge


def card():
    return qa.make_card("synthetic-01", 1, digest(SOURCE), SOURCE, PROPOSAL)


def vision(*states):
    return lambda c, im: {"findings": [
        {"role": role, "element": item, "status": state}
        for (role, item), state in zip(qa.questions_for(c)[0], states, strict=True)]}


class SceneTests(unittest.TestCase):
    def test_packet_requires_current_model_evidence_for_every_channel(self):
        for channel in ("legends-01", "science-01", "cinema-01"):
            sha = digest(SOURCE)
            row = certify({"section": 1, "sha256": "fixture"}, channel, False, sha, SOURCE)
            qa.require_packet_rows([row], sha, article_id=channel)
            for field in ("pixel_input_sha256", "clip"):
                broken = deepcopy(row)
                broken["image_qa"].pop(field)
                with self.assertRaisesRegex(Blocked, "image_review_required_before_packaging"):
                    qa.require_packet_rows([broken], sha, article_id=channel)
            row["image_qa"]["vision"]["models"]["blip"]["answers"][:2] = ["no", "no"]
            with self.assertRaisesRegex(Blocked, "image_review_required_before_packaging"):
                qa.require_packet_rows([row], sha, article_id=channel)

    def test_literal_evidence_and_version_binding(self):
        value = card()
        self.assertEqual(value["section_text_sha256"], digest(SOURCE))
        with self.assertRaisesRegex(Blocked, "evidence_not_in_section"):
            qa.make_card("synthetic-01", 1, digest(SOURCE), "Unrelated text", PROPOSAL)
        report = qa.judge(value, object(), "image", CLIP, vision=vision("present", "present", "absent"))
        row = {"is_cover": False, "sha256": "image", "image_qa": report}
        self.assertTrue(qa.accepted_for(row, value, is_cover=False))
        self.assertFalse(qa.accepted_for(row, {**value, "source_text_sha256": "stale"}, is_cover=False))
        self.assertFalse(qa.accepted_for(row, value, is_cover=True))
        self.assertFalse(qa.accepted_for({**row, "sha256": "changed"}, value, is_cover=False))

    def test_positive_clip_cannot_clear_missing_setting_or_contradiction(self):
        for states, reasons in (
            (("absent", "present", "absent"), ["required_place_or_element_absent"]),
            (("present", "absent", "absent"), ["required_place_or_element_absent"]),
            (("present", "present", "present"), ["contradiction_present"]),
        ):
            result = qa.judge(card(), object(), "image", CLIP, vision=vision(*states))
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reasons"], reasons)

    def test_uncertainty_and_provider_failure_are_distinct(self):
        result = qa.judge(card(), object(), "image", CLIP, vision=vision("uncertain", "present", "absent"))
        self.assertEqual(result["status"], "needs_review")
        with patch("builtins.open", side_effect=OSError):
            result = qa.judge(card(), object(), "image", CLIP, vision=lambda *_: open("offline"))
        self.assertEqual(result["status"], "provider_unavailable")

    def test_duplicate_blocks_visually_correct_image(self):
        result = qa.judge(card(), object(), "image", CLIP, duplicate=True,
                          vision=vision("present", "present", "absent"))
        self.assertEqual(result["status"], "rejected")
        self.assertIn("image_duplicate", result["reasons"])

    def test_one_model_disagreement_stays_uncertain(self):
        evidence = qa.inspect_pixels(card(), object(), ("smol", "blip"), ask=lambda model, image, questions:
                                     ["yes" if model == "smol" else "no"] * len(questions))
        self.assertEqual([x["status"] for x in evidence["findings"]], ["uncertain"] * 3)
        self.assertEqual(qa.binary_answer("Yes, but maybe not"), "uncertain")

    def test_channel_policies_require_sources_and_spoiler_evidence(self):
        result = qa.judge(card(), object(), "image", CLIP, vision=vision("present", "present", "absent"))
        film = {"qid": "Q1", "year": 2013, "original_title": "Synthetic"}
        photo = {"kind": "photo", "source_page": "https://example.org/still",
                 "film_id": "Q1", "original_title": "Synthetic", "year": 2013}
        qa.apply_channel_policy(result, "science", photo)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("scientific_photo_source_or_license_unverified", result["reasons"])

        result = qa.judge(card(), object(), "image", CLIP, vision=vision("present", "present", "absent"))
        qa.apply_channel_policy(result, "cinema", {**photo, "scene": "final plot twist"}, film=film)
        self.assertIn("cinema_spoiler_safety_unconfirmed", result["reasons"])
        qa.apply_channel_policy(result, "cinema", {**photo, "scene": "final scene"}, film=film)
        self.assertEqual(result["status"], "rejected")

    def test_explicit_cover_retains_its_scene_assignment(self):
        images = [{"file": "cover", "section": 1, "is_cover": True, "cover_score": .1},
                  {"file": "section", "section": 1, "is_cover": False, "cover_score": .9}]
        self.assertEqual(select_cover(images), "cover")
        self.assertIsNone(select_cover(images[1:]))

    def test_archive_blocks_exact_and_reencoded_duplicates(self):
        image = Image.new("RGB", (640, 480), "black")
        ImageDraw.Draw(image).rectangle((130, 130, 470, 400), fill="white")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "archive.jpg"
            image.save(original, quality=85)
            original_bytes = original.read_bytes()
            reencoded = BytesIO()
            image.save(reencoded, format="JPEG", quality=62)
            self.assertNotEqual(original_bytes, reencoded.getvalue())
            memory = SimpleNamespace(lock=RLock(), root=root, save=lambda: None)
            index = ImageIndex(memory, [original])
            scene = {"scene_card": card(), "channel": "legends", "section": 1, "is_cover": False}
            assess = vision("present", "present", "absent")
            with patch("src.editorial_images.similarity", return_value=("fixture", [.3, .25, .05], [1.0])), \
                 patch("src.editorial_scene_qa.judge", wraps=lambda *args, **kw: qa_judge(
                     *args, vision=assess, **kw)):
                for raw in (original_bytes, reencoded.getvalue()):
                    with self.assertRaises(qa.ImageQAError) as context:
                        index.save(raw, {"kind": "illustration"}, scene, "theme", root / "result.jpg")
                    self.assertIn("image_duplicate", context.exception.args[0]["reasons"])
                    self.assertFalse((root / "result.jpg").exists())

    def test_private_write_recovers_from_concurrent_branch_update(self):
        seen, pushes = [], []
        def execute(args, env, timeout=60):
            seen.append(args)
            if "push" in args:
                pushes.append(args)
                if len(pushes) == 1:
                    raise Blocked("private_review_transport_rejected")
            if "rev-parse" in args:
                return ("a" if args[-1] == "FETCH_HEAD" else "b") * 40
        push_private_main(["git", "-C", "/fixture"], {}, execute=execute)
        self.assertEqual(sum("fetch" in item for item in seen), 2)
        self.assertEqual(sum(item[-2:] == ["rebase", "a" * 40] for item in seen), 2)
        self.assertEqual(pushes[0], pushes[1])
        self.assertNotIn("--force", pushes[1])

    def test_clip_low_does_not_refute_direct_visual_confirmation(self):
        report = qa.judge(card(), object(), "test", {"passed": False},
                          vision=vision("present", "present", "absent"))
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["clip"]["decision_weight"], "advisory_low_similarity")

    def test_setting_requires_two_observable_cues(self):
        value = {**card(), "location_cues": ["a steel door", "a brick wall"]}
        for state, expected in (("present", "accepted"), ("absent", "needs_review")):
            report = qa.judge(value, object(), "test", CLIP,
                              vision=vision("absent", "present", "present", state, "absent"))
            self.assertEqual(report["status"], expected)

    def test_standalone_generator_cannot_spend_without_scene_card(self):
        with patch("src.editorial_images.klein") as provider:
            with self.assertRaisesRegex(Blocked, "scene_card_required"):
                generated({}, "subject", None, None, "task", "a", "legends")
        provider.assert_not_called()

    def test_packaging_rejects_missing_or_stale_evidence(self):
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(Blocked, "requires_current_image_audit"):
                require_audited_bundle(Path(folder))
        with self.assertRaisesRegex(Blocked, "image_review_required_before_packaging"):
            qa.require_packet_rows([{"sha256": "unverified"}], "text-sha")

    def test_klein_corrects_specific_errors_and_reuses_checkpoint(self):
        calls = []
        memory = SimpleNamespace(lock=RLock(), time_left=lambda: 500,
                                 config={"daily_calls": {"klein": 2}, "per_article_calls": {"klein": 2}},
                                 data={"calls": []})
        def send(task, article, provider, callback):
            calls.append(task)
            memory.data["calls"].append({"article": article, "provider": provider})
            return callback()
        memory.call = send
        rejected = qa.judge(card(), object(), "candidate", CLIP, vision=vision("present", "absent", "absent"))
        chooser = SimpleNamespace(memory=memory)
        def save(raw, *_):
            if raw == b"first":
                raise qa.ImageQAError(deepcopy(rejected))
            return "saved"
        chooser.save = save
        with TemporaryDirectory() as folder, patch("src.editorial_images.klein", side_effect=[
                (b"first", {}), (b"second", {})]) as generation:
            path = Path(folder) / "images" / "candidate.jpg"
            scene = {"scene_card": card()}
            self.assertEqual(generated_scene(scene, "theme", path, chooser, "task", "a", "legends", "style"), "saved")
            self.assertIn("a steel lever", generation.call_args.args[0])
            self.assertIn("Clearly show these missing items", generation.call_args.args[0])
            self.assertEqual(generated_scene(scene, "theme", path, chooser, "task", "a", "legends", "style"), "saved")
        self.assertEqual(len(calls), 2)
        self.assertEqual(qa.allocation(memory, "a"), 0)

    def test_saved_provider_bytes_survive_checkpoint_failure_and_restart(self):
        cfg = {"daily_calls": {"klein": 1}, "per_article_calls": {"klein": 1}}
        stored, recovered = [], []

        def failing_store():
            stored.append(1)
            if len(stored) == 2:
                raise StorageError("fixture_git_timeout_after_provider_response")

        with TemporaryDirectory() as folder, patch("src.editorial_images.klein",
                                                  return_value=(b"fixture image bytes", {})) as provider:
            root = Path(folder)
            deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
            memory = Memory(root, "2026-11-23", deadline, cfg, SimpleNamespace(checkpoint=failing_store))
            scene = {"scene_card": card()}
            path = memory.folder / "synthetic-01" / "images" / "candidate.jpg"
            index = SimpleNamespace(memory=memory, save=lambda raw, *_: recovered.append(raw))
            with self.assertRaisesRegex(StorageError, "timeout_after_provider_response"):
                generated_scene(scene, "subject", path, index, "task", "synthetic-01", "legends", "style")
            index.memory = Memory(root, "2026-11-23", deadline, cfg)
            generated_scene(scene, "subject", path, index, "task", "synthetic-01", "legends", "style")
            provider.assert_called_once()
            self.assertEqual(recovered, [b"fixture image bytes"])
            self.assertEqual(index.memory.data["calls"][0]["status"], "responded")

    def test_bad_freeform_description_cannot_override_targeted_answers(self):
        def assess(*_):
            return {"findings": vision("present", "present", "absent")(card(), None)["findings"],
                    "models": {"blip": {"observations": {"place": "an incorrect free-form caption"}}}}
        result = qa.judge(card(), object(), "image", CLIP, vision=assess)
        self.assertEqual(result["status"], "accepted")

    def test_klein_does_not_generate_again_after_vision_failure(self):
        memory = SimpleNamespace(lock=RLock(), time_left=lambda: 500,
                                 config={"daily_calls": {"klein": 3}, "per_article_calls": {"klein": 3}},
                                 data={"calls": []}, call=lambda task, article, provider, callback: callback())
        rejected = {"status": "provider_unavailable"}
        def save(*_):
            raise qa.ImageQAError(rejected)
        chooser = SimpleNamespace(memory=memory, save=save)
        with TemporaryDirectory() as folder, patch("src.editorial_images.klein", return_value=(b"first", {})) as generation:
            with self.assertRaisesRegex(Blocked, "provider_unavailable"):
                generated_scene({"scene_card": card()}, "theme", Path(folder) / "asset.jpg",
                                chooser, "task", "a", "legends", "style")
        self.assertEqual(generation.call_count, 1)


if __name__ == "__main__":
    unittest.main()
