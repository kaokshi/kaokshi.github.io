import base64
import json
import unittest
from src.zen_analytics_private_archive import archive, LATEST, REPO

def report(stamp="2026-11-26T10:00:00Z"):
    return json.dumps({"schema":"zen-metrika/v1","collected_at":stamp,"channels":{
        c:{"channel":c,"source":"yandex_metrika","status":"binding_unverified",
           "binding_verified":False,"usable_for_editorial_advice":False,
           "periods":{"7":{},"30":{}}} for c in ("science","cinema","legends")}}).encode()

class Fake:
    def __init__(self, private=True):
        self.private=private
        self.files={}
        self.writes=0
    def __call__(self,path,body=None):
        if not path:
            return 200,{"full_name":REPO,"private":self.private,"visibility":"private" if self.private else "public","default_branch":"main"}
        p=path.split("?")[0].removeprefix("/contents/")
        if body is None:
            if p not in self.files: return 404,None
            return 200,{"type":"file","encoding":"base64","content":base64.b64encode(self.files[p]).decode(),"sha":"fake"}
        self.writes+=1
        if p in self.files and body.get("sha")!="fake": return 409,None
        self.files[p]=base64.b64decode(body["content"])
        return 201,{}

class ArchiveTest(unittest.TestCase):
    def test_replay_and_two_windows(self):
        f=Fake()
        self.assertEqual(archive(f,report(),update_latest=True),{"archived":True,"latest":"updated"})
        self.assertEqual(archive(f,report(),update_latest=True),{"archived":False,"latest":"replay"})
        self.assertEqual(f.writes,2)
        archive(f,report("2026-11-26T11:00:00Z"),update_latest=True)
        self.assertEqual(len(f.files),3)
        self.assertEqual(archive(f,report(),update_latest=True)["latest"],"newer_exists")
        self.assertEqual(f.files[LATEST],report("2026-11-26T11:00:00Z"))
    def test_private_required(self):
        f=Fake(False)
        with self.assertRaises(ValueError): archive(f,report(),update_latest=True)
        self.assertEqual(f.writes,0)
    def test_no_promotion_or_incomplete(self):
        f=Fake()
        r=json.loads(report())
        r["channels"]["cinema"]["usable_for_editorial_advice"]=True
        with self.assertRaises(ValueError): archive(f,json.dumps(r).encode())
        self.assertEqual(f.writes,0)
    def test_immutable_corruption(self):
        f=Fake()
        archive(f,report())
        path=next(iter(f.files))
        f.files[path]=b"bad"
        with self.assertRaises(ValueError): archive(f,report(),update_latest=True)
        self.assertNotIn(LATEST,f.files)

    def test_seed_discovery(self):
        from src.zen_analytics_private_archive import verify_seed_and_archive_latest
        from test_zen_studio_seed import seed, envelope
        raw=envelope(seed())
        digest=json.loads(raw)["sha256"]
        path="zen-analytics/studio/owner-seeds/"+digest+".json"
        class Listing(Fake):
            def __call__(self,p,body=None):
                if p=="/contents/zen-analytics/studio/owner-seeds?ref=main":
                    return 200,[{"type":"file","path":path}]
                return super().__call__(p,body)
        f=Listing()
        f.files[path]=raw
        f.files[LATEST]=report()
        self.assertEqual(verify_seed_and_archive_latest(f,"discover"),
            {"seed_channels":3,"metrika_archive_created":True})
        self.assertFalse(verify_seed_and_archive_latest(f,path)["metrika_archive_created"])
