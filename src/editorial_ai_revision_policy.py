#!/usr/bin/env python3
"""Evidence-gated AI revision routing using ONLY existing GitHub detectors.

This is NOT a new authorship detector. No score threshold is enabled until
reproducible, provenance-verified external validation is approved.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/ai_revision_policy.json"


def wilson_interval(successes, total, z=1.959963984540054):
    if not isinstance(total, int) or total < 1 or not isinstance(successes, int) or not 0 <= successes <= total:
        raise ValueError("Invalid validation counts")
    p = successes / total
    den = 1 + z*z/total
    mid = (p + z*z/(2*total))/den
    rad = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))/den
    return max(0., mid-rad), min(1., mid+rad)


def qualify_validation(validation, requirements):
    """Require empirical 95% bounds, not merely point estimates."""
    if not isinstance(validation,dict) or not isinstance(requirements,dict):
        return False
    for key in ("independently_verified_provenance",
                "no_overlap_with_calibration_or_model_training",
                "frozen_threshold_before_holdout"):
        if validation.get(key) is not True:
            return False
    human=validation.get("human_holdout_n")
    ai=validation.get("ai_holdout_n")
    fp=validation.get("human_false_alarms")
    tp=validation.get("ai_detected")
    if any(not isinstance(v,int) or isinstance(v,bool) for v in (human,ai,fp,tp)):
        return False
    if human<requirements["minimum_human_holdout"] or ai<requirements["minimum_ai_holdout"]:
        return False
    try:
        fpr=wilson_interval(fp,human)
        tpr=wilson_interval(tp,ai)
    except ValueError:
        return False
    return (fpr[1] <= requirements["maximum_human_false_alarm_rate"]
            and tpr[0] >= requirements["minimum_ai_detection_rate"])


def decide_revision(channel, report, policy=None, validation=None):
    """Targeted revision is possible ONLY with an accepted independent validation."""
    if policy is None:
        policy=json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if channel not in policy["current_channels"]:
        return {"decision":"not_active","revision_required":False,"reason":"channel_paused_or_unknown"}
    checks=report.get("checks",{}) if isinstance(report,dict) else {}
    required=policy["only_detectors"]
    if not isinstance(checks,dict) or any(
            not isinstance(checks.get(name),dict)
            or checks[name].get("status")!="completed"
            for name in required):
        return {"decision":"incomplete_ai_tests","revision_required":False,
                "reason":"one_or_more_first_party_checks_missing"}
    if policy.get("status")!="validated" or not qualify_validation(
            validation, policy["validation_requirements"]):
        return {"decision":"diagnostic_only","revision_required":False,
                "reason":"no_independently_validated_ai_rewrite_cutoff",
                "threshold":None}
    cutoff=policy.get("thresholds",{}).get(channel)
    index=report.get("whole_document_index_percent")
    if not isinstance(index,(int,float)) or isinstance(index,bool) or not math.isfinite(index) or not 0<=index<=100:
        return {"decision":"incomplete_ai_tests","revision_required":False,"reason":"whole_document_index_missing"}
    if not isinstance(cutoff,(int,float)) or isinstance(cutoff,bool) or not 0<=cutoff<=100:
        return {"decision":"diagnostic_only","revision_required":False,"reason":"no_numeric_genre_cutoff"}
    if index>cutoff:
        return {"decision":"targeted_editorial_revision","revision_required":True,
                "reason":"validated_genre_specific_signal_above_cutoff",
                "threshold":cutoff,"index":index,
                "instruction":"Inspect flagged claims, repetitiveness and prose; revise only substantive issues, rerun all checks."}
    return {"decision":"no_ai_revision_flag","revision_required":False,
            "reason":"within_validated_genre_cutoff","threshold":cutoff,"index":index}


if __name__ == "__main__":
    print("This module is read-only. No calibrated policy is enabled.")
