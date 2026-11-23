"""Synthetic fixture tests for the fail-closed editorial vision route."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src import editorial_scene_qa as qa
from src.editorial_images import generated, generated_scene, select_cover
from src.editorial_offline_review_pack import require_audited_bundle
from src.editorial_memory import digest, json_bytes
from src.editorial_providers import Blocked

SOURCE = "A maintenance room held a steel lever and a switch."
PROPOSAL = {"scene_type": "object", "location": "a maintenance room", "time_of_day": "",
            "characters": [], "must_have": ["a steel lever"], "must_not_have": ["a staircase"],
            "optional_details": [], "visual_description": "A steel lever in a maintenance room.",
            "evidence_spans": {"a maintenance room": "maintenance room", "a steel lever": "a steel lever"}}
CLIP = {"passed": True, "topic": .24}


def card():
    return qa.make_card("synthetic-01", 1, digest(SOURCE), SOURCE, PROPOSAL)


def vision(*states):
    return lambda c, im: {"findings": [
        {"role": role, "element": item, "status": state}
        for (role, item), state in zip(qa.questions_for(c)[0], states, strict=True)]}


class SceneTests(unittest.TestCase):
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
