"""Recount historical predictions without any model calls or private image output."""
import argparse
from collections import Counter
import json
from pathlib import Path


def recount(rows, expected_count):
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError("Incomplete case archive")
    counts = Counter()
    for row in rows:
        suffix = "match" if "expected_match" in row else "accept"
        expected, actual = row["expected_" + suffix], row["predicted_" + suffix]
        if expected is None:
            counts["unlabelled"] += 1
        elif type(expected) is not bool or (actual is not None and type(actual) is not bool):
            raise ValueError("Invalid recorded label")
        elif actual is None:
            counts["unavailable"] += 1
        else:
            counts[("TP" if actual else "FN") if expected else ("FP" if actual else "TN")] += 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("count", type=int)
    args = parser.parse_args()
    rows = json.loads(args.file.read_text())
    result = {"historical": recount(rows, args.count)}
    if args.file.name == "detail.json":
        from editorial_science_vision import CONTRACTS, score_transcription
        corrected = []
        for row in rows:
            if row["kind"] == "sourced_scientific_label_check":
                evidence = row["evidence"]
                contract = next(key for key in CONTRACTS if row["case"].startswith(key + "_"))
                raw = json.dumps({k: evidence[k] for k in ("observed_labels", "all_labels_legible")})
                updated = score_transcription(raw, b"", contract)
                row = dict(row, predicted_accept=updated["status"] == "needs_independent_validation")
            corrected.append(row)
        result["offline_rescored"] = recount(corrected, args.count)
    print(args.file.name, json.dumps(result))
