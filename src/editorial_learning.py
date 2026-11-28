"""Analyze verified feedback and test editorial proposals on independent cases."""
from collections import Counter
from pathlib import Path
import re

from .editorial_memory import digest, read_json
from .editorial_review import canonical, parse_article, structural_checks

CATEGORIES = {
    "missing_cta": "Before submitting, compare the last paragraph with the original and preserve a natural channel-specific subscription CTA.",
    "article_length": "Develop underexplained scenes or mechanisms with concrete new details. Verify 4000–6000 visible non-whitespace characters (legacy orders: 3000–6000). Allow up to 6500 only with a concrete recorded reason. Do not shrink toward the minimum; volume and content should differ between articles.",
    "unsupported_claim": "Keep source-backed claims only. Compare each numeric and causal claim with its exact primary-source excerpt.",
    "section_structure": "Preserve each section's bold heading and body. Verify that the first nonempty line is the first heading.",
    "repeated_paragraphs": "Identify duplicated ideas and paragraphs before editing. Replace only the repeated contribution.",
}
SHA = re.compile(r"[a-f0-9]{64}")


def valid(row):
    return (isinstance(row, dict) and row.get("owner_verified") is True
            and row.get("channel") in {"legends", "science", "cinema"}
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


def historical_observations(root):
    """Summarize verified local version pairs without treating them as owner-approved holdout."""
    article_count, valid_pairs = 0, 0
    issues, calls, decisions, comparisons = Counter(), Counter(), Counter(), Counter()
    for state_file in sorted(root.glob("????-??-??/state.json")):
        try:
            data = read_json(state_file)
            for call in data.get("calls", []):
                if call.get("provider") in {"groq", "openrouter"} and call.get("status") in {
                        "responded", "failed", "reserved"}:
                    calls[(call["provider"], call["status"])] += 1
            for article_id, article in data.get("articles", {}).items():
                if not re.fullmatch(r"(?:legends|science|cinema)-\d{2}", article_id):
                    continue
                article_count += 1
                genre = article_id.split("-")[0]
                for change in article.get("history", []):
                    model = change.get("provider")
                    if model not in {"groq", "openrouter"} or change.get("status") not in {"accepted", "rejected"}:
                        continue
                    before, after = change.get("before"), change.get("after")
                    if not all(isinstance(x, str) and SHA.fullmatch(x) for x in (before, after)) or before == after:
                        continue
                    reports = []
                    for sha in (before, after):
                        versions = state_file.parent / article_id / "versions"
                        raw = read_json(versions / (sha + ".json"), {})
                        text = versions / (sha + ".txt")
                        if (raw.get("text_sha256") == sha and text.is_file()
                                and digest(text.read_bytes()) == sha and isinstance(raw.get("issues"), list)):
                            reports.append(raw)
                    if len(reports) != 2:
                        continue
                    valid_pairs += 1
                    decisions[(model, change["status"])] += 1
                    for issue in {x.partition(":")[0] for x in reports[0]["issues"]} & CATEGORIES.keys():
                        issues[(genre, issue)] += 1
                    complete = all(all(report.get(k, {}).get("status") == "completed"
                                       for k in ("model_a", "grammar", "editorial")) for report in reports)
                    comparisons[(model, "checked" if complete else "incomplete")] += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue

    return {"articles": article_count, "verified_revision_pairs": valid_pairs,
            "provider_calls": [{"provider": model, "status": status, "count": n}
                               for (model, status), n in sorted(calls.items())],
            "decisions": [{"provider": model, "status": status, "count": n}
                          for (model, status), n in sorted(decisions.items())],
            "comparison": [{"provider": model, "checks": status, "count": n}
                           for (model, status), n in sorted(comparisons.items())],
            "recurrent_issues": [{"channel": genre, "issue": issue, "count": n}
                                 for (genre, issue), n in issues.most_common()]}


def learn(root: Path, minimum):
    feedback = []
    invalid = 0
    for path in sorted((root / "feedback").glob("*.json")):
        try:
            row = read_json(path)
            if not valid(row):
                invalid += 1
                continue
            if row["partition"] == "holdout":
                evidence = {}
                for version in ("baseline", "candidate"):
                    item = (root / row[f"{version}_file"]).resolve()
                    if item.is_relative_to(root.resolve()) and item.suffix == ".md" and item.is_file():
                        article = parse_article(item.read_text(encoding="utf-8"))
                        if digest(canonical(article)) == row[f"{version}_sha256"]:
                            evidence[version] = structural_checks(article, [])["issues"]
                if set(evidence) != {"baseline", "candidate"}:
                    invalid += 1
                    continue
                if row["issue"] != "unsupported_claim":
                    known = {version: {item.partition(":")[0] for item in issues}
                             for version, issues in evidence.items()}
                    row["baseline_resolved"] = row["issue"] not in known["baseline"]
                    row["candidate_resolved"] = row["issue"] not in known["candidate"]
                    row["regression"] = row.get("regression") is True or bool(
                        known["candidate"] - known["baseline"])
            feedback.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            invalid += 1
    return {**evaluate_cases(feedback, minimum), "invalid_or_unavailable_cases": invalid,
            "observations": historical_observations(root)}


def propose_pull_requests(result, token):
    """Submit independent evidence summaries to a review branch, without artifacts or texts."""
    import base64
    import json
    import os
    import subprocess

    repository = "kaokshi/kaokshi.github.io"
    root = f"repos/{repository}"
    env = {**os.environ, "GH_TOKEN": token}

    def api(route, method="GET", payload=None, absent=False):
        args = ["gh", "api", "-X", method, f"{root}/{route}"]
        if payload is not None:
            args.extend(["--input", "-"])
        invocation = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                                    env=env, capture_output=True, text=True, timeout=30, check=False)
        response = json.loads(invocation.stdout or "{}")
        if invocation.returncode and not (absent and str(response.get("status")) == "404"):
            raise RuntimeError("learning_proposal_github_rejected")
        return response if not invocation.returncode else None

    proposed = []
    for row in result["proposals"]:
        if row["status"] != "candidate_improved":
            continue
        # The record contains aggregates and a checked-in instruction. Private
        # feedback, article identifiers and source hashes never enter the PR.
        tag = f'{row["channel"]}-{row["issue"]}'
        branch = "editorial/learning-" + tag
        prior = api("pulls?state=open&head=kaokshi:" + branch)
        if prior:
            proposed.append(prior[0]["html_url"])
            continue
        path = f"config/editorial_prompt_candidates/{tag}.json"
        document = {"candidate": row["instruction"], "channel": row["channel"], "issue": row["issue"],
                    "holdout_cases": row["held_out_cases"], "baseline_resolved": row["baseline_resolved"],
                    "candidate_resolved": row["candidate_resolved"], "regressions": row["regressions"],
                    "deployment": "requires_owner_review", "rollback": row["rollback"]}
        if api("git/ref/heads/" + branch, absent=True):
            existing = api(f"contents/{path}?ref={branch}", absent=True)
            if not existing or json.loads(base64.b64decode(existing["content"])) != document:
                raise RuntimeError("learning_proposal_branch_conflict")
        else:
            base = api("git/ref/heads/main")["object"]["sha"]
            tree = api("git/commits/" + base)["tree"]["sha"]
            new_tree = api("git/trees", "POST", {"base_tree": tree, "tree": [{
                "path": path, "mode": "100644", "type": "blob",
                "content": json.dumps(document, indent=2) + "\n"}]})["sha"]
            commit = api("git/commits", "POST", {"message": f"Propose editorial instruction for {tag}",
                         "tree": new_tree, "parents": [base]})["sha"]
            api("git/refs", "POST", {"ref": "refs/heads/" + branch, "sha": commit})
        pr = api("pulls", "POST", {"title": f"Editorial learning proposal: {tag}", "head": branch,
                 "base": "main", "draft": True,
                 "body": "Independent holdout results and rollback are recorded in the proposal. "
                         "The production prompt remains subject to owner approval."})
        proposed.append(pr["html_url"])
    return proposed
