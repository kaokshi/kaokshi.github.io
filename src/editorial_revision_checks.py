"""Run the repository's draft checks on a single in-memory article version."""

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from editorial_ai_check import CONTENT, ROOT, evaluate_article, extract_text
from editorial_agent_v1 import article_digest, validate_ai_report
from editorial_language_tool import serialize_match
from editorial_quality import image_hash_audit, inspect_draft, similarity_report
from editorial_reader_interest import analyze as reader_interest_analyze


def command_report(argv, accepted_exit_codes):
    try:
        result = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
        )
        if result.returncode not in accepted_exit_codes:
            return {"status": "error", "exit_code": result.returncode}
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return {"status": "completed", "data": data, "exit_code": result.returncode}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "reason": type(exc).__name__}


def grammar_report(text, language_tool):
    try:
        return {"status": "completed",
                "issues": [serialize_match(match) for match in language_tool(text)]}
    except Exception as exc:
        # Missing Java/packages, startup failures and invalid matches are all incomplete.
        return {"status": "error", "reason": type(exc).__name__}


def check_draft(article, language_tool, *, source_urls=()):
    text = extract_text(article["body_html"])
    draft = inspect_draft(article)
    if source_urls:
        draft["urls"] = sorted(set(draft["urls"]) | set(source_urls))
        draft["warnings"] = [w for w in draft["warnings"] if w != "science_sources_not_recorded"]
    # Compare only with other drafts from the active channels.
    corpus = [p for p in CONTENT.glob("*.json")
              if p.name.startswith(("legends-", "science-", "cinema-"))]
    similar = similarity_report(corpus, candidate=article)
    image_audit = image_hash_audit(corpus, candidate=article)
    duplicates = [entry for key in ("duplicates", "sha256_duplicates")
                  for entry in image_audit.get(key, []) if article["slug"] in entry["articles"]]
    unreadable = [entry for entry in image_audit.get("unreadable", [])
                  if entry["slug"] == article["slug"]]
    grammar = grammar_report(text, language_tool)
    reader_interest = reader_interest_analyze(article)
    with TemporaryDirectory(prefix="editorial-quality-") as directory:
        source = Path(directory) / "draft.md"
        source.write_text("# " + article.get("title", "") + "\n\n" + text, encoding="utf-8")
        vale = command_report(["vale", "--config", str(ROOT / ".vale.ini"),
                               "--output=JSON", str(source)], {0, 1})
        if vale["status"] == "completed":
            rows = vale.pop("data")
            if all(isinstance(matches, list) for matches in rows.values()):
                vale["issues"] = [issue for matches in rows.values() for issue in matches]
            else:
                vale = {"status": "error", "reason": "unexpected_report"}
        if draft["urls"]:
            urls = Path(directory) / "links.txt"
            urls.write_text("\n".join(draft["urls"]) + "\n", encoding="utf-8")
            links = command_report(
                ["lychee", "--no-progress", "--format", "json", "--exclude-all-private",
                 "--timeout", "20", str(urls)], {0, 2},
            )
        else:
            links = {"status": "not_applicable", "reason": "no_recorded_links"}

    ai = evaluate_article(article)
    image_check_complete = not article.get("image_files") or (
        image_audit["near_duplicate_status"] == "completed" and not unreadable
    )
    complete = (validate_ai_report(article, ai) is not None and image_check_complete
                and grammar["status"] == vale["status"] == "completed"
                and links["status"] in {"completed", "not_applicable"}
                and draft["style"]["russian_morphology"]["status"] == "completed")
    findings = list(draft["warnings"])
    if similar:
        findings.append("similar_article")
    if duplicates:
        findings.append("duplicate_image")
    if links.get("exit_code") == 2:
        findings.append("unreachable_links")
    return {
        "text_sha256": article_digest(article),
        "automated_checks_completed": complete,
        "ai": ai,
        "quality": draft,
        "reader_interest": reader_interest,
        "grammar": grammar,
        "vale": vale,
        "links": links,
        "similar_articles": similar,
        "image_similarity": {
            "status": "completed" if image_check_complete else "incomplete",
            "duplicates": duplicates, "unreadable": unreadable,
        },
        "findings": findings,
        "manual_checks_pending": [
            ("science_claims" if article["channel"] == "science" else
             "cinema_plot_accuracy_and_spoilers" if article["channel"] == "cinema"
             else "fiction_originality_and_logic"),
            "editorial_style_and_cta", "image_rights",
        ],
    }
