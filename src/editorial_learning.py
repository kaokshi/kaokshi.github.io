"""Analyze verified feedback and test editorial proposals on independent cases."""
from collections import Counter
from pathlib import Path
import re

from .editorial_memory import read_json

CATEGORIES = {
    "missing_cta": "Before submitting, compare the last paragraph with the original and preserve a natural channel-specific subscription CTA.",
    "article_length": "Develop underexplained scenes or mechanisms with concrete new details. Verify 3000–6000 visible non-whitespace characters.",
    "unsupported_claim": "Keep source-backed claims only. Compare each numeric and causal claim with its exact primary-source excerpt.",
    "section_structure": "Preserve each section's bold heading and body. Verify that the first nonempty line is the first heading.",
    "repeated_paragraphs": "Identify duplicated ideas and paragraphs before editing. Replace only the repeated contribution.",
}
SHA = re.compile(r"[a-f0-9]{64}")


def valid(row):
    return (isinstance(row, dict) and row.get("owner_verified") is True
            and row.get("channel") in {"legends", "science"}
            and row.get("issue") in CATEGORIES and isinstance(row.get("article_id"), str)
            and isinstance(row.get("provenance"), str) and bool(row["provenance"].strip())
            and all(isinstance(row.get(k), str) and SHA.fullmatch(row[k])
                    for k in ("text_sha256", "source_sha256", "baseline_sha256", "candidate_sha256"))
            and row["baseline_sha256"] != row["candidate_sha256"]
            and row.get("partition") in {"development", "holdout"})


def evaluate_cases(rows, minimum):
    unique = {}
    for row in rows:
        if valid(row):
            key = (row["channel"], row["issue"], row["source_sha256"])
            unique.setdefault(key, row)
    development = [row for row in unique.values() if row["partition"] == "development"]
    dev_artifacts = {row[k] for row in development
                     for k in ("source_sha256", "text_sha256", "baseline_sha256", "candidate_sha256")}
    dev_articles = {row["article_id"] for row in development}
    counts = Counter((row["channel"], row["issue"]) for row in development)
    proposals = []
    for (channel, issue), count in counts.most_common():
        if count < minimum:
            continue
        cases = [row for row in unique.values()
                 if (row["partition"] == "holdout" and row["channel"] == channel
                     and row["issue"] == issue and row["article_id"] not in dev_articles
                     and not dev_artifacts.intersection(row[k] for k in
                         ("source_sha256", "text_sha256", "baseline_sha256", "candidate_sha256"))
                     and row.get("candidate_instruction") == CATEGORIES[issue]
                     and all(type(row.get(k)) is bool for k in
                             ("baseline_resolved", "candidate_resolved", "regression")))]
        baseline = sum(row["baseline_resolved"] for row in cases)
        candidate = sum(row["candidate_resolved"] for row in cases)
        regressions = sum(row["regression"] or (row["baseline_resolved"] and not row["candidate_resolved"])
                          for row in cases)
        status = ("rejected_regression" if regressions else "independent_holdout_pending"
                  if len(cases) < 5 else "candidate_improved" if candidate > baseline
                  else "rejected_no_improvement")
        proposals.append({"channel": channel, "issue": issue, "development_cases": count,
                          "held_out_cases": len(cases), "baseline_resolved": baseline,
                          "candidate_resolved": candidate, "regressions": regressions,
                          "status": status, "instruction": CATEGORIES[issue],
                          "rollback": "Keep the current production prompt.",
                          "requires_owner_approval": True})
    return {"verified_cases": len(unique), "proposals": proposals, "production_modified": False}


def learn(root: Path, minimum):
    feedback = [read_json(path) for path in (root / "feedback").glob("*.json")]
    return evaluate_cases(feedback, minimum)
