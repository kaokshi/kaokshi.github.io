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



def document_coverage_score(window_values, spans, total_words):
    """One article-wide index: average each word's covering window scores.

    Overlap is split among windows, so repeated context cannot contribute
    multiple times. This is a *heuristic index* (0-100), not P(AI|text).
    """
    if not isinstance(total_words, int) or isinstance(total_words, bool) or total_words < 1:
        raise ValueError("Invalid document word count")
    summarize_windows(window_values)
    if not isinstance(spans, list) or len(spans) != len(window_values):
        raise ValueError("Window score/span mismatch")
    events_sum = [0.0] * (total_words + 1)
    events_count = [0] * (total_words + 1)
    for value, span in zip(window_values, spans):
        if (not isinstance(span, (list, tuple)) or len(span) != 2 or
            any(not isinstance(n, int) or isinstance(n, bool) for n in span)):
            raise ValueError("Invalid window span")
        start, end = span
        if not 0 <= start < end <= total_words:
            raise ValueError("Window outside document")
        events_sum[start] += value
        events_sum[end] -= value
        events_count[start] += 1
        events_count[end] -= 1
    total = 0.0
    running_sum = 0.0
    running_count = 0
    for i in range(total_words):
        running_sum += events_sum[i]
        running_count += events_count[i]
        if running_count < 1:
            raise ValueError("Unscored document words")
        total += running_sum / running_count
    score = max(0.0, min(100.0, 100.0 * total / total_words))
    return {
        "score_percent": round(score, 2),
        "scale": "0-100",
        "direction": "Higher means a stronger model-generated-text signal",
        "unit": "uncalibrated_whole_document_index",
        "formula": "Mean per-word average of overlapping-window ruBERT scores",
        "words_evaluated": total_words,
        "windows_evaluated": len(window_values),
        "all_words_covered": True,
        "probability_ai_generated": None,
        "calibrated": False,
        "release_gate_changed": False,
        "warning": (
            "This is a single score for the complete article, NOT a verified "
            "probability of AI authorship; publication moderation cannot be predicted."
        ),
    }

def legacy_window_spans(total_words, window_words=85, stride=65):
    """Reproduce pinned runner's window boundaries for older archived reports."""
    if not isinstance(total_words,int) or total_words < 1:
        raise ValueError("Invalid document length")
    if total_words <= window_words:
        return [[0,total_words]]
    starts=list(range(0,total_words-window_words+1,stride))
    if starts[-1] != total_words-window_words:
        starts.append(total_words-window_words)
    return [[start,start+window_words] for start in starts]


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
        words=doc.get("word_count")
        spans=doc.get("window_spans")
        if words is not None and spans is None:
            spans=legacy_window_spans(words,doc.get("model_window_words",85),doc.get("model_window_stride_words",65))
        global_score=document_coverage_score(values,spans,words) if spans is not None else None
        result={"schema":"rubert-shadow-single-article-v2",
                "statistics":summarize_windows(values),
                "whole_document":global_score,
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
