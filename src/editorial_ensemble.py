#!/usr/bin/env python3
"""Offline experimental ensemble of the FOUR APPROVED GitHub checks.

Never a proof of AI authorship, nor a publication approval.
Training needs provenance-verified labelled HUMAN and AI examples, with
four *actual* detector results for the same immutable text. No placeholder
or guessed scores. AI Accent is a style feature, not a probability.
"""
import hashlib
import json
import math
from pathlib import Path

FEATURES = ("rubert_v4", "modernbert", "lightgbm", "ai_accent_detector")
SUPPORTED_GENRES = {"legends", "science", "cinema"}
ACTIVE = {"legends", "science"}  # Cinema model can train; cinema release/revision routing remains paused.
MIN_TRAIN_PER_CLASS = 20  # prototype training only, NOT validation criteria
EPS = 1e-12


def vector(row):
    vals = row.get("features")
    if not isinstance(vals, dict) or set(vals) != set(FEATURES):
        raise ValueError("All four actual scores are required")
    out = []
    for name in FEATURES:
        value = vals[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Missing, malformed or out-of-range detector score: " + name)
        out.append(float(value) / 100)
    return out


def checked_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("Expected labelled dataset list")
    seen = set()
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("label") not in ("human", "ai") or row.get("channel") not in SUPPORTED_GENRES:
            raise ValueError("Unlabelled or unsupported training example")
        if row.get("provenance_verified") is not True or row.get("four_detectors_completed") is not True:
            raise ValueError("Each sample needs VERIFIED provenance and four completed model runs")
        source = row.get("source_id")
        digest = row.get("text_sha256")
        if not isinstance(source, str) or not source.strip() or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Missing source identifier or text sha256")
        try:
            bytes.fromhex(digest)
        except ValueError as exc:
            raise ValueError("Invalid text sha256") from exc
        unique = (row["channel"], digest)
        if unique in seen:
            raise ValueError("Duplicate text digest")
        seen.add(unique)
        vector(row)
        result.append(row)
    return result


def sigmoid(z):
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    t = math.exp(z)
    return t / (1 + t)


def fit(rows, *, steps=1000, learning_rate=0.15):
    """Deterministic ridge-regularized logistic stacker. Experimental only."""
    data = checked_rows(rows)
    if not data:
        raise ValueError("Empty training data")
    genres = {r["channel"] for r in data}
    if len(genres) != 1:
        raise ValueError("Train each channel separately")
    counts = {c: sum(r["label"] == c for r in data) for c in ("human", "ai")}
    if min(counts.values()) < MIN_TRAIN_PER_CLASS:
        raise ValueError("Insufficient real labelled training samples")
    if any(r.get("split") != "train" for r in data):
        raise ValueError("Only training partition may be fitted")
    w = [0.0] * 5
    x = [[1.0] + vector(r) for r in data]
    y = [int(r["label"] == "ai") for r in data]
    for _ in range(steps):
        grad = [0.0] * len(w)
        for features, label in zip(x, y):
            p = sigmoid(sum(a*b for a,b in zip(w, features)))
            for j, val in enumerate(features):
                grad[j] += (p - label) * val
        for j in range(len(w)):
            grad[j] = grad[j] / len(x) + (0 if j == 0 else 0.01 * w[j])
            w[j] -= learning_rate * grad[j]
    return {"schema": "experimental-ensemble-v1", "status": "experimental_unvalidated",
            "channel": data[0]["channel"], "features": list(FEATURES),
            "coefficients": w, "n_train": len(data), "training_sources": len({r["source_id"] for r in data}),
            "score_semantics": "experimental_uncalibrated_0_to_100_not_probability"}


def score(model, row):
    if model.get("status") != "experimental_unvalidated" or model.get("features") != list(FEATURES):
        raise ValueError("Unrecognized model")
    if row.get("channel") != model.get("channel"):
        raise ValueError("Wrong genre")
    x = [1.] + vector(row)
    w = model["coefficients"]
    if len(w) != 5 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in w):
        raise ValueError("Invalid model coefficients")
    return round(100 * sigmoid(sum(a*b for a,b in zip(w, x))), 4)


def wilson(successes, n, z=1.959963984540054):
    if n < 1:
        raise ValueError("Empty holdout")
    p = successes / n
    den = 1 + z*z/n
    mid = (p + z*z/(2*n)) / den
    radius = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return max(0, mid-radius), min(1, mid+radius)


