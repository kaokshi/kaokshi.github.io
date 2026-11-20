"""Diagnose verifiable defects and apply only separately justified paragraph edits."""

from html import unescape
import re

from editorial_agent_v1 import article_digest
from editorial_ai_check import extract_text
from editorial_quality import MIN_CHARS
from editorial_revision import PlainParagraphs, assistant_edit
from editorial_writer import Pending

CATEGORIES = {
    "logic", "opening", "filler", "natural_language", "chronology", "unsupported_claim",
}


def paragraphs(article):
    return [{"paragraph": i, "text": unescape(fragment)}
            for i, (_, _, fragment) in enumerate(PlainParagraphs(article["body_html"]).paragraphs, 1)]


def diagnose(article, provider, reviewed=None):
    digest = article_digest(article)
    blocks = paragraphs(article)
    if reviewed is not None and reviewed.get("text_sha256") == digest:
        response = reviewed
        origin = "operator"
    else:
        origin = "model"
        try:
            response = provider("diagnose", {
                "title": article["title"], "channel": article["channel"],
                "text_sha256": digest, "paragraphs": blocks,
                "instruction": ("Отметь только конкретные смысловые или языковые проблемы. "
                                "Проверь начало, хронологию, логику, пустые абзацы, "
                                "естественность и необоснованные утверждения. Верни issues "
                                "с id, category, paragraph, quote и reason. Цитируй "
                                f"точно. Категории: {', '.join(sorted(CATEGORIES))}."),
            })
        except Pending as exc:
            return {"status": exc.status, "reason": str(exc), "text_sha256": digest, "issues": []}
    try:
        issues = valid_issues(blocks, response.get("issues"))
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "invalid_diagnosis", "reason": str(exc), "text_sha256": digest, "issues": []}
    return {"status": "completed", "origin": origin, "text_sha256": digest, "issues": issues}


def valid_issues(blocks, issues):
    if not isinstance(issues, list):
        raise ValueError("Diagnosis requires an issues list")
    ids = set()
    for issue in issues:
        key, number, quote, reason = (issue[k] for k in ("id", "paragraph", "quote", "reason"))
        if (not isinstance(key, str) or not key.strip() or key in ids
                or type(number) is not int or not 1 <= number <= len(blocks)
                or not all(isinstance(s, str) and s.strip() for s in (quote, reason))
                or quote not in blocks[number - 1]["text"] or issue["category"] not in CATEGORIES):
            raise ValueError("Issue needs a unique id, recognized category, exact quote and reason")
        ids.add(key)
    return issues


def revise(article, issues, provider, reviewed=None):
    if not issues:
        return None
    digest = article_digest(article)
    blocks = paragraphs(article)
    valid_issues(blocks, issues)
    if reviewed is not None and reviewed.get("text_sha256") == digest:
        response = reviewed
    else:
        response = provider("revise", {
            "title": article["title"], "channel": article["channel"], "text_sha256": digest,
            "paragraphs": blocks, "issues": issues,
            "instruction": ("Редактируй только абзацы из замечаний. Сохрани голос автора, "
                            "факты и смысл. Для спорных фактов опирайся на приложенное "
                            "обоснование и избегай новых утверждений. Верни edits с "
                            "issue_id, paragraph, before, after, reason. Для удаления "
                            "пустого абзаца укажи remove: true и пустой after."),
        })
    index = {i["id"]: i for i in issues}
    edits = response.get("edits")
    if edits == []:
        return None
    if not isinstance(edits, list):
        raise ValueError("Revision requires an edits list")
    for edit in edits:
        issue = index.get(edit.get("issue_id"))
        if issue is None or issue["paragraph"] != edit.get("paragraph"):
            raise ValueError("Every edit must target a diagnosed issue")
        if edit.get("remove") is True and issue["category"] != "filler":
            raise ValueError("Only diagnosed filler can be removed as a whole paragraph")
    candidate, changes = assistant_edit(article, {"base_text_sha256": digest, "edits": edits}, allow_remove=True)
    chars = lambda draft: len(re.sub(r"\s", "", extract_text(draft["body_html"])))
    if chars(candidate) < min(MIN_CHARS, chars(article)):
        raise ValueError("Revision would reduce the draft below the minimum length")
    return candidate, changes
