"""Regression tests of editorial triage with mock authenticated check receipts.

Synthetic checks test routing logic; they are NOT real successful AI runs.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_auto_route as source
import editorial_stage_gate as gate


class GateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.p1=patch.object(source,"ROOT",self.root)
        self.p2=patch.object(gate,"ROOT",self.root)
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)
        images=[]
        for i in range(7):
            name=f"content/assets/test-{i}.png"
            file=self.root/name
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_bytes(b"fake test bytes"+str(i).encode())
            images.append(name)
        self.article={
            "slug":"legends-stage-test","channel":"legends","status":"draft",
            "body_html":"<p>"+"Северный ветер у старых ворот. "*150+"</p>",
            "image_files":images,"fiction":True,
            "rights_basis":"Synthetic samples, not genuine license review",
            "image_sources":[{"file":x} for x in images],
            "release_hold":True
        }
        self.refresh_receipts()

    def refresh_receipts(self):
        digest=source.digest_article(self.article)
        self.ai={
            "article_sha256":digest,"article_slug":self.article["slug"],
            "all_checks_completed":True,
            "whole_document_index_percent":99.5,
            "checks":{
                "rubert_v4":{"status":"completed","data":{"prediction_native":"human","score_percent":99}},
                "modernbert":{"status":"completed","data":{"probability":.01}},
                "lightgbm":{"status":"completed","data":{"probability":.02}},
                "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":.99}}
            }
        }
        self.evidence={
            "status":"completed","report_ref":"https://github.com/example/actions/runs/1",
            "article_sha256":digest,
            "fiction_originality_reviewed":True,
            "checks":{name:{"status":"completed"} for name in gate.REQUIRED_STEPS}
        }

    def decide(self):
        return gate.stage_decision(self.article,self.evidence,self.ai)

    def test_ready_means_review_preview_not_rss_or_pages(self):
        d=self.decide()
        self.assertEqual(d["disposition"],"ready_for_next_stage")
        self.assertEqual(d["next_stage"],"owner_approval_preview")
        self.assertFalse(d["publication_authorized"])
        self.assertFalse(d["rss_modified"])

    def test_high_ai_score_alone_does_not_trigger_rewrite(self):
        d=self.decide()
        self.assertFalse(d["ai_rewrite_threshold_applied"])
        self.assertEqual(d["revision_actions"],[])

    def test_clear_content_defect_creates_actionable_revision(self):
        self.article["body_html"]="<p>Мало текста.</p>"
        d=self.decide()
        self.assertEqual(d["disposition"],"needs_revision")
        self.assertTrue(any("3000" in x for x in d["revision_actions"]))

    def test_failed_check_returns_specific_findings(self):
        self.evidence["checks"]["grammar"]={"status":"failed","findings":["Согласовать падеж в третьем абзаце."]}
        d=self.decide()
        self.assertEqual(d["disposition"],"needs_revision")
        self.assertIn("grammar: Согласовать падеж в третьем абзаце.",d["revision_actions"])

    def test_failed_check_without_findings_cannot_be_marked_passed(self):
        self.evidence["checks"]["grammar"]={"status":"failed","findings":[]}
        d=self.decide()
        self.assertEqual(d["disposition"],"awaiting_evidence")

    def test_stale_ai_report_waits_for_fresh_run(self):
        self.ai["article_sha256"]="not-current"
        d=self.decide()
        self.assertEqual(d["disposition"],"awaiting_evidence")
        self.assertIn("ai_four_github_models_missing_or_stale",d["awaiting_checks"])

    def test_stale_editorial_receipt_waits(self):
        self.evidence["article_sha256"]="not-current"
        self.assertEqual(self.decide()["disposition"],"awaiting_evidence")

    def test_missing_model_never_creates_fake_pass(self):
        self.ai["checks"]["modernbert"]["status"]="error"
        self.assertEqual(self.decide()["disposition"],"awaiting_evidence")

    def test_conflicting_model_families_require_editorial_rework(self):
        self.ai["checks"]["modernbert"]["data"]["probability"]=.94
        self.ai["checks"]["lightgbm"]["data"]["probability"]=.9
        d=self.decide()
        self.assertEqual(d["disposition"],"needs_revision")
        self.assertTrue(any("Спорные результаты" in x for x in d["revision_actions"]))
        self.assertFalse(d["publication_authorized"])

    def test_conflict_within_aiw_family_goes_to_rework(self):
        self.ai["checks"]["modernbert"]["data"]["probability"]=.96
        self.ai["checks"]["lightgbm"]["data"]["probability"]=.03
        d=self.decide()
        self.assertEqual(d["disposition"],"needs_revision")
        self.assertTrue(any("aiw_family_internal_disagreement" in x for x in d["revision_actions"]))

    def test_missing_model_output_is_not_disagreement_or_pass(self):
        del self.ai["checks"]["modernbert"]["data"]["probability"]
        d=self.decide()
        self.assertEqual(d["disposition"],"awaiting_evidence")
        self.assertIn("ai_agreement_analysis_incomplete",d["awaiting_checks"])

    def test_paused_cinema_not_deleted(self):
        self.article["channel"]="cinema"
        self.refresh_receipts()
        self.assertEqual(self.decide()["disposition"],"paused")

    def test_science_requires_covered_facts(self):
        self.article["channel"]="science"
        self.article["claims"]=[{"id":"c1","source_urls":["https://example.org/science"]}]
        self.refresh_receipts()
        d=self.decide()
        self.assertEqual(d["disposition"],"needs_editor_review")
        self.evidence["verified_claim_ids"]=["c1"]
        self.assertEqual(self.decide()["disposition"],"ready_for_next_stage")

    def test_changed_image_bytes_invalidate_reports(self):
        (self.root/self.article["image_files"][0]).write_bytes(b"changed")
        self.assertEqual(self.decide()["disposition"],"awaiting_evidence")

    def test_license_review_not_silently_passed(self):
        self.article["rights_basis"]=""
        self.refresh_receipts()
        self.assertEqual(self.decide()["disposition"],"needs_editor_review")

if __name__=="__main__":
    unittest.main()