def evaluate(model, holdout, threshold, training_rows):
    """Fail closed on source or text leakage and missing independent holdout."""
    holdout = checked_rows(holdout)
    train = checked_rows(training_rows)
    if any(r.get("split") != "holdout" or r["channel"] != model["channel"] for r in holdout):
        raise ValueError("Holdout labels/channel invalid")
    trained_sources = {r["source_id"] for r in train}
    trained_digests = {r["text_sha256"] for r in train}
    holdout_sources = [r["source_id"] for r in holdout]
    if trained_sources.intersection(holdout_sources) or trained_digests.intersection(r["text_sha256"] for r in holdout):
        raise ValueError("Train/holdout leakage")
    if len(set(holdout_sources)) != len(holdout_sources):
        raise ValueError("Holdout sources are not independent")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= threshold <= 100:
        raise ValueError("Invalid frozen threshold")
    if any(r.get("threshold_frozen_before_holdout") is not True or r.get("disjoint_from_model_training") is not True for r in holdout):
        raise ValueError("Holdout provenance or threshold freeze not established")
    human = [r for r in holdout if r["label"] == "human"]
    ai = [r for r in holdout if r["label"] == "ai"]
    fp = sum(score(model, r) > threshold for r in human)
    tp = sum(score(model, r) > threshold for r in ai)
    return {"human_holdout_n": len(human), "ai_holdout_n": len(ai),
            "human_false_alarms": fp, "ai_detected": tp,
            "human_false_alarm_95pct": wilson(fp, len(human)),
            "ai_detected_95pct": wilson(tp, len(ai)),
            "eligible_for_release": False,
            "note": "Report is experimental; human review of provenance and external training overlap remains required."}


def route_revision(*, channel, attempt, substantive_issues, checks_completed=True,
                   score_percent=None, threshold=None, validated=False):
    """Owner-approved editorial rule: actual defects, no AI cutoff.

    An AI-only signal never triggers rewriting. Correct substantive issues
    at most twice; after two attempts route to owner. Only routing decisions,
    not text rewriting, notifications, publication or GitHub workflow execution.
    """
    if channel not in ACTIVE:
        return {"decision": "paused_channel", "release": False}
    if not isinstance(attempt, int) or isinstance(attempt, bool) or not 0 <= attempt <= 2:
        raise ValueError("attempt must be 0, 1 or 2")
    if not isinstance(substantive_issues, list) or not all(
            isinstance(x, str) and x.strip() for x in substantive_issues):
        raise ValueError("Issues must be a list of nonempty descriptions")
    if checks_completed is not True:
        return {"decision": "owner_review", "reason": "checks_incomplete",
                "release": False}
    # All model outputs and threshold experiments remain diagnostic.
    # No value of score_percent, validated or threshold authorizes changes.
    if not substantive_issues:
        return {"decision": "quality_cleared_for_other_release_gates",
                "reason": "no_substantive_defects", "release": False}
    if attempt >= 2:
        return {"decision": "owner_review", "reason": "two_revisions_exhausted",
                "issues": substantive_issues, "release": False}
    return {"decision": "targeted_revision", "attempt_next": attempt + 1,
            "issues": substantive_issues, "rerun_all_checks": True,
            "release": False}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--train", help="Verified labelled JSON dataset, must contain 'train' and 'holdout' rows")
    parser.add_argument("--out", help="Output folder for experimental model and report")
    args=parser.parse_args()
    if not args.train or not args.out:
        parser.error("--train and --out are mandatory (no synthetic real-world training)")
    dataset=json.loads(Path(args.train).read_text(encoding="utf-8"))
    data=checked_rows(dataset)
    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    report={}
    for channel in sorted(SUPPORTED_GENRES):
        train=[r for r in data if r["channel"]==channel and r.get("split")=="train"]
        holdout=[r for r in data if r["channel"]==channel and r.get("split")=="holdout"]
        model=fit(train)
        # Fixed research threshold, not a production setting; must be frozen before holdout.
        evaluation=evaluate(model,holdout,90,train)
        report[channel]=evaluation
        (output / (channel + "-experimental.json")).write_text(json.dumps(model,indent=2)+"\n")
    (output / "evaluation.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"training":"experimental_only","evaluation":report,"publication_authorized":False}))


if __name__ == "__main__":
    main()
