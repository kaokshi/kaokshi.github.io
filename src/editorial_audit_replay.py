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
    print(args.file.name, json.dumps(recount(json.loads(args.file.read_text()), args.count)))
