#!/usr/bin/env python3
"""Shadow calibration of the EXISTING ruBERT v4, without new AI detectors.

Do not call raw ruBERT scores a probability of AI authorship. Do not modify
the active 25% release gate. Threshold proposals require provenance-verified,
disjoint calibration and holdout samples from the same editorial genre.
"""
import argparse
import json
import math
from pathlib import Path
from collections import defaultdict

GENRES = ("legends", "science", "cinema")
METRICS = ("max", "p75", "mean", "share_over_25")
MIN_CALIBRATION_PER_CLASS = 50
MIN_HOLDOUT_PER_CLASS = 30
FPR_CEILING = 0.05


def percentile(sorted_values, q):
    if not sorted_values:
        raise ValueError("Empty scores")
    pos = (len(sorted_values)-1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    return sorted_values[lower] + (sorted_values[upper]-sorted_values[lower]) * (pos-lower)


def summarize_windows(values):
    if not isinstance(values, list) or not values or any(
        not isinstance(x, (int, float)) or isinstance(x, bool)
        or not math.isfinite(x) or not 0 <= x <= 1 for x in values
    ):
        raise ValueError("Window probabilities must be finite floats in [0,1]")
    vals=sorted(values)
    return {
        "n_windows":len(vals),
        "max":round(vals[-1],6),
        "mean":round(sum(vals)/len(vals),6),
        "p50":round(percentile(vals,.5),6),
        "p75":round(percentile(vals,.75),6),
        "p90":round(percentile(vals,.9),6),
        "share_over_25":round(sum(x>.25 for x in vals)/len(vals),6),
        "share_over_native_threshold":None,
        "notice":"All values are uncalibrated *model scores*, not a verified percentage of AI authorship.",
    }


def _rates(samples, metric, threshold):
    human=[row for row in samples if row["label"]=="human"]
    ai=[row for row in samples if row["label"]=="ai"]
    fpr=sum(row["stats"][metric]>threshold for row in human)/len(human)
    tpr=sum(row["stats"][metric]>threshold for row in ai)/len(ai)
    return {"false_positive_rate":round(fpr,4),
            "true_positive_rate":round(tpr,4),
            "human_count":len(human),"ai_count":len(ai)}


def _unique_validated_samples(rows):
    seen=set()
    result=[]
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError("Invalid dataset row")
        identifier=row.get("id")
        if (not isinstance(identifier,str) or not identifier.strip() or identifier in seen):
            raise ValueError("Duplicate/missing sample identity")
        seen.add(identifier)
        if (row.get("provenance_verified") is not True
                or row.get("genre") not in GENRES
                or row.get("label") not in ("human","ai")
                or row.get("split") not in ("calibration","holdout")
                or not row.get("source_group")):
            raise ValueError("Unverified provenance / missing genre, split or source group")
        stats=summarize_windows(row.get("window_probabilities"))
        result.append({**row,"stats":stats})
    # Prevent versions of the same document or author-source series crossing
    # splits. Labelled text can be biased by source; measure group leakage.
    groups={}
    for row in result:
        group=(row["genre"],row["source_group"])
        if group in groups and groups[group]!=row["split"]:
            raise ValueError("Source group leaked across calibration and holdout")
        groups[group]=row["split"]
    return result


def suggest_thresholds(rows, metric="p75"):
    if metric not in METRICS:
        raise ValueError("Invalid metric")
    valid=_unique_validated_samples(rows)
    proposals={}
    for genre in GENRES:
        subset=[row for row in valid if row["genre"]==genre]
        calibration=[row for row in subset if row["split"]=="calibration"]
        holdout=[row for row in subset if row["split"]=="holdout"]
        c_human=[row for row in calibration if row["label"]=="human"]
        c_ai=[row for row in calibration if row["label"]=="ai"]
        h_human=[row for row in holdout if row["label"]=="human"]
        h_ai=[row for row in holdout if row["label"]=="ai"]
        counts={"calibration_human":len(c_human),"calibration_ai":len(c_ai),
                "holdout_human":len(h_human),"holdout_ai":len(h_ai)}
        if (min(len(c_human),len(c_ai))<MIN_CALIBRATION_PER_CLASS
                or min(len(h_human),len(h_ai))<MIN_HOLDOUT_PER_CLASS):
            proposals[genre]={"status":"insufficient_labeled_evidence",
                              "counts":counts,"candidate_threshold":None,
                              "holdout_validated":False,
                              "eligible_to_change_release_gate":False}
            continue
        # Use only the calibration split to pick the lowest cutoff under the
        # empirical FPR ceiling. Holdout is never used to select threshold.
        candidates=sorted({0.0,1.0,*[x["stats"][metric] for x in calibration]})
        feasible=[x for x in candidates if _rates(calibration,metric,x)["false_positive_rate"]<=FPR_CEILING]
        candidate=feasible[0] if feasible else 1.0
        calib=_rates(calibration,metric,candidate)
        held=_rates(holdout,metric,candidate)
        proposals[genre]={
            "status":"holdout_evaluated",
            "metric":metric,"candidate_threshold":candidate,"counts":counts,
            "calibration_rates":calib,"holdout_rates":held,
            "holdout_validated":held["false_positive_rate"]<=FPR_CEILING,
            "eligible_to_change_release_gate":False,
            "notice":("A 5% observed FPR on 30-50 documents is statistically noisy. "
                      "Manual review, confidence intervals and domain drift assessment required.")
        }
    return {
        "schema":"rubert-calibration-shadow-v1",
        "detector":"existing_ruBERT_v4_only",
        "statistic":metric,
        "target_max_human_false_positive_rate":FPR_CEILING,
        "release_gate_changed":False,
        "real_world_ai_probability_calibrated":False,
        "proposals":proposals,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--windows-json",help="Existing GitHub ruBERT model's JSON output for one document")
    p.add_argument("--dataset-jsonl",help="Provenance-verified labeled controls, disjoint splits")
    p.add_argument("--metric",choices=METRICS,default="p75")
    p.add_argument("--output",default="editorial-calibration-reports/summary.json")
    args=p.parse_args()
    if bool(args.windows_json)==bool(args.dataset_jsonl):
        p.error("Specify exactly one of --windows-json or --dataset-jsonl")
    if args.windows_json:
        doc=json.loads(Path(args.windows_json).read_text(encoding="utf-8"))
        if "checks" in doc:
            values=doc["checks"]["rubert_v4"]["data"]["window_probabilities"]
        else:
            values=doc["window_probabilities"]
        result={"schema":"rubert-shadow-single-article-v1",
                "statistics":summarize_windows(values),
                "release_gate_changed":False,
                "ai_generation_probability":None}
    else:
        rows=[json.loads(line) for line in Path(args.dataset_jsonl).read_text(encoding="utf-8").splitlines() if line.strip()]
        result=suggest_thresholds(rows,args.metric)
    dest=Path(args.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":
    main()
