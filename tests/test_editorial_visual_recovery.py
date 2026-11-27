"""Generalized diagram recovery for unrelated themes (synthetic fixtures only)."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from src.editorial_routerai import UnsafeToTest
from src.editorial_routerai_diagrams import (
    CAVE_SOURCE, TEMPLATES, evidence_available, evidence_terms, scene_diagram_replacement,
    template_contract,
)
from src import editorial_routerai_two_articles as two

RECORDS = [{"url": CAVE_SOURCE, "status": "accessible",
            "text": "stalactites stalagmites columns water table zone of saturation"}]


class RegistryTest(unittest.TestCase):
    def test_unrelated_theme_is_matched_from_catalogue_data(self):
        scene = {"template": "cave-formations",
                 "subject": "Cross-section of a karst system: morphology and morphology detail"}
        self.assertEqual(scene_diagram_replacement(scene), "cave-speleothem-types")
        self.assertEqual(scene_diagram_replacement(scene, RECORDS), "cave-speleothem-types")
        # Deterministic: repeated planning cannot drift between attempts or processes.
        self.assertEqual(scene_diagram_replacement(scene), scene_diagram_replacement(dict(scene)))

    def test_replacement_is_grounded_in_the_article_sources(self):
        scene = {"template": "cave-formations", "subject": "Speleothem morphology classification chart"}
        self.assertIsNone(scene_diagram_replacement(scene, []))
        self.assertIsNone(scene_diagram_replacement(scene, [{"url": CAVE_SOURCE, "status": "unreachable",
                                                             "text": "stalactites stalagmites columns"}]))
        self.assertIsNone(scene_diagram_replacement(scene, [{"url": "https://example.org", "status": "accessible",
                                                             "text": "stalactites stalagmites columns"}]))

    def test_current_template_is_never_returned_and_unknown_topics_stay_unmatched(self):
        scene = {"template": "asteroid-orbit-risk", "subject": "Risk probability overlay"}
        self.assertNotEqual(scene_diagram_replacement(scene), "asteroid-orbit-risk")
        self.assertIsNone(scene_diagram_replacement({"template": "cave-formations",
                                                     "subject": "An unrelated new subject"}))

    def test_contract_exposes_every_rendered_template_only(self):
        self.assertEqual(set(two.template_contract.__module__ and TEMPLATES), set(TEMPLATES))
        self.assertIsNone(template_contract("not-a-template"))
        self.assertTrue(all(evidence_terms(row) for row in TEMPLATES.values()
                            if row.id != "groundwater-scale"))

    def test_plan_rejects_an_unrenderable_schematic_before_paid_stages(self):
        rows = [{"subject": "Cover", "brief": "Cover brief", "anchor": "Intro",
                 "kind": "photo", "query": "cave interior photo"},
                {"subject": "Schematic", "brief": "Schematic brief", "anchor": "Section text",
                 "kind": "diagram", "template": "not-a-template"}]
        with self.assertRaisesRegex(UnsafeToTest, "image_plan_template_invalid"):
            two.plan_images({"style": "Style", "images": rows},
                            [{"paragraphs": ["Section text"]}], science=True, licensed_photos=True)


class DiagramIllustrationTest(unittest.TestCase):
    """The rejected-schema path must repair itself without a paid image call."""

    def run_illustration(self):
        stages, generated = [], []
        rendered = []

        def render(template, records):
            rendered.append(template)
            if template == "cave-formations":
                raise UnsafeToTest("diagram_template_or_evidence_unavailable")
            return b"png", {"kind": "diagram", "source": CAVE_SOURCE, "template": template,
                            "rights_status": "original_programmatic_graphic"}

        class Spend:
            image_price = Decimal("0")
            def generate(self, *args, **kwargs):
                generated.append(args)
            def chat(self, channel, stage, role, prompt, cap, pictures=(), reserve=0):
                stages.append(stage)
                return '{"passed": true, "issues": [], "evidence": "Visible labelled schematic"}'

        class Index:
            def save(self, raw, metadata, scene, title, target, require_scene_score=False):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                return {"file": target.name, "sha256": "b" * 64, "original_sha256": "b" * 64,
                        "perceptual_hash": "1" * 16, "image_embedding": [0.1],
                        "scene_similarity": 0.5, "topic_similarity": 0.5,
                        "scene_score_flags": [], "cover_score": 1}

        saved = []
        scene = {"kind": "diagram", "template": "cave-formations", "section": 1,
                 "subject": "Speleothem morphology classification chart", "brief": "Conceptual schematic"}
        article = {"intro": ["Вступление."],
                   "sections": [{"heading": "Раздел", "paragraphs": ["Текст."]}]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(two, "render_diagram", side_effect=render), \
                patch.object(two, "decode_candidate", return_value=("image", None)), \
                patch.object(two, "thumbnail", return_value="preview"), \
                patch.object(two, "describe_pixels", return_value="labelled schematic"), \
                patch.object(two, "compare_pixels", return_value=([0.5, 0.1], [0.0])):
            rows, _, failed = two.illustrate(Spend(), lambda name, data: saved.append(name), {},
                                             "science", article, "Заголовок", "стиль", [scene],
                                             Index(), Path(directory), 0, RECORDS)
        return rows, failed, stages, generated, rendered, saved

    def test_rejected_schematic_is_recovered_and_persisted(self):
        rows, failed, stages, generated, rendered, saved = self.run_illustration()
        self.assertEqual(failed, [])
        self.assertEqual([row["file"] for row in rows], ["image-00.jpg"])
        self.assertEqual(rendered, ["cave-formations", "cave-speleothem-types"])
        self.assertEqual(generated, [])
        self.assertIn("science/attempts/image-00-attempt-1-qa.json", saved)
        self.assertIn("science/attempts/image-00-attempt-2-qa.json", saved)

    def test_locally_rejected_photo_does_not_call_paid_qa(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        spend = SimpleNamespace(image_price=Decimal(0), chat=Mock())
        checkpoints = SimpleNamespace(image=lambda *_: None, read_attempt_asset=lambda *_: None,
                                      read=lambda *_: None)
        records = []
        scene = {"kind": "photo", "subject": "Synthetic scene", "brief": "Synthetic brief"}
        article = {"intro": ["Synthetic fixture."], "sections": []}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(two, "science_asset", return_value=(b"corrupt", {})), \
                patch.object(two, "decode_candidate", side_effect=ValueError("corrupt")):
            rows, _, failed = two.illustrate(spend, lambda name, data: records.append((name, data)),
                                             {}, "legends", article, "Fixture", "Fixture", [scene],
                                             None, Path(directory), 0, [], checkpoints=checkpoints)
        self.assertEqual((rows, failed), ([], [0]))
        spend.chat.assert_not_called()
        qa_rows = [data for name, data in records if name.endswith("-qa.json")]
        self.assertEqual(len(qa_rows), 3)
        self.assertTrue(all(b"image_local_check_failed" in data for data in qa_rows))

    def test_replay_uses_identical_stage_keys_so_no_extra_charge_is_created(self):
        first = self.run_illustration()
        second = self.run_illustration()
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[4], second[4])


if __name__ == "__main__":
    unittest.main()
