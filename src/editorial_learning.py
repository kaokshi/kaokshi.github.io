#!/usr/bin/env python3
"""Turn owner-reviewed revision cases into evidence-backed editorial proposals."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from editorial_ai_check import ROOT
from editorial_agent_v1 import ACTIVE, article_digest
from editorial_writer import Pending, command_provider

PROPOSALS = {
    "facts": "Добавить в фактчек сопоставление каждого проверяемого тезиса с первоисточником.",
    "sources": "Проверять наличие и доступность первоисточников до написания черновика.",
    "language": "Добавить подтверждённые языковые ошибки в контрольный набор корректора.",
    "repetition": "Проверять соседние повторы и похожие материалы перед редактурой.",
    "story_logic": "Проверять последовательность событий и мотивацию персонажей по сценам.",
    "title": "Проверять, соответствует ли обещание заголовка содержанию.",
    "structure": "Сверять порядок смысловых блоков и переходы между ними.",
    "length": "Проверять содержательный объём до смысловой редакции и после неё.",
    "rejection": "Разбирать подтверждённые причины отклонения до изменения инструкций.",
    "opening": "Проверять, даёт ли вступление конкретный вопрос или сцену и ведёт ли к основной теме.",
    "image_rights": "Проверять источник, лицензию и атрибуцию каждой иллюстрации при подборе.",
}
DECISIONS = {"resolved", "unresolved", "regression"}
BATCH_SIZE = 20


def reviewed_observations(case, feedback):
    if case.get("schema") != "zen-editorial-revision-case-v1":
        raise ValueError("Expected a revision case")
    versions = case["versions"]
    article = versions[0]["article"]
    if (feedback.get("reviewer") != "owner" or article["channel"] not in ACTIVE
            or feedback.get("slug") != article["slug"]
            or feedback.get("channel") != article["channel"]
            or article_digest(article) != versions[0]["text_sha256"]
            or feedback.get("source_text_sha256") != versions[0]["text_sha256"]):
        raise ValueError("Owner feedback must identify the original article and channel")
    version_sha = feedback.get("reviewed_text_sha256")
    for i, version in enumerate(versions):
        if version["text_sha256"] == version_sha:
            break
    else:
        raise ValueError("Owner feedback references a version absent from the case")
    revised = version["article"]
    report = version.get("report") or {}
    if (article_digest(revised) != version_sha or revised["slug"] != article["slug"]
            or revised["channel"] != article["channel"] or report.get("text_sha256") != version_sha):
        raise ValueError("Reviewed version needs its own matching check report")
    findings = feedback.get("findings")
    if not isinstance(findings, dict) or not findings:
        raise ValueError("Expected owner-confirmed findings by category")
    observations = []
    for category, outcome in findings.items():
        if category not in PROPOSALS or outcome not in DECISIONS:
            raise ValueError("Unknown feedback category or decision")
        observations.append({
            "channel": article["channel"], "slug": article["slug"],
            "source_sha256": versions[0]["text_sha256"], "reviewed_sha256": version_sha,
            "category": category, "owner_decision": outcome, "revision_stage": version["stage"],
            "rule": version["rule"] if version.get("rule") in {
                "duplicate_paragraph", "duplicate_comma", "semantic", "article_length",
            } else None,
            "checks_completed": report.get("automated_checks_completed") is True,
            "report_pointer": f"/versions/{i}/report",
        })
    return observations


def report(observations, *, on_request=False):
    by_version = {}
    for row in observations:
        key = (row["channel"], row["slug"], row["source_sha256"],
               row["reviewed_sha256"], row["category"])
        if key in by_version and by_version[key] != row:
            raise ValueError("Conflicting owner observations for the same version and category")
        by_version[key] = row
    rows = [by_version[key] for key in sorted(by_version)]
    genres = {}
    for channel in ACTIVE:
        channel_rows = [row for row in rows if row["channel"] == channel]
        distinct_articles = len({row["slug"] for row in channel_rows})
        candidates = []
        if on_request or distinct_articles >= BATCH_SIZE:
            for category, proposal in PROPOSALS.items():
                category_rows = [row for row in channel_rows if row["category"] == category]
                distinct = {row["slug"] for row in category_rows}
                if len(distinct) >= 2:
                    candidates.append({
                        "category": category, "affected_articles": len(distinct), "proposal": proposal,
                        "decisions": dict(Counter(row["owner_decision"] for row in category_rows)),
                    })
        genres[channel] = {"verified_articles": distinct_articles, "proposals": candidates}
    return {
        "schema": "zen-editorial-learning-report-v1", "observations": rows, "genres": genres,
        "proposal_requirements": ["separate_branch", "independent_holdout", "regression_tests",
                                  "rollback_plan", "owner_approval"],
    }


def evaluate_holdout(manifest, observations):
    """Compare paired human ratings on reviewed, previously unseen article hashes."""
    if (manifest.get("schema") != "zen-editorial-holdout-v1" or manifest.get("reviewer") != "owner"
            or manifest.get("blinded") is not True or not manifest.get("experiment")):
        raise ValueError("Holdout needs an identified experiment and blinded owner review")
    seen = {digest for row in observations for digest in (row["source_sha256"], row["reviewed_sha256"])}
    learned_slugs = {row["slug"] for row in observations}
    test_slugs = set()
    genres = {channel: Counter() for channel in ACTIVE}
    for sample in manifest["samples"]:
        slug, channel = sample["slug"], sample["channel"]
        base, revised = sample["baseline"], sample["candidate"]
        hashes = (base["text_sha256"], revised["text_sha256"])
        if (channel not in ACTIVE or slug in test_slugs or slug in learned_slugs or hashes[0] == hashes[1]
                or not all(re.fullmatch(r"[0-9a-f]{64}", h) and h not in seen for h in hashes)):
            raise ValueError("Holdout overlaps exposed cases or contains duplicate versions")
        seen.update(hashes)
        test_slugs.add(slug)
        axes = {"facts", "language", "originality", "story_logic"}
        baseline, candidate = base["defects"], revised["defects"]
        if (baseline.keys() != axes or candidate.keys() != axes
                or any(type(n) is not int or n < 0 for n in (*baseline.values(), *candidate.values()))):
            raise ValueError("Use nonnegative owner-reviewed defect counts on each quality axis")
        counts = genres[channel]
        counts["pairs"] += 1
        counts["improved"] += sum(candidate.values()) < sum(baseline.values())
        counts["regressed"] += any(candidate[k] > baseline[k] for k in axes)
        for axis in sorted(axes):
            counts[f"baseline_{axis}"] += baseline[axis]
            counts[f"candidate_{axis}"] += candidate[axis]
    if not test_slugs:
        raise ValueError("Holdout must contain reviewed article pairs")
    return {"experiment": manifest["experiment"], "genres": {k: dict(v) for k, v in genres.items()},
            "regression_detected": any(v["regressed"] for v in genres.values()),
            "deployment_approval_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", nargs=2, metavar=("CASE_JSON", "OWNER_FEEDBACK_JSON"),
                        type=Path, action="append", required=True)
    parser.add_argument("--on-request", action="store_true")
    parser.add_argument("--provider", type=Path, help="Local LLM for advisory prompt suggestions")
    parser.add_argument("--holdout", type=Path, help="Blinded independent owner-reviewed comparison")
    args = parser.parse_args()
    try:
        policy = json.loads((ROOT / "config/agent_learning.json").read_text(encoding="utf-8"))
        if policy.get("enabled") is not True or policy.get("mode") != "collect_observations_and_propose_changes":
            raise ValueError("Editorial learning is disabled")
        observations = []
        for case, feedback in args.review:
            observations.extend(reviewed_observations(json.loads(case.read_text(encoding="utf-8")),
                                                       json.loads(feedback.read_text(encoding="utf-8"))))
        result = report(observations, on_request=args.on_request)
        if args.provider:
            provider = command_provider(args.provider)
            categories = {genre: row["proposals"] for genre, row in result["genres"].items() if row["proposals"]}
            if categories:
                try:
                    suggested = provider("learning_proposals", {
                        "recurring_issues": categories,
                        "instruction": ("Предложи точные улучшения промптов по подтверждённым итогам. "
                                        "Не меняй пороги, веса, правила публикации или лимиты правок. "
                                        "Верни proposals с category, instruction и rationale."),
                    })
                    eligible = {p["category"] for items in categories.values() for p in items}
                    result["llm_suggestions"] = {"status": "advisory",
                                                 "proposals": [p for p in suggested["proposals"]
                                                               if p["category"] in eligible],
                                                 "provider": provider.identity}
                except Pending as exc:
                    result["llm_suggestions"] = {"status": exc.status, "reason": str(exc)}
                except (KeyError, TypeError) as exc:
                    result["llm_suggestions"] = {"status": "provider_error", "reason": type(exc).__name__}
        if args.holdout:
            result["holdout"] = evaluate_holdout(json.loads(args.holdout.read_text(encoding="utf-8")), observations)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
