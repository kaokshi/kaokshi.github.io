"""Diagnose verifiable defects and apply only separately justified paragraph edits."""

from html import escape, unescape

from editorial_agent_v1 import article_digest
from editorial_length import MIN_CHARS, MAX_CHARS, count_characters, length_distance
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
        genre = ("Для мистики отдельно проверь, не прерывают ли сюжет псевдонаучные ссылки "
                 "и авторские объяснения сюжета. Для научпопа отметь неподтверждённые факты.")
        try:
            response = provider("diagnose", {
                "title": article["title"], "channel": article["channel"],
                "text_sha256": digest, "paragraphs": blocks,
                "instruction": ("Отметь только конкретные смысловые или языковые проблемы. "
                                "Проверь начало, хронологию, логику, пустые абзацы, "
                                "естественность и необоснованные утверждения. Верни issues "
                                "с id, category, paragraph, quote и reason. Цитируй "
                                f"точно. Категории: {', '.join(sorted(CATEGORIES))}. {genre}"),
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
                            "пустого абзаца укажи remove: true и пустой after. "
                            "Дай одну сводную правку на абзац."),
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
    return candidate, changes


def resize_article(article, provider, documents=()):
    html = article["body_html"]
    count = count_characters(html)
    blocks = paragraphs(article)
    spans = PlainParagraphs(html).paragraphs
    if not spans:
        raise ValueError("No editable paragraphs for length adjustment")
    expand = count < MIN_CHARS
    direction = "expand" if expand else "trim"
    target = (MIN_CHARS + 400 - count) if expand else (count - MAX_CHARS + 400)
    instruction = (
        f"Добавь перед концовкой самостоятельный фрагмент примерно на {target} символов "
        "без пробелов. Для мистики разработай действие и мотивацию, для науки углуби объяснение "
        "только по приложенным источникам. Сохраняй голос автора. Верни paragraphs."
        if expand else
        f"Сократи примерно на {target} символов без пробелов. Сжимай повторы и отступления, "
        "сохрани ключевые факты и события, развязку и подпись. Верни replacements "
        "с paragraph и after. Для удаления абзаца передай пустой after."
    )
    output = provider("revise", {
        "mode": "resize", "direction": direction, "instruction": instruction,
        "text_sha256": article_digest(article), "title": article["title"], "channel": article["channel"],
        "paragraphs": blocks, "characters": count,
        "documents": [{**doc, "text": doc["text"][:4500]} for doc in documents[:3]],
    })
    if expand:
        additions = output["paragraphs"]
        if (not isinstance(additions, list) or not additions
                or not all(isinstance(p, str) and p.strip() for p in additions)):
            raise ValueError("Length expansion needs substantive paragraphs")
        old = {" ".join(block["text"].split()).casefold() for block in blocks}
        normalized = {" ".join(p.split()).casefold() for p in additions}
        if len(normalized) != len(additions) or old.intersection(normalized):
            raise ValueError("Expansion would duplicate existing text")
        pos = spans[-1][0]
        fragment = "".join(f"<p>{escape(p.strip(), quote=False)}</p>\n" for p in additions)
        candidate = {**article, "body_html": html[:pos] + fragment + html[pos:]}
    else:
        edits = []
        for row in output["replacements"]:
            number = row["paragraph"]
            if type(number) is not int or not 1 <= number < len(blocks):
                raise ValueError("Invalid reduction target or attempted removal of the ending")
            edits.append({"paragraph": number, "before": blocks[number - 1]["text"],
                          "after": row["after"], "remove": row["after"] == "",
                          "reason": "Сокращён фрагмент для соблюдения редакционного объёма."})
        candidate, _ = assistant_edit(article, {"base_text_sha256": article_digest(article),
                                                 "edits": edits}, allow_remove=True)
    after = count_characters(candidate["body_html"])
    if length_distance(after) >= length_distance(count):
        raise ValueError(f"Length repair did not bring the article closer to the range ({count} → {after})")
    return candidate, [f"Объём статьи приведён ближе к норме {MIN_CHARS}–{MAX_CHARS}: {count} → {after}."]
