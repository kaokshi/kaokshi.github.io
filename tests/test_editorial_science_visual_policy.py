"""Contract tests: science mixed-media preference must not enable unverified image generation."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScienceVisualPolicyTests(unittest.TestCase):
    def test_approved_mixed_style_has_safety_gates(self):
        p = json.loads((ROOT / "config/editorial_science_visual_policy.json").read_text(encoding="utf-8"))
        self.assertEqual(p["schema"], "zen-science-mixed-visual-policy-v1")
        self.assertEqual(p["strategy"], "mixed")
        self.assertTrue(p["media_types"]["documentary_photo"]["enabled"])
        for media in ("explanatory_2d", "explanatory_3d", "animation"):
            self.assertTrue(p["media_types"][media]["enabled_for_planning"])
            self.assertFalse(p["media_types"][media]["automatic_production"])
            self.assertIn("rights_verified", p["media_types"][media]["requires"])
        self.assertFalse(p["production_guardrails"]["publication_enabled"])
        self.assertTrue(p["production_guardrails"]["keep_existing_commons_photo_pipeline"])
        self.assertIn("zen_rss_support_verified", p["media_types"]["animation"]["requires"])


if __name__ == "__main__":
    unittest.main()
