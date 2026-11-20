"""Offline tests of private export packets and GitHub-mode Python provider launch."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_owner_packet import make_packet
from editorial_writer import command_provider, topic_key
from editorial_agent_v1 import article_digest


class OwnerPacketTests(unittest.TestCase):
    def valid_case(self, root):
        folder=root/"legends-draft-1234567890abcdef"
        folder.mkdir()
        a={"slug":folder.name,"channel":"legends","status":"draft",
           "publication_authorized":False,"body_html":"<p>Искренняя история.</p>"}
        sha=article_digest(a)
        case={"decision":"assistant_handoff_pending","publication_authorized":False,
              "rss_delivery_authorized":False,
              "handoff":{"recipient":"chatgpt","transport_status":"not_connected",
                         "text_sha256":sha,"reasons":["unresolved_issue"]},
              "versions":[{"article":a,"text_sha256":sha,"stage":"original",
                           "report":{"manual_checks_pending":["image_rights"]}}]}
        reports={"decision":"assistant_handoff_pending","draft_sha256":sha}
        (folder/"case.json").write_text(json.dumps(case,ensure_ascii=False),encoding="utf-8")
        (folder/"reports.json").write_text(json.dumps(reports,ensure_ascii=False),encoding="utf-8")
        (folder/"draft.md").write_text("# История\n\nИскренняя история.",encoding="utf-8")
        return folder

    def test_packet_has_exact_file_hashes_but_cannot_claim_delivery(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            folder=self.valid_case(root)
            archive=root/"review.zip"
            result=make_packet(folder,archive)
            self.assertTrue(archive.is_file())
            self.assertFalse(result["publication_authorized"])
            self.assertEqual(result["transport_status"],"not_connected")
            self.assertEqual(result["assistant_handoff"]["recipient"],"chatgpt")
            with ZipFile(archive) as z:
                self.assertIn("case.json",z.namelist())
                self.assertIn("draft.md",z.namelist())
                for name, meta in result["files"].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),meta["sha256"])

    def test_stale_digest_refused(self):
        with TemporaryDirectory() as td:
            root=Path(td)
            folder=self.valid_case(root)
            p=folder/"reports.json"
            data=json.loads(p.read_text())
            data["draft_sha256"]="0"*64
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,"digest"):
                make_packet(folder,root/"review.zip")

    def test_provider_python_does_not_require_execute_bit(self):
        with TemporaryDirectory() as td:
            script=Path(td)/"mock_provider.py"
            script.write_text("#!/usr/bin/env python3\nimport sys, json\n"
                              "request=json.load(sys.stdin)\n"
                              "print(json.dumps({'seen':request['task']}))\n")
            script.chmod(0o600)
            self.assertEqual(command_provider(script)("diagnose",{}),{"seen":"diagnose"})


if __name__=="__main__":
    unittest.main()
