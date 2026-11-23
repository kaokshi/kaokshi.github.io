"""Immutable article versions, source evidence, and checks on canonical visible text."""
from collections import Counter
from html.parser import HTMLParser
import re
from threading import Lock

from .editorial_ai_check import measure_text, threshold_review
from .editorial_length import HARD_MAX, MIN_CHARS, check_length
from .editorial_language_tool import serialize_match
from .editorial_memory import digest
from .editorial_no_repeat import normalize, words
from .editorial_providers import Blocked, ProviderError, check_url, request
from .editorial_quality import text_style

HEADING = re.compile(r"\*\*([^*\n]{2,125})\*\*")
CTA = re.compile(
    r"\b(?:подпиш(?:итесь|ись)|подписывай(?:тесь|ся)|подписаться|"
    r"следите за (?:каналом|новыми (?:историями|выпусками|статьями))|"
    r"присоединяйтесь к (?:нашему )?каналу|оформ(?:ите|ить) подписку)\b", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text):
    return {token.replace(",", ".") for token in NUMBER.findall(text)}
RULES = (
    "Русская статья, 3000–6000 непробельных знаков, ориентир 4300–4900. "
    "Только при конкретной необходимости раскрыть материал можно написать 6001–7000 знаков. "
    "В этом случае после статьи добавь одну служебную строку "
    "<!-- length_exception_reason: конкретная причина -->. Строка в текст статьи не входит. "
    "Строго 6–7 смысловых разделов с отдельными жирными заголовками **такого вида** "
    "и коротким вступлением перед первым разделом (без заголовка). "
    "Каждый заголовок раздела обязан сопровождаться сразу следующей за ним иллюстрацией; "
    "отдельная лучшая обложка идёт сразу после оглавления и перед вступлением. "
    "При написании текста не вставляй ссылки на изображения или элементы оглавления: "
    "экспортёр добавит их после проверки, без лишних пустых абзацев. "
    "Заголовок всей статьи перед разделами не добавляй. "
    "В исходном тексте абзацы отделяй пустой строкой только для разбора структуры. "
    "Закончи естественным призывом "
    "подписаться. Кроме строки обоснования объёма возвращай только текст статьи."
)


LENGTH_NOTE = re.compile(r"\s*<!--\s*length_exception_reason:[ \t]*([^<>\n]*?)\s*-->\s*\Z")


def parse_article(raw):
    note = LENGTH_NOTE.search(raw)
    reason = None
    if note:
        raw = raw[:note.start()]
        reason = note.group(1).strip() or None
    sections = []
    intro = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        paragraph = []
        for line in block.splitlines():
            heading = HEADING.fullmatch(line.strip())
            if heading:
                if paragraph:
                    (sections[-1]["paragraphs"] if sections else intro).append(" ".join(paragraph))
                    paragraph.clear()
                sections.append({"heading": heading[1].strip(), "paragraphs": []})
            elif line.strip():
                paragraph.append(line.strip())
        if paragraph:
            (sections[-1]["paragraphs"] if sections else intro).append(" ".join(paragraph))
    return {"sections": sections, "intro": intro, "length_exception_reason": reason}


def canonical(article):
    lines = list(article["intro"])
    for section in article["sections"]:
        lines.extend([section["heading"], *section["paragraphs"]])
    return "\n".join(lines)


class ExtractSource(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "noscript", "header", "svg"}:
            self.hidden += 1
        if tag in {"p", "li", "h1", "h2", "h3", "article"}:
            self.stack.append(tag)
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "noscript", "header", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if self.stack and tag == self.stack[-1]:
            self.stack.pop()
            self.parts.append(" ")

    def handle_data(self, value):
        if self.stack and not self.hidden:
            self.parts.append(value)


def sources(urls, domains):
    records = []
    for url in dict.fromkeys(urls[:5]):
        try:
            check_url(url, domains)
            raw = request(url, domains, timeout=12, limit=2_500_000)
            parser = ExtractSource()
            parser.feed(raw.decode("utf-8", "replace"))
            text = " ".join("".join(parser.parts).split())[:15_000]
            if len(text) < 400:
                raise ProviderError("source_text_missing")
        except (ProviderError, TypeError, ValueError) as exc:
            records.append({"url": str(url), "status": "unavailable",
                            "reason": str(exc) if isinstance(exc, ProviderError) else type(exc).__name__})
            continue
        records.append({"url": url, "status": "accessible", "body_sha256": digest(raw),
                        "text": text})
    return records


def source_context(records):
    available = [(i, row) for i, row in enumerate(records, 1) if row["status"] == "accessible"][:3]
    return "\n\n".join(f"S{i} {row['url']}\n{row['text'][:2000]}" for i, row in available)


