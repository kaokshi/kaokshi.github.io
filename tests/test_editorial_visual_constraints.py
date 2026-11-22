"""Strict semantic guards and reproducible best-candidate regression cases."""
import math
import unittest
from src.editorial_providers import Blocked
from src.editorial_visual_constraints import validate_constraints,evaluate,best_candidate,count_prompt

SCENE={
    "brief":"A metro dispatcher room with four clocks",
    "subject":"four different wall clocks",
    "must_include":["old control panel","four clocks"],
    "must_avoid":["empty tunnel"],
    "count_requirements":[{"object":"wall clock","mode":"exact","value":4}],
}
def assessment(margin=2, evidence=None):
    d={"siglip_relative_margin":margin,"florence_caption":"A control room with clocks"}
    if evidence is not None:d["detail_evidence"]=evidence
    return d
def evidence(n,complete=True):
    return {"verified_independently":complete,
            "present":["old control panel","four clocks"],
            "absent":["empty tunnel"],
            "counts":{"wall clock":n}}

class SemanticGuardTests(unittest.TestCase):
    def test_no_independent_evidence_is_review_only(self):
        result=evaluate(SCENE, assessment())
        self.assertFalse(result["passed"])
        self.assertIn("mandatory_details_not_independently_verified",result["issues"])

    def test_correct_count_can_pass_when_independently_verified(self):
        self.assertTrue(evaluate(SCENE, assessment(evidence=evidence(4)))["passed"])

    def test_two_clocks_rejected_even_if_siglip_score_better(self):
        result=evaluate(SCENE, assessment(margin=100,evidence=evidence(2)))
        self.assertFalse(result["passed"])
        self.assertIn("count_mismatch_or_unverified:wall clock",result["issues"])

    def test_forbidden_or_missing_objects_rejected(self):
        d=evidence(4)
        d["absent"]=[]
        d["present"]=["old control panel"]
        r=evaluate(SCENE,assessment(evidence=d))
        self.assertFalse(r["passed"])
        self.assertEqual(len([x for x in r["issues"] if "unverified" in x]),2)

    def test_invalid_requirement_rejected(self):
        with self.assertRaises(ValueError):
            validate_constraints({**SCENE,"count_requirements":[{"object":"wall clock","mode":"exact","value":"4"}]})
        with self.assertRaises(ValueError):
            validate_constraints({**SCENE,"count_requirements":[{"object":"wall clock","mode":"exact","value":0}]})

    def test_best_verified_beats_last_with_high_semantic_score(self):
        correct=evaluate(SCENE,assessment(margin=2,evidence=evidence(4)))
        wrong=evaluate(SCENE,assessment(margin=20,evidence=evidence(2)))
        rows=[{"review":correct,"index":1},{"review":wrong,"index":2}]
        self.assertEqual(best_candidate(rows)["index"],1)

    def test_fails_closed_with_nan_score(self):
        with self.assertRaises(Blocked):
            evaluate(SCENE,assessment(margin=float("nan"),evidence=evidence(4)))

    def test_exact_count_included_in_regeneration_prompt(self):
        self.assertIn("Exactly 4 separate",count_prompt(SCENE))

    def test_unconstrained_scene_can_pass_relative_semantic_check(self):
        plain={"brief":"underground tunnel","subject":"tunnel"}
        self.assertTrue(evaluate(plain,assessment(margin=1))["passed"])
        self.assertFalse(evaluate(plain,assessment(margin=-1))["passed"])

if __name__=="__main__":
    unittest.main()
