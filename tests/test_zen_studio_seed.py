import base64
import gzip
import hashlib
import unittest
from src.zen_dzen_xlsx import import_report
from src.zen_studio_seed import canonical,decode_seed
from test_zen_dzen_xlsx import fixture

def envelope(seed):
    raw=canonical(seed)
    return canonical({"schema":"zen-studio-seed-envelope/v1","encoding":"gzip+base64",
        "sha256":hashlib.sha256(raw).hexdigest(),"usable_for_editorial_advice":False,
        "payload":base64.b64encode(gzip.compress(raw)).decode()})

def seed():
    reports=[]
    for c,owner in (("science","a"),("cinema","c"),("legends","d")):
        # The synthetic workbook builder uses the same owner; change the row
        # identity before recomputing the semantic hash (not real evidence).
        r=import_report(fixture(c),c)
        for s in ("all_platforms","dzen_platform"):
            r[s]["channel_id"]=owner*24
            for p in r[s]["publications"]:
                p["url"]=p["url"].replace("a"*24,owner*24)
        body={k:v for k,v in r.items() if k not in ("snapshot_id","file_sha256")}
        r["snapshot_id"]=hashlib.sha256(canonical(body)).hexdigest()
        reports.append(r)
    return {"schema":"zen-studio-owner-seed/v1","source":"owner_uploaded_xlsx",
            "usable_for_editorial_advice":False,"reports":reports}

class SeedTest(unittest.TestCase):
    def test_valid(self):
        s=seed()
        self.assertEqual(decode_seed(envelope(s)),s)
    def test_tamper(self):
        s=seed()
        s["reports"][0]["all_platforms"]["totals"]["opens"]=99
        with self.assertRaises(ValueError): decode_seed(envelope(s))
    def test_no_promotion(self):
        s=seed()
        s["usable_for_editorial_advice"]=True
        with self.assertRaises(ValueError): decode_seed(envelope(s))
    def test_duplicate_identity(self):
        s=seed()
        s["reports"][1]=s["reports"][0]
        with self.assertRaises(ValueError): decode_seed(envelope(s))