def fact_evidence(text, audit, records):
    verified = []
    issues = []
    claims = audit.get("claims")
    if not isinstance(claims, list) or not claims:
        return [], ["fact_evidence_missing"]
    for row in claims:
        try:
            claim = row["claim"].strip()
            source = row["source"]
            quote = row["quote"].strip()
            evidence = records[int(re.fullmatch(r"S([1-5])", source)[1]) - 1]
            ok = (len(claim) > 10 and claim in text and len(quote) >= 18
                  and evidence["status"] == "accessible"
                  and normalize(quote) in normalize(evidence["text"])
                  and numbers(claim) <= numbers(quote)
                  and row.get("verdict") == "supported")
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            claim, ok = str(row)[:100], False
        if ok:
            verified.append({"claim": claim, "source": evidence["url"], "quote": quote})
        else:
            issues.append("unsupported_claim: " + claim)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        values = numbers(sentence)
        supported = {value for row in verified if row["claim"] in sentence or sentence.strip() in row["claim"]
                     for value in numbers(row["claim"])}
        if values - supported:
            issues.append("unsupported_numeric_claim: " + sentence.strip()[:240])
    return verified, issues


def structural_checks(article, history):
    text = canonical(article)
    sections = article["sections"]
    paragraphs = [p for section in sections for p in section["paragraphs"]]
    reason = article.get("length_exception_reason")
    chars, issue = check_length(text, reason)
    problems = []
    if issue:
        problems.append(f"article_length: {chars} ({issue})")
    # The opening image comes before the intro in the exported artifact.
    # The original prose may contain a compact pre-heading intro.
    if (not 6 <= len(sections) <= 7 or len(article["intro"]) > 2
            or any(not section["paragraphs"] for section in sections)):
        problems.append("section_structure")
    if not paragraphs or not CTA.search(paragraphs[-1]):
        problems.append("missing_cta")
    duplicates = [p for p, count in Counter(normalize(p) for p in paragraphs).items()
                  if count > 1]
    if duplicates:
        problems.append("repeated_paragraphs: " + " / ".join(p[:70] for p in duplicates))
    terms = words(text)
    similar = [title for title, old in history if terms and old
               and len(terms & old) / len(terms | old) > .52]
    if similar:
        problems.append("archive_overlap: " + " / ".join(similar[:4]))
    return {"chars_without_spaces": chars, "section_count": len(sections),
            "length_exception_reason": reason,
            "issues": problems, "style": text_style(text)}


LANGUAGE_LOCK = Lock()


def grammar(text):
    try:
        import language_tool_python
        with LANGUAGE_LOCK, language_tool_python.LanguageTool("ru") as tool:
            return {"status": "completed", "issues": [serialize_match(x) for x in tool.check(text)]}
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}


def qa(raw, channel, history, records, editorial_audit, left):
    article = parse_article(raw)
    text = canonical(article)
    checks = structural_checks(article, history)
    detectors = measure_text(text) if text.strip() and left() >= 16 * 60 else {}
    for name, result in detectors.items():
        if result.get("status") != "completed":
            detectors[name] = {**result, "status": "unavailable"}
    language = grammar(text) if text.strip() and left() >= 6 * 60 else {
        "status": "unavailable", "reason": "deadline_or_empty"}
    if text and left() >= 150:
        try:
            verdict = editorial_audit(text, language.get("issues", []))
            if not isinstance(verdict.get("issues"), list) or any(
                    not isinstance(x, str) or not x.strip() for x in verdict["issues"]):
                raise Blocked("invalid_editorial_audit")
            editorial = {"status": "completed", "issues": verdict["issues"][:15]}
        except Blocked as exc:
            verdict = {}
            editorial = {"status": "unavailable", "reason": str(exc)}
    else:
        verdict = {}
        editorial = {"status": "unavailable", "reason": "deadline_or_empty"}
    facts, fact_issues = fact_evidence(text, verdict, records) if channel in {"science", "cinema"} else ([], [])
    fact_status = ("not_applicable" if channel == "legends" else "unavailable"
                   if editorial["status"] != "completed" else "findings" if fact_issues else "completed")
    threshold = threshold_review(detectors)
    return {**checks, "article": article, "text_sha256": digest(text), "detectors": detectors,
            "model_a": threshold, "grammar": language, "editorial": editorial,
            "facts": {"status": fact_status, "evidence": facts, "issues": fact_issues},
            "issues": checks["issues"] + editorial.get("issues", [])
            + (fact_issues if fact_status == "findings" else [])}


def improves(previous, candidate):
    """Preserve passing dimensions, require fewer problems or a closer length."""
    if previous["text_sha256"] == candidate["text_sha256"]:
        return False
    types = lambda report: Counter(issue.partition(":")[0] for issue in report["issues"])
    before, after = types(previous), types(candidate)
    if after - before:
        return False
    if any(previous[key]["status"] == "completed" and candidate[key]["status"] != "completed"
           for key in ("editorial", "grammar", "facts", "model_a")):
        return False
    if sum(after.values()) < sum(before.values()):
        return True
    if before["article_length"]:
        distance = lambda n: max(MIN_CHARS - n, n - HARD_MAX, 0)
        return distance(candidate["chars_without_spaces"]) < distance(previous["chars_without_spaces"])
    return False
