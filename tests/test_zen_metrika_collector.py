import unittest
from datetime import date
from src.zen_metrika_collector import channel_report, CHANNELS

class CollectorTest(unittest.TestCase):
    def mock(self, foreign=False, cross=False, sampled=False):
        def get(token,path,params=None):
            if path.startswith("/management"):
                cid=int(path.rsplit("/",1)[-1])
                if cid==CHANNELS["cinema"][0]:
                    return 200,{"counter":{"id":cid,"site":CHANNELS["cinema"][1]}}
                return (200,{}) if cross else (403,None)
            n=len(params["metrics"].split(","))
            rows=[]
            if params.get("dimensions")=="ym:pv:URL" and foreign:
                rows=[{"dimensions":[{"name":"https://dzen.ru/sciencebox?private=1"}],"metrics":[1]}]
            return 200,{"sampled":sampled,"totals":[0]*n,"data":rows,"total_rows":len(rows)}
        return get
    def report(self, **kwargs):
        return channel_report("cinema","test",self.mock(**kwargs),date(2026,11,25))
    def test_no_false_binding_confirmation(self):
        r=self.report()
        self.assertEqual(r["status"],"binding_unverified")
        self.assertFalse(r["usable_for_editorial_advice"])
        self.assertEqual(r["periods"]["7"]["end"],"2026-11-24")
    def test_foreign_page(self):
        r=self.report(foreign=True)
        self.assertEqual(r["status"],"mixed_channel_traffic")
        self.assertNotIn("private=1",str(r))
    def test_cross_access(self):
        self.assertEqual(self.report(cross=True)["reason"],"credential_isolation_unverified")
    def test_sampled(self):
        self.assertEqual(self.report(sampled=True)["reason"],"report_unavailable_or_sampled")
    def test_missing_token(self):
        self.assertEqual(channel_report("cinema","")["reason"],"token_missing")
if __name__=="__main__":
    unittest.main()
