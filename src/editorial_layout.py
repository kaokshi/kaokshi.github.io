"""Semantic sections and lossless formatting of the writer's paragraph stream."""
from html import escape
import re
from xml.etree import ElementTree as ET

from editorial_length import extract_text

SECTION_SCHEMA = "zen-sections-v1"
GENERIC = re.compile(r"(?:введение|вступление|заключение|основная часть|итоги|(?:блок|раздел|глава|часть)\s*[\divx]*)", re.I)


def parse_sections(body):
    try:
        root = ET.fromstring("<article>" + body + "</article>")
    except ET.ParseError as exc:
        raise ValueError("Invalid section markup") from exc
    if (root.text or "").strip() or len(root) < 2:
        raise ValueError("Article needs multiple semantic sections")
    names = set()
    paragraphs = 0
    for section in root:
        if (section.tag != "section" or (section.text or "").strip() or (section.tail or "").strip()
                or len(section) < 2 or section[0].tag != "h2"):
            raise ValueError("Each section must begin with its own heading")
        heading = section[0]
        if (section.attrib or heading.attrib or len(heading) != 1 or heading[0].attrib or heading[0].tag != "strong" or len(heading[0])
                or (heading.text or "").strip() or (heading[0].tail or "").strip()):
            raise ValueError("Use h2 with a bold heading")
        name = " ".join(re.findall(r"\w+", (heading[0].text or "").casefold()))
        if not name or name in names or GENERIC.fullmatch(name) or re.match(r"(?:призыв к |подпи(?:сывай|шис|шите|ска на))", name):
            raise ValueError("Empty, repeated or generic heading")
        names.add(name)
        if any((node.tail or "").strip() for node in section):
            raise ValueError("Text outside a section paragraph")
        if any(node.tag not in {"p", "figure"} for node in section[1:]):
            raise ValueError("Unexpected section content")
        prose = [node for node in section if node.tag == "p"]
        if not prose or any(not "".join(p.itertext()).strip() for p in prose):
            raise ValueError("Section needs prose")
        paragraphs += len(prose)
    if len(root) > (paragraphs + 1) // 2:
        raise ValueError("Group related paragraphs into sections")
    return root


def format_sections(paragraphs, starts):
    if not isinstance(starts, list) or not starts:
        raise ValueError("Missing semantic section plan")
    numbers = [row["start"] for row in starts]
    if (any(type(n) is not int for n in numbers) or numbers[0] != 1
            or numbers != sorted(set(numbers)) or numbers[-1] >= len(paragraphs)):
        raise ValueError("Invalid section boundaries")
    sections = []
    for row, stop in zip(starts, numbers[1:] + [len(paragraphs) + 1]):
        title = row["heading"]
        if not isinstance(title, str):
            raise ValueError("Invalid heading")
        body = "".join(f"<p>{escape(p, quote=False)}</p>" for p in paragraphs[row["start"]-1:stop-1])
        sections.append(f"<section><h2><strong>{escape(title.strip())}</strong></h2>{body}</section>")
    html = "\n".join(sections)
    parse_sections(html)
    return html


def arrange(paragraphs, plan, provider, title):
    try:
        return format_sections(paragraphs, plan)
    except (KeyError, TypeError, ValueError):
        content = list(paragraphs)
        while content and re.match(r"(?:cta\s*:\s*)?подпи(?:сывай|шис|шите)", content[-1], re.I):
            content.pop()
        retry = provider("section_plan", {"title": title, "paragraphs": content,
            "instruction": "Сгруппируй абзацы по смыслу. Верни sections с полями start (номер первого "
                           "абзаца от 1) и heading (конкретный живой подзаголовок). Начни с 1. "
                           "Сделай 2–3 раздела по несколько абзацев, не больше четырёх. Заголовок — короткая конкретная деталь из текста, без двоеточия, слов о структуре и призывов. Не придумывай факты. Оставь подписку в финальном разделе."})
        return format_sections(paragraphs, retry["sections"])


def docx_body(doc, article, assets):
    root = parse_sections(article["body_html"])
    if article.get("image_files") or list(root.iter("figure")):
        from editorial_cover import verify_cover
        verify_cover(article, assets)
    for section in root:
        heading = doc.add_heading(level=2)
        heading.add_run(section[0][0].text).bold = True
        for node in section[1:]:
            if node.tag == "figure":
                from docx.shared import Inches
                file = node.find("img").attrib["src"]
                doc.add_picture(str(assets / ("content" + file)), width=Inches(5.8))
            else:
                doc.add_paragraph("".join(node.itertext()))


def markdown_body(body):
    if "<section>" not in body:
        return "\n\n".join(line.strip() for line in extract_text(body).splitlines() if line.strip())
    return "\n\n".join(line for section in parse_sections(body) for line in
        ["## **" + section[0][0].text + "**", *("".join(p.itertext()) for p in section.findall("p"))])


def repair_sections(article, provider):
    """Recover headings without changing prose or losing existing illustrations."""
    body = article["body_html"]
    try:
        parse_sections(body)
        return article
    except ValueError:
        pass
    try:
        root = ET.fromstring("<article>" + body + "</article>")
        for heading in root.findall("./section/h2"):
            if not len(heading) and (heading.text or "").strip():
                text = heading.text
                heading.text = None
                ET.SubElement(heading, "strong").text = text
        repaired = "".join(ET.tostring(section, encoding="unicode") for section in root)
        parse_sections(repaired)
        return {**article, "layout_schema": SECTION_SCHEMA, "body_html": repaired}
    except (ET.ParseError, ValueError):
        pass
    from editorial_revision import PlainParagraphs
    from html import unescape
    spans = PlainParagraphs(body).paragraphs
    remainder = body
    for start, end, _ in reversed(spans):
        remainder = remainder[:start] + remainder[end:]
    remainder = re.sub(r"</?section>|<h2>.*?</h2>", "", remainder, flags=re.S)
    if not spans or remainder.strip():
        raise ValueError("Section repair needs editable prose; preserve unsupported markup for review")
    paragraphs = [unescape(raw) for _, _, raw in spans]
    return {**article, "layout_schema": SECTION_SCHEMA,
            "body_html": arrange(paragraphs, None, provider, article["title"])}


def layout_findings(article, assets):
    try:
        parse_sections(article["body_html"])
    except (KeyError, ValueError):
        return ["section_headings_invalid", "cover_selection_pending"]
    from editorial_cover import verify_cover
    try:
        verify_cover(article, assets)
    except (KeyError, TypeError, IndexError, OSError, ValueError):
        return ["cover_selection_pending"]
    return []
