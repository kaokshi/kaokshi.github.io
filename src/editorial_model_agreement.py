#!/usr/bin/env python3
"""First-party ensemble consistency audit, NOT a new AI detector.

Flags contradictory model diagnostics for investigation, not proof of
authorship. Two AIW models share training data; the accent tool measures style.
No cutoff here is a validated automatic author-attribution threshold.
"""
import math

AIW_FAMILY=("modernbert","lightgbm")
REQUIRED=("rubert_v4","modernbert","lightgbm","ai_accent_detector")


def _unit(value):
    return (float(value) if isinstance(value,(int,float)) and
            not isinstance(value,bool) and math.isfinite(value) and
            0.0<=value<=1.0 else None)


def audit(report):
    checks=report.get("checks") if isinstance(report,dict) else None
    if not isinstance(checks,dict) or any(
            not isinstance(checks.get(k),dict) or
            checks[k].get("status")!="completed" or
            not isinstance(checks[k].get("data"),dict)
            for k in REQUIRED):
        return {"status":"incomplete","disputed":False,
                "reason_codes":["missing_or_unfinished_first_party_ai_checks"],
                "note":"Incomplete checks are not a disagreement."}
    rubert=checks["rubert_v4"]["data"]
    modern=checks["modernbert"]["data"]
    light=checks["lightgbm"]["data"]
    accent=checks["ai_accent_detector"]["data"]
    rubric=rubert.get("prediction_native")
    ms=_unit(modern.get("probability"))
    ls=_unit(light.get("probability"))
    ascore=_unit(accent.get("ai_accent_score"))
    if rubric not in ("ai","human") or any(x is None for x in (ms,ls,ascore)):
        return {"status":"incomplete","disputed":False,
                "reason_codes":["invalid_model_output"],
                "note":"One or more actual model results cannot be interpreted."}
    # These are the classifiers' native 0.5 decision boundaries, NOT
    # a user-defined probability of author attribution.
    m_ai=ms>=0.5
    l_ai=ls>=0.5
    reasons=[]
    if m_ai!=l_ai:
        reasons.append("aiw_family_internal_disagreement")
    if m_ai==l_ai and (rubric=="ai")!=m_ai:
        reasons.append("rubert_vs_aiw_family_disagreement")
    return {"status":"completed","disputed":bool(reasons),
            "reason_codes":reasons,
            "family_decisions":{
                "rubert_native":rubric,
                "aiw_modernbert":"ai" if m_ai else "human",
                "aiw_lightgbm":"ai" if l_ai else "human",
                "ai_accent_index_0_100":round(ascore*100,2)},
            "note":"Model disagreements are a review task, not evidence requiring cosmetic rewriting or a publication decision."}
