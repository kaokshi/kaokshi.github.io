#!/usr/bin/env python3
"""Read-only, fail-closed AI-style checks for editorial drafts.

Never modifies article status, permissions, release flags, or site configuration.
Outputs are diagnostic estimates, not proof of authorship or publication approval.
"""
import argparse
import hashlib
import math
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from statistics import mean
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content" / "articles"
OUTPUT = ROOT / "editorial-check-reports"


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"p", "div", "br", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")


def extract_text(html):
    parser = TextExtractor()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def selected_articles(slug=None):
    files = sorted(CONTENT.glob("*.json"))
    if slug:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
            raise ValueError("Unsafe slug")
        files = [p for p in files if p.stem == slug or json.loads(p.read_text(encoding="utf-8")).get("slug") == slug]
        if not files:
            raise ValueError("Article not found: " + slug)
    return files


def tool_result(argv, text_path, timeout=240):
    try:
        p = subprocess.run(
            [*argv, str(text_path)], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:300]}
    if p.returncode != 0:
        return {"status": "error", "exit_code": p.returncode, "reason": p.stderr[-1000:] or p.stdout[-1000:]}
    try:
        payload = json.loads(p.stdout)
        return {"status": "completed", "data": payload}
    except json.JSONDecodeError:
        return {"status": "error", "reason": "CLI did not return JSON", "output_excerpt": p.stdout[:350]}



MODEL_A = ("modernbert", "lightgbm", "transformer")
REQUIRED_CHECKS = (*MODEL_A, "ai_accent_detector")
REVIEW_THRESHOLD = 80.0

def normalized_aiw_result(argv, text_path):
    """aiw-ru 2.5.2 returns probability in [0, 1]; require valid whole-doc scores."""
    result = tool_result(argv, text_path)
    if result.get("status") != "completed":
        return result
    data = result.get("data")
    value = data.get("probability") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("skipped") is True
            or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        return {"status": "error", "reason": "aiw_whole_document_probability_missing_or_invalid"}
    data["score_percent"] = float(100 * Decimal(str(value)))
    return result

def threshold_review(results):
    """Owner-approved >80 diagnostic REVIEW flag, not a calibrated AI percentage."""
    diagnostics = {}
    for name in MODEL_A:
        item = results.get(name, {})
        data = item.get("data") if isinstance(item, dict) else None
        value = data.get("score_percent") if isinstance(data, dict) else None
        complete = (item.get("status") == "completed"
                    and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and 0 <= value <= 100)
        diagnostics[name] = {
            "status": item.get("status", "unavailable"),
            "index_0_100": value if complete else None,
            "unit": "uncalibrated_model_diagnostic",
        }
    item = results.get("ai_accent_detector", {})
    data = item.get("data") if isinstance(item, dict) else None
    accent = data.get("ai_accent_score") if isinstance(data, dict) else None
    accent_ok = (item.get("status") == "completed"
                 and isinstance(accent, (int, float)) and not isinstance(accent, bool)
                 and math.isfinite(accent) and 0 <= accent <= 1)
    diagnostics["ai_accent_detector"] = {
        "status": item.get("status", "unavailable"),
        "index_0_100": round(accent * 100, 2) if accent_ok else None,
        "unit": "style_signal_not_authorship_probability",
    }
    values = [diagnostics[name]["index_0_100"] for name in MODEL_A]
    complete = accent_ok and all(v is not None for v in values)
    index = mean(values) if complete else None
    return {
        "status": "completed" if complete else "incomplete",
        "primary_model": "model_a",
        "checks": diagnostics,
        "model_a_index_0_100": index,
        "calculation": "arithmetic_mean_of_three_correlated_uncalibrated_classifiers",
        "review_if_above": REVIEW_THRESHOLD,
        "exceeds_owner_review_threshold": index > REVIEW_THRESHOLD if complete else None,
        "needs_rewrite_based_on_ai": None,
        "calibrated_ai_probability": None,
        "note": ("Above 80 requires editorial examination and an attempted substantive "
                 "improvement when defects exist. This index is not percent AI authorship; "
                 "a score alone cannot verify a defect or authorize publication."),
    }

def evaluate(path):
    article = json.loads(path.read_text(encoding="utf-8"))
    return evaluate_article(article, source_file=str(path.relative_to(ROOT)), fallback_slug=path.stem)


def evaluate_article(article, *, source_file=None, fallback_slug=None):
    article_text = extract_text(article.get("body_html", ""))
    if not article_text:
        raise ValueError("Empty article text")
    results = {}
    with tempfile.TemporaryDirectory(prefix="editorial-ai-") as work:
        document = Path(work) / "draft.txt"
        document.write_text(article_text, encoding="utf-8")
        for model in MODEL_A:
            results[model] = normalized_aiw_result(
                ["aiw-ru", "classify", "--model", model, "--json"], document)
        results["ai_accent_detector"] = tool_result(
            ["ai-accent-detector", "--json", "--lang", "ru"], document)
    review = threshold_review(results)
    return {
        "schema": "editorial-ai-check-model-a-v2",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "article_slug": article.get("slug", fallback_slug),
        "channel": article.get("channel"),
        "text_sha256": hashlib.sha256(article_text.encode("utf-8")).hexdigest(),
        "characters_without_spaces": len(re.sub(r"\s", "", article_text)),
        "checks": results,
        "editorial_threshold": review,
        "all_checks_completed": review["status"] == "completed",
        "editorial_approved": False,
        "publication_authorized": False,
        "notice": "Unvalidated score, not an AI-authorship percentage; this report never authorizes publication.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", action="append", help="Existing article slug; repeat to check multiple files")
    parser.add_argument("--output", default=str(OUTPUT), help="Report directory, not part of site output")
    args = parser.parse_args()
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    if args.slug:
        articles = list(dict.fromkeys(path for slug in args.slug for path in selected_articles(slug)))
    else:
        articles = selected_articles()
    for article in articles:
        result = evaluate(article)
        destination = target / (article.stem + ".json")
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.append({"article": article.stem, "report": destination.name, "checks_completed": result["all_checks_completed"], "threshold_passed": None})
        print(article.stem, "completed", sum(x["status"] == "completed" for x in result["checks"].values()), "/4; model_a", result["editorial_threshold"]["model_a_index_0_100"])
    (target / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if any(not entry["checks_completed"] for entry in manifest):
        raise SystemExit("One or more tools failed; report is incomplete (no approval granted).")


if __name__ == "__main__":
    main()
