"""Source-bound diagram replacements for previously rejected visual concepts."""
from io import BytesIO
import unittest
from unittest.mock import patch

from src.editorial_routerai import UnsafeToTest
from src.editorial_routerai_diagrams import (
    CAVE_SOURCE, WATER_STORAGE, WATER_STORAGE_SOURCE, cave_diagram_replacement, render_diagram,
)


class DiagramTest(unittest.TestCase):
    def test_known_cave_concepts_have_distinct_source_bound_templates(self):
        from PIL import Image

        records = [{"url": CAVE_SOURCE, "status": "accessible",
                    "text": "water table zone of saturation zone of aeration stalactites stalagmites columns"}]
        cases = {
            "Cross-section diagram of cave hydrology zones": "cave-groundwater-zones",
            "Conceptual time-lapse of dripstone growth": "cave-dripstone-growth",
            "Speleothem morphology classification chart": "cave-speleothem-types",
        }
        outputs = []
        for subject, wanted in cases.items():
            template = cave_diagram_replacement({"template": "cave-formations", "subject": subject})
            self.assertEqual(template, wanted)
            data, meta = render_diagram(template, records)
            self.assertEqual(meta["source"], CAVE_SOURCE)
            with Image.open(BytesIO(data)) as img:
                self.assertEqual(img.size, (1200, 800))
            outputs.append(data)
        self.assertEqual(len(set(outputs)), len(cases))
        self.assertIsNone(cave_diagram_replacement({"template": "cave-formations", "subject": "Different topic"}))

    def test_groundwater_scale_requires_the_published_values(self):
        self.assertEqual(cave_diagram_replacement({"template": "cave-formations",
                                                   "subject": "Isometric visualization of an aquifer scale"}),
                         "groundwater-scale")
        with patch("src.editorial_routerai_diagrams.water_storage_evidence", return_value=[]):
            with self.assertRaisesRegex(UnsafeToTest, "diagram_template_or_evidence_unavailable"):
                render_diagram("groundwater-scale", [])
        records = [{"url": WATER_STORAGE_SOURCE, "status": "accessible",
                    "text": ", ".join(f"{v:,} cubic kilometers" for _, v in WATER_STORAGE)}]
        with patch("src.editorial_routerai_diagrams.water_storage_evidence", return_value=records):
            _, meta = render_diagram("groundwater-scale", [])
        self.assertEqual(meta["source"], WATER_STORAGE_SOURCE)
