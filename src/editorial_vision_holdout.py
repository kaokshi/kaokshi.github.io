"""Score blinded human labels against separate, frozen visual predictions."""
from collections import defaultdict
import json
import re
from hashlib import sha256
from pathlib import Path

from editorial_audit_replay import recount


def score(labels, predictions, training_sources, labels_sha256):
    if sha256(labels.read_bytes()).hexdigest() != labels_sha256:
        raise ValueError("Human labels differ from the frozen set")
    examples = json.loads(labels.read_text())
    predictions = json.loads(predictions.read_text())
    seen = set()
    groups = defaultdict(list)
    for case in examples:
        key = case["case_sha256"]
        if (not re.fullmatch("[0-9a-f]{64}", key) or key in seen or not case.get("human_reviewer")
                or not case.get("source_group") or case["source_group"] in training_sources
                or type(case["accept"]) is not bool or not case.get("defect_type")):
            raise ValueError("Invalid or overlapping holdout labels")
        seen.add(key)
        groups[case["defect_type"]].append({"expected_accept": case["accept"], "predicted_accept": predictions[key]})
    if not groups or set(predictions) != seen:
        raise ValueError("Incomplete or unexpected predictions")
    if any({r["expected_accept"] for r in rows} != {True, False} for rows in groups.values()):
        raise ValueError("Each defect requires positive and negative controls")
    return {kind: recount(rows, len(rows)) for kind, rows in groups.items()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("training_sources", type=Path)
    parser.add_argument("labels_sha256")
    args = parser.parse_args()
    print(json.dumps(score(args.labels, args.predictions, set(json.loads(args.training_sources.read_text())),
                           args.labels_sha256), ensure_ascii=False))
