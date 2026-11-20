#!/usr/bin/env python3
"""Offline genre-specific calibration audit. NEVER activates AI thresholds or publication.

Input: licensed, locally held summary observations only. Raw third-party text is
neither read nor stored. Human provenance and base-model nonoverlap require
external audit: metadata claims alone cannot prove those facts.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

GENRES = frozenset({"legends", "science", "cinema"})
SPLITS = frozenset({"calibration", "final_holdout"})
MIN_CALIBRATION_PER_CLASS = 40
MIN_FINAL_PER_CLASS = 120
CUTOFF = 30.0
FPR_LIMIT = 0.05
TPR_FLOOR = 0.60
Z_95 = 1.959963984540054


def wilson(k, n):
    if not (isinstance(k, int) and isinstance(n, int) and 0 <= k <= n and n > 0):
        raise ValueError("Invalid Wilson counts")
    z2 = Z_95**2
    p = k / n
    middle = (p + z2 / (2 * n)) / (1 + z2 / n)
    error = Z_95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return (max(0.0, middle - error), min(1.0, middle + error))


def sha(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def checked_rows(rows, genre):
    if not isinstance(rows, list):
        raise ValueError("Expected observation list")
    normalized = []
    seen_texts = set()
    group_splits = {}
    source_splits = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("genre") != genre:
            raise ValueError("Invalid row or mixed genres")
        if row.get("split") not in SPLITS or row.get("label") not in ("human", "ai"):
            raise ValueError("Invalid split or authorship label")
        index = row.get("raw_index_0_100")
        if (not isinstance(index, (int, float)) or isinstance(index, bool)
                or not math.isfinite(index) or not 0 <= index <= 100):
            raise ValueError("Invalid raw index")
        for key in ("text_sha256", "source_id", "group_id", "provenance_evidence_id"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("Missing essential provenance metadata: " + key)
        if not sha(row["text_sha256"]) or row["text_sha256"] in seen_texts:
            raise ValueError("Duplicate or invalid text SHA-256")
        seen_texts.add(row["text_sha256"])
        # Metadata declarations need later human verification, not blind trust.
        if row.get("four_detectors_completed") is not True:
            raise ValueError("Incomplete detector run")
        if row.get("provenance_independently_audited") is not True:
            raise ValueError("Unverified provenance declaration")
        if row.get("independent_of_base_detector_training") is not True:
            raise ValueError("Base-detector training overlap unverified")
        for field, tracking in (("group_id", group_splits), ("source_id", source_splits)):
            group = row[field]
            previous = tracking.setdefault(group, row["split"])
            if previous != row["split"]:
                raise ValueError("Calibration/final group leakage via " + field)
        normalized.append(row)
    return normalized


def choose_raw_threshold(calibration):
    """Empirical top-tail threshold, on calibration data ONLY.

    Choose threshold at or above the max observed human calibration index:
    minimizes observed false alarms without pretending statistical validation.
    Never examine final holdout during threshold selection.
    """
    humans = [float(row["raw_index_0_100"]) for row in calibration if row["label"] == "human"]
    machines = [row for row in calibration if row["label"] == "ai"]
    if len(humans) < MIN_CALIBRATION_PER_CLASS or len(machines) < MIN_CALIBRATION_PER_CLASS:
        raise ValueError("Insufficient calibration counts")
    return max(humans)


def calibrated_index(raw, raw_threshold):
    # When raw == threshold, calibrated == 30, so strict >30 is preserved.
    return max(0.0, min(100.0, CUTOFF + float(raw) - raw_threshold))


def frozen_digest(genre, model_sha, threshold, calibration_digests):
    payload = json.dumps({
        "genre": genre, "model_sha256": model_sha,
        "raw_threshold": threshold, "display_cutoff": CUTOFF,
        "calibration_text_sha256": sorted(calibration_digests),
        "mapping": "clipped_translation_v1"
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit(dataset, *, evaluate_final=False):
    if dataset.get("schema") != "genre-calibration-audit-v1":
        raise ValueError("Unrecognized schema")
    genre = dataset.get("genre")
    if genre not in GENRES:
        raise ValueError("Unsupported genre")
    model_sha = dataset.get("model_sha256")
    if not sha(model_sha):
        raise ValueError("Missing frozen model SHA256")
    rows = checked_rows(dataset.get("observations"), genre)
    cal = [row for row in rows if row["split"] == "calibration"]
    holdout = [row for row in rows if row["split"] == "final_holdout"]
    t = choose_raw_threshold(cal)
    digest = frozen_digest(genre, model_sha, t, [row["text_sha256"] for row in cal])
    result = {
        "schema": "genre-calibration-audit-result-v1",
        "genre": genre, "model_sha256": model_sha,
        "calibration_n": len(cal), "final_holdout_n": len(holdout),
        "mapping": {"kind": "clipped_translation_v1", "raw_threshold": t,
                    "display_cutoff": CUTOFF, "frozen_digest": digest},
        "release_authorized": False, "publication_authorized": False,
        "threshold_enabled": False,
        "audit_status": "CALIBRATION_ONLY_AWAITING_INDEPENDENT_FINAL_REVIEW",
        "limitations": [
            "Metadata claims are not external proof of human authorship or no training overlap",
            "Calibration observations are NOT an independent final evaluation",
            "Score is an editorial triage index, NOT the percent of text written by AI"
        ],
    }
    if not evaluate_final:
        return result
    # A committed freeze certificate must be created prior to opening final data.
    certificate = dataset.get("pre_holdout_freeze")
    if not isinstance(certificate, dict) or certificate.get("digest") != digest:
        raise ValueError("No matching PRE-HOLDOUT freeze record")
    if certificate.get("auditor_approved_before_holdout_access") is not True:
        raise ValueError("Threshold not independently frozen before holdout access")
    if certificate.get("independent_data_audit_completed") is not True:
        raise ValueError("Independent provenance audit not attested")
    counts = Counter(row["label"] for row in holdout)
    if counts["human"] < MIN_FINAL_PER_CLASS or counts["ai"] < MIN_FINAL_PER_CLASS:
        raise ValueError("Insufficient final holdout classes")
    fp = sum(calibrated_index(row["raw_index_0_100"], t) > CUTOFF
             for row in holdout if row["label"] == "human")
    tp = sum(calibrated_index(row["raw_index_0_100"], t) > CUTOFF
             for row in holdout if row["label"] == "ai")
    fpr_bounds = wilson(fp, counts["human"])
    tpr_bounds = wilson(tp, counts["ai"])
    meets_numeric = fpr_bounds[1] <= FPR_LIMIT and tpr_bounds[0] >= TPR_FLOOR
    result.update({
        "audit_status": "NUMERIC_GATES_MET_EXTERNAL_SIGNOFF_REQUIRED" if meets_numeric
        else "INDEPENDENT_NUMERIC_GATES_FAILED",
        "holdout_human_n": counts["human"], "holdout_ai_n": counts["ai"],
        "false_alarms": fp, "ai_detected": tp,
        "fpr_95_wilson": list(fpr_bounds), "tpr_95_wilson": list(tpr_bounds),
        "numeric_gates_met": meets_numeric,
        # Intentionally NEVER enables the threshold from a CLI JSON declaration.
        "threshold_enabled": False, "release_authorized": False,
        "publication_authorized": False
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--evaluate-final", action="store_true",
                        help="Requires independently approved, previously frozen certificate")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = audit(data, evaluate_final=args.evaluate_final)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "genre": result["genre"], "audit_status": result["audit_status"],
        "threshold_enabled": False, "publication_authorized": False
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
