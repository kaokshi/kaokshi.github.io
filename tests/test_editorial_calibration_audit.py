"""Synthetic-only tests for fail-closed calibration; no real-world validation."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_calibration_audit import audit, wilson  # noqa: E402


def row(label, split, n, *, score=None):
    if score is None:
        score = 10 if label == "human" else 90
    return {
        "genre": "science", "label": label, "split": split,
        "raw_index_0_100": score,
        "text_sha256": hashlib.sha256(f"{split}|{label}|{n}".encode()).hexdigest(),
        "source_id": f"{split}/source/{label}/{n}",
        "group_id": f"{split}/group/{label}/{n}",
        "provenance_evidence_id": f"third-party-review-{n}-{label}",
        "four_detectors_completed": True,
        "provenance_independently_audited": True,
        "independent_of_base_detector_training": True,
    }


def sample(ncal=40, nhold=120):
    return {
        "schema": "genre-calibration-audit-v1", "genre": "science",
        "model_sha256": hashlib.sha256(b"synthetic_model").hexdigest(),
        "observations": (
            [row(label, "calibration", n) for label in ("human", "ai") for n in range(ncal)]
            + [row(label, "final_holdout", n) for label in ("human", "ai") for n in range(nhold)]
        ),
    }


class CalibrationAuditTests(unittest.TestCase):
    def test_calibration_never_opens_holdout(self):
        result = audit(sample())
        self.assertEqual(result["mapping"]["display_cutoff"], 30)
        self.assertFalse(result["threshold_enabled"])
        self.assertFalse(result["publication_authorized"])
        self.assertNotIn("numeric_gates_met", result)

    def test_unknown_provenance_rejected(self):
        data = sample()
        data["observations"][0]["provenance_independently_audited"] = False
        with self.assertRaisesRegex(ValueError, "provenance"):
            audit(data)

    def test_base_model_overlap_not_confirmed_rejected(self):
        data = sample()
        data["observations"][0]["independent_of_base_detector_training"] = False
        with self.assertRaisesRegex(ValueError, "Base-detector"):
            audit(data)

    def test_cross_split_source_leakage_rejected(self):
        data = sample()
        data["observations"][-1]["source_id"] = data["observations"][0]["source_id"]
        with self.assertRaisesRegex(ValueError, "leakage"):
            audit(data)

    def test_duplicate_digest_rejected(self):
        data = sample()
        data["observations"][-1]["text_sha256"] = data["observations"][0]["text_sha256"]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            audit(data)

    def test_final_requires_preexisting_freeze(self):
        with self.assertRaisesRegex(ValueError, "PRE-HOLDOUT freeze"):
            audit(sample(), evaluate_final=True)

    def test_final_insufficient_holdout(self):
        data = sample(nhold=119)
        digest = audit(data)["mapping"]["frozen_digest"]
        data["pre_holdout_freeze"] = {
            "digest": digest, "auditor_approved_before_holdout_access": True,
            "independent_data_audit_completed": True,
        }
        with self.assertRaisesRegex(ValueError, "Insufficient final"):
            audit(data, evaluate_final=True)

    def test_synthetic_numeric_pass_still_not_activated(self):
        data = sample()
        digest = audit(data)["mapping"]["frozen_digest"]
        data["pre_holdout_freeze"] = {
            "digest": digest, "auditor_approved_before_holdout_access": True,
            "independent_data_audit_completed": True,
        }
        result = audit(data, evaluate_final=True)
        self.assertTrue(result["numeric_gates_met"])
        self.assertFalse(result["threshold_enabled"])
        self.assertFalse(result["publication_authorized"])
        self.assertEqual(result["false_alarms"], 0)

    def test_frozen_digest_changes_with_calibration(self):
        data = sample()
        old = audit(data)["mapping"]["frozen_digest"]
        data["observations"][0]["raw_index_0_100"] = 11
        self.assertNotEqual(old, audit(data)["mapping"]["frozen_digest"])

    def test_holdout_changes_do_not_change_mapping(self):
        data = sample()
        original = audit(data)["mapping"]
        data["observations"][-1]["raw_index_0_100"] = 5
        self.assertEqual(original, audit(data)["mapping"])

    def test_numeric_failure_still_hard_disabled(self):
        data = sample()
        digest = audit(data)["mapping"]["frozen_digest"]
        for row_value in data["observations"]:
            if row_value["split"] == "final_holdout" and row_value["label"] == "human":
                row_value["raw_index_0_100"] = 95
        data["pre_holdout_freeze"] = {
            "digest": digest, "auditor_approved_before_holdout_access": True,
            "independent_data_audit_completed": True,
        }
        result = audit(data, evaluate_final=True)
        self.assertFalse(result["numeric_gates_met"])
        self.assertFalse(result["threshold_enabled"])

    def test_wilson_not_perfect_with_small_n(self):
        self.assertGreater(wilson(0, 12)[1], 0.2)


if __name__ == "__main__":
    unittest.main()
