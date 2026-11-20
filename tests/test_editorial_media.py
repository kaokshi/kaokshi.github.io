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
