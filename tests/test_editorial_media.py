import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_cover import eligible_cover, CRITERIA
from editorial_quality import image_hash_audit


class MediaTests(unittest.TestCase):
    def test_reused_filename_in_another_channel(self):
        from PIL import Image
        import editorial_quality
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'shared.jpg'
            Image.new('RGB', (70, 60), 'red').save(root / name)
            path = root / 'cinema.json'
            path.write_text(json.dumps(dict(slug='cinema-one', image_files=[name])))
            with patch.object(editorial_quality, 'ROOT', root):
                result = image_hash_audit([path], candidate=dict(slug='science-one', image_files=[name]))
            rows = result.get('duplicates', result.get('sha256_duplicates'))
            self.assertTrue(any(r['method']=='sha256' and r['articles']==['cinema-one','science-one'] for r in rows))
            if result['near_duplicate_status']=='completed':
                self.assertTrue(any(r['method']=='perceptual_hash' and r['distance']==0 for r in rows))

    def test_unexplained_visual_ratings_cannot_select_cover(self):
        with self.assertRaises(ValueError):
            eligible_cover(dict.fromkeys(CRITERIA, 5) | {'reason':'none','defect':''}, (1200,900))

    def test_verified_scene_is_reused_after_an_interruption(self):
        from hashlib import sha256
        from unittest.mock import patch
        from editorial_media import produce
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = [{"id": str(i)} for i in range(7)]
            calls = []
            def provider(channel, row, root, endpoint):
                calls.append(row["id"])
                if len(calls) == 2:
                    raise OSError("offline")
                data = row["id"].encode()
                name = "content/assets/" + row["id"]
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return dict(row, file=name, sha256=sha256(data).hexdigest())
            with patch("editorial_media.produce_one", provider):
                checkpoint = root / "progress.json"
                with self.assertRaises(OSError):
                    produce("legends", plan, root, "local", checkpoint=checkpoint)
                self.assertEqual(len(produce("legends", plan, root, "local", checkpoint=checkpoint)), 7)
                self.assertEqual(calls.count("0"), 1)

    def test_disqualified_noncover_blocks_the_set(self):
        from editorial_cover import unsuitable
        self.assertTrue(unsuitable({"assessment": {"topic": 0, "genre": 5, "defect": ""}}))
        self.assertTrue(unsuitable({"assessment": {"topic": 5, "genre": 5, "defect": "misleading"}}))
