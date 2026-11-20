#!/usr/bin/env python3
"""Read-only, fail-closed AI-style checks for editorial drafts.

Never modifies article status, permissions, release flags, or site configuration.
Outputs are diagnostic estimates, not proof of authorship or publication approval.
"""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
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



def threshold_review(results):
    """Report four existing AI detectors without a ruBERT-only cutoff.

    The result is descriptive. A model score is neither a calibrated percentage
    of AI authorship nor a justified instruction to rewrite prose.
    """
    diagnostics = {}
    for name in ("rubert_v4", "modernbert", "lightgbm", "ai_accent_detector"):
        item = results.get(name, {})
        data = item.get("data") if isinstance(item, dict) else None
        value = data.get("score_percent") if isinstance(data, dict) else None
        if name == "rubert_v4" and isinstance(data, dict):
            value = data.get("whole_document_index_percent", value)
        if name == "ai_accent_detector" and isinstance(data, dict):
            accent = data.get("ai_accent_score")
            if isinstance(accent, (int, float)) and not isinstance(accent, bool) and 0 <= accent <= 1:
                value = round(accent * 100, 4)
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100
        diagnostics[name] = {
            "status": item.get("status", "unavailable") if isinstance(item, dict) else "unavailable",
            "index_0_100": round(value, 2) if valid else None,
            "unit": "uncalibrated_model_diagnostic",
        }
    return {
        "status": "diagnostics_only",
        "primary_model": None,
        "checks": diagnostics,
        "passed": None,
        "limit_percent": None,
        "needs_rewrite_based_on_ai": None,
        "calibrated_ai_probability": None,
        "note": ("No detector has an independent validated rewrite threshold for our genres. "
                 "Do not rewrite solely because of an AI model score."),
    }


def evaluate(path):
    article = json.loads(path.read_text(encoding="utf-8"))
    text = extract_text(article.get("body_html", ""))
    results = {}
    with tempfile.TemporaryDirectory(prefix="editorial-ai-") as work:
        document = Path(work) / "draft.txt"
        document.write_text(text, encoding="utf-8")
        # ruBERT v4 is primary; the three original tools are comparison only.
        rubert_python = ROOT / ".rubert-venv" / "bin" / "python"
        rubert_source = ROOT / ".rubert-upstream"
        results["rubert_v4"] = tool_result(
            [str(rubert_python), str(ROOT / "src" / "rubert_v4_runner.py"), "--source", str(rubert_source)],
            document, timeout=600
        )
        # Both comparison classifiers share the LLMTrace training corpus.
        # They MUST NOT be represented as independent proof of authorship.
        for model in ("modernbert", "lightgbm"):
            results[model] = tool_result(["aiw-ru", "classify", "--model", model, "--json"], document)
        results["ai_accent_detector"] = tool_result(
            ["ai-accent-detector", "--json", "--lang", "ru"], document
        )
    from editorial_auto_route import digest_article
    return {
        "schema": "editorial-ai-check-v1",
        "article_sha256": digest_article(article),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(path.relative_to(ROOT)),
        "article_slug": article.get("slug", path.stem),
        "channel": article.get("channel"),
        "characters_without_spaces": len(re.sub(r"\s", "", text)),
        "checks": results,
        "editorial_threshold": threshold_review(results),
        "all_checks_completed": all(x["status"] == "completed" for x in results.values()),
        "editorial_approved": False,
        "publication_authorized": False,
        "notice": "Models may be wrong. ModernBERT and LightGBM share training data. This report never authorizes publication.",
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
        print(f'{article.stem}: {sum(x["status"] == "completed" for x in result["checks"].values())}/4 tools completed')
    (target / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if any(not entry["checks_completed"] for entry in manifest):
        raise SystemExit("One or more tools failed; report is incomplete (no approval granted).")


if __name__ == "__main__":
    main()
