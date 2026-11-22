"""Conservative visual constraints for generated editorial illustrations.

Models suggest candidates, but without independently verified evidence an exact
count or mandatory object is NOT certified. The result is review-only.
"""
import math
from .editorial_providers import Blocked


def validate_constraints(scene):
    counts = scene.get("count_requirements", [])
    if not isinstance(counts, list) or len(counts) > 6:
        raise ValueError("invalid_count_requirements")
    for item in counts:
        if (not isinstance(item, dict) or not isinstance(item.get("object"), str)
                or not 2 <= len(item["object"].strip()) <= 80
                or item.get("mode") != "exact" or type(item.get("value")) is not int
                or not 1 <= item["value"] <= 20):
            raise ValueError("invalid_exact_count_constraint")
    for key in ("must_include", "must_avoid"):
        value = scene.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError("invalid_" + key)
    return counts


def evaluate(scene, assessment, margin_min=0.0):
    """A model-only caption cannot prove mandatory detail or exact quantity."""
    counts = validate_constraints(scene)
    margin = assessment.get("siglip_relative_margin")
    caption = assessment.get("florence_caption")
    if (type(margin) not in (int, float) or not math.isfinite(margin)
            or not isinstance(caption, str) or not caption.strip()):
        raise Blocked("visual_assessment_incomplete")
    issues = []
    if margin <= margin_min:
        issues.append("relative_semantic_mismatch")
    evidence = assessment.get("detail_evidence", {})
    if not isinstance(evidence, dict):
        raise Blocked("visual_detail_evidence_malformed")
    constrained = bool(scene.get("must_include") or scene.get("must_avoid") or counts)
    if constrained and evidence.get("verified_independently") is not True:
        issues.append("mandatory_details_not_independently_verified")
    elif constrained:
        present, absent, observed = (evidence.get("present"),
                                     evidence.get("absent"), evidence.get("counts"))
        if not isinstance(present, list) or not isinstance(absent, list) or not isinstance(observed, dict):
            raise Blocked("visual_detail_evidence_malformed")
        for thing in scene.get("must_include", []):
            if thing not in present:
                issues.append("missing_or_unverified:" + thing)
        for thing in scene.get("must_avoid", []):
            if thing not in absent:
                issues.append("forbidden_or_unverified:" + thing)
        for item in counts:
            if type(observed.get(item["object"])) is not int or observed[item["object"]] != item["value"]:
                issues.append("count_mismatch_or_unverified:" + item["object"])
    return {"passed":not issues,"status":"verified_candidate" if not issues else "owner_review_required",
            "issues":issues,"siglip_relative_margin":margin,
            "caption":caption[:1200],"not_a_fact_or_rights_check":True}


def best_candidate(candidates):
    """Never select the last attempt blindly; do not turn rankings into approval."""
    if not candidates:
        raise Blocked("no_visual_candidates")
    def rank(x):
        review=x["review"]
        score=review.get("siglip_relative_margin",float("-inf"))
        return (int(review["passed"]),-len(review["issues"]),score)
    return max(candidates,key=rank)


def count_prompt(scene):
    counts = validate_constraints(scene)
    return ". ".join(
        "Exactly " + str(item["value"]) + " separate clearly distinguishable "
        + item["object"] + " objects, no more and no fewer"
        for item in counts)
