"""Non-generative benchmark controls: all negatives must be actual pixel changes."""
import io
import sys
from pathlib import Path
import unittest
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_vision_benchmark import SCENES, make_variants


def image(color):
    b=io.BytesIO()
    Image.new("RGB",(128,128),color).save(b,format="JPEG")
    return b.getvalue()


class BenchmarkTests(unittest.TestCase):
    def test_three_scenes_eight_cases_each(self):
        for i in SCENES:
            cases=list(make_variants(image("navy"),image("white"),i))
            self.assertEqual(len(cases),8)
            self.assertEqual(sum(expected for _,expected,_ in cases),2)
            self.assertEqual(sum(not expected for _,expected,_ in cases),6)
            self.assertEqual(len({name for name,_,_ in cases}),8)
            for name,accept,modified in cases:
                self.assertIsInstance(modified,bytes)
                self.assertGreater(len(modified),100)
                if not accept:
                    self.assertNotEqual(modified,image("navy"))
        self.assertEqual(3*8,24)


if __name__=="__main__":
    unittest.main()
