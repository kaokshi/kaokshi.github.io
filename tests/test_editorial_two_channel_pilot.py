"""Safe, file-based tests for the two-channel real-executor pilot runner."""
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import editorial_two_channel_pilot as pilot
from editorial_agent_v1 import article_digest
from editorial_writer import topic_key


class PilotTests(unittest.TestCase):
    def test_external_endpoint_rejected_without_network(self):
        self.assertEqual(pilot.endpoint_ready("https://example.org:443")["status"],
                         "invalid_endpoint")
        self.assertEqual(pilot.endpoint_ready("http://192.168.0.9:8093")["status"],
                         "invalid_endpoint")

    def test_missing_executor_is_not_claimed_ready(self):
        self.assertEqual(pilot.endpoint_ready("http://127.0.0.1:1")["status"],"not_ready")

    def test_case_snapshot_uses_actual_sha_and_quality_checks(self):
        topic="Сторож старой станции"
        with TemporaryDirectory() as tmp:
            base=Path(tmp)
            key=sha256(topic_key(topic).encode()).hexdigest()[:16]
            folder=base/f"legends-draft-{key}"
            folder.mkdir()
            art={"slug":f"legends-draft-{key}","channel":"legends","status":"draft",
                 "body_html":"<p>"+("Сторож услышал шаги у двери. "*150)+"</p>",
                 "publication_authorized":False}
            digest=article_digest(art)
            case={"decision":"owner_review_pending",
                  "versions":[{"stage":"original","article":art,"text_sha256":digest,
                  "report":{"text_sha256":digest,"automated_checks_completed":True,
                            "manual_checks_pending":["image_rights"]}}]}
            (folder/"case.json").write_text(json.dumps(case,ensure_ascii=False),encoding="utf-8")
            row=pilot.dossier_summary(base,"legends",topic)
            self.assertEqual(row["draft_sha256"],digest)
            self.assertTrue(row["length_ok"])
            self.assertTrue(row["article_ready_for_owner"])
            self.assertEqual(row["manual_checks_pending"],["image_rights"])
            self.assertFalse(row["publication_authorized"])
            case["versions"][0]["report"]["text_sha256"]="0"*64
            (folder/"case.json").write_text(json.dumps(case,ensure_ascii=False),encoding="utf-8")
            row=pilot.dossier_summary(base,"legends",topic)
            self.assertFalse(row["article_ready_for_owner"])

    def test_run_one_passes_topics_and_private_workspace_only(self):
        with TemporaryDirectory() as tmp:
            calls=[]
            def runner(cmd,**kw):
                calls.append((cmd,kw))
                class Result:
                    returncode=2
                    stdout='{"status":"provider_error"}'
                    stderr=""
                return Result()
            row=pilot.run_one(Path(tmp),"science","Почему море светится",
                              research_query="bioluminescent plankton",runner=runner)
            self.assertEqual(row["status"],"not_created")
            self.assertEqual(row["execution"]["exit_code"],2)
            self.assertIn("--research-query",calls[0][0])
            self.assertIn("--europe-pmc",calls[0][0])
            self.assertNotIn("--article",calls[0][0])
            self.assertEqual(calls[0][1]["timeout"],1800)

if __name__=="__main__":
    unittest.main()
