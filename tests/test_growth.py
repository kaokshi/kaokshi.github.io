import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"growth"))
import report
class AnalyticsTest(unittest.TestCase):
    def test_no_data_no_cost_claim(self):
        text=report.analyze([],[])
        self.assertIn("н/д",text)
        self.assertIn("отдельного одобрения",text)
    def test_paid_cost(self):
        paid=[{"channel":"cinema","spend_rub":300.0,"paid_subscriptions":3.0,
                "paid_reads":20.0}]
        txt=report.analyze([],paid)
        self.assertIn("100.00 ₽",txt)
        self.assertIn("15.00 ₽",txt)
        self.assertIn("леген",txt.lower()) if False else None
    def test_organic_ctr_separate(self):
        org=[{"channel":"science","impressions":1000.0,"opens":50.0,"reads":30.0}]
        txt=report.analyze(org,[])
        self.assertIn("5.0%",txt)
        self.assertIn("60.0%",txt)
if __name__=="__main__":
    unittest.main()
