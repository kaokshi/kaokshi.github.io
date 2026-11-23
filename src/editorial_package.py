"""Readable owner review packages with round-trip checks on text and images."""
from collections import Counter
from html import escape, unescape
from xml.sax.saxutils import escape as xml_escape
from html.parser import HTMLParser
import io
from zipfile import ZIP_DEFLATED, ZipFile

from .editorial_memory import digest, write, write_json
from .editorial_review import canonical
from .editorial_scene_qa import require_packet_rows, require_ready_rows
from .editorial_images import select_cover
from .editorial_layout import layout_issues
from .editorial_providers import Blocked
from .editorial_subscription import approved_url, channel_from_folder, split_cta


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.pending = None
        self.lines = []
        self.images = []
        self.all_images = []
        self.extra_media = False
        self.placement = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.inside = True
        if self.inside and tag in {"h2", "p"}:
            self.placement.append(tag)
            self.pending = []
        if tag in {"source", "object", "embed", "svg", "iframe"} or "srcset" in dict(attrs):
            self.extra_media = True
        if tag == "img":
            image = dict(attrs).get("src")
            self.all_images.append(image)
            if self.inside:
                self.images.append(image)
                self.placement.append(image)

    def handle_endtag(self, tag):
        if tag in {"h2", "p"} and self.pending is not None:
            self.lines.append("".join(self.pending))
            self.pending = None
        if tag == "main":
            self.inside = False

    def handle_data(self, text):
        if self.pending is not None:
            self.pending.append(text)


def add_word_hyperlink(paragraph, label, url):
    """Add a real clickable external link to DOCX, preserving paragraph.text."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    relation = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    properties.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)
    run.append(properties)
    node = OxmlElement("w:t")
    node.text = label
    run.append(node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def final_visual_gate(article, rows, source_sha, article_id, read_asset, *, encoded=None):
    """Validate the actual text, files and final documents at every ready boundary."""
    import imagehash
    from PIL import Image, ImageOps

    if not article or digest(canonical(article)) != source_sha:
        raise Blocked("stale_image_needs_recheck")
    require_packet_rows(rows, source_sha, article_id=article_id, article=article)
    require_ready_rows(rows)
    if layout_issues(article, rows):
        raise Blocked("review_visual_layout_incomplete")
    hero = next(row for row in rows if row["is_cover"])
    ordered = [hero, *sorted((row for row in rows if not row["is_cover"]), key=lambda row: row["section"])]
    fingerprints = []
    for row in ordered:
        from pathlib import PurePosixPath
        name = row["file"]
        if PurePosixPath(name).name != name or "\\" in name or not name:
            raise Blocked("invalid_review_asset_name")
        try:
            raw = read_asset(name)
            if digest(raw) != row["sha256"]:
                raise Blocked("review_image_sha_mismatch")
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 22_000_000:
                    raise Blocked("review_image_dimensions_exceed_limit")
                image.load()
                fingerprint = imagehash.phash(ImageOps.exif_transpose(image))
        except (OSError, ValueError, KeyError):
            raise Blocked("review_image_file_missing_or_invalid") from None
        if any(fingerprint - previous <= 6 for previous in fingerprints):
            raise Blocked("review_duplicate_image_pixels")
        fingerprints.append(fingerprint)
    if encoded:
        _verify_exported_visual_order(article, ordered, encoded)


def _verify_exported_visual_order(article, ordered, encoded):
    from docx import Document

    html, word = encoded
    expected_html, expected_word = [f'images/{ordered[0]["file"]}'], [ordered[0]["sha256"]]
    expected_html.extend("p" for _ in article["intro"])
    expected_word.extend("p" for _ in article["intro"])
    for section, picture in zip(article["sections"], ordered[1:], strict=True):
        expected_html.extend(("h2", f'images/{picture["file"]}'))
        expected_word.extend(("h2", picture["sha256"]))
        for _ in section["paragraphs"]:
            expected_html.append("p")
            expected_word.append("p")
    checker = ArticleHTML()
    try:
        checker.feed(html.decode("utf-8"))
        doc = Document(io.BytesIO(word))
        with ZipFile(io.BytesIO(word)) as archive:
            embedded = Counter(digest(archive.read(name)) for name in archive.namelist()
                               if name.startswith("word/media/"))
        actual_word, body = [], []
        for p in doc.paragraphs:
            matches = p._p.xpath(".//a:blip")
            if matches:
                for image in matches:
                    rel = image.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                    actual_word.append(digest(doc.part.related_parts[rel].blob))
            elif p.text and p.style.name in {"Heading 2", "Normal"}:
                actual_word.append("h2" if p.style.name == "Heading 2" else "p")
                body.append(p.text)
    except Exception as exc:
        raise Blocked("review_document_unreadable_" + type(exc).__name__) from None
    if (checker.placement != expected_html or checker.all_images != checker.images or checker.extra_media
            or embedded != Counter(row["sha256"] for row in ordered)
            or actual_word != expected_word or len(doc.inline_shapes) != len(ordered)
            or "\n".join(checker.lines) != canonical(article)
            or "\n".join(body) != canonical(article)):
        raise Blocked("review_document_image_placement_mismatch")


def package(folder, title, version, report, images, image_folder, summary):
    from docx import Document
    from docx.shared import Inches

    article = report["article"]
    ready = summary.get("status") in {"review_pending", "rights_review_pending"}
    require_packet_rows(images, report["text_sha256"], article_id=folder.name, article=article)
    if ready:
        final_visual_gate(article, images, report["text_sha256"], folder.name,
                          lambda name: (image_folder / name).read_bytes())
    folder.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading(title, 0)
    problems = layout_issues(article, images)
    if summary.get("status") in {"review_pending", "rights_review_pending"}:
        require_ready_rows(images)
        if problems:
            raise Blocked("review_visual_layout_incomplete")
    cover = select_cover(images)
    hero = next((row for row in images if row["file"] == cover), None)
    ordered = ([hero] if hero else []) + sorted(
        (row for row in images if row["file"] != cover),
        key=lambda row: (row["section"], row["file"]))
    out = ['<!doctype html><html lang="ru"><meta charset="utf-8">',
           f"<title>{escape(title)}</title>",
           "<style>body{font:18px/1.6 system-ui;margin:2rem auto;max-width:760px;padding:0 1rem}"
           "img{max-width:100%;height:auto;display:block}figure{margin:0}"
           "h1,h2,p,nav,ol,li{margin-block:0}nav{margin-bottom:0}"
           "figcaption{font-size:.83em;color:#555}</style>",
           f"<h1>{escape(title)}</h1>",
           '<nav aria-label="Оглавление"><strong>Оглавление</strong><ol>']
    lines = ["# " + title, "**Оглавление**"]
    document.add_heading("Оглавление", 1)
    for number, section in enumerate(article["sections"], 1):
        heading = section["heading"]
        out.append(f'<li><a href="#section-{number}">{escape(heading)}</a></li>')
        document.add_paragraph(heading, "Caption")
        lines.append(f"{number}. {heading}")
    out.extend(["</ol></nav>", "<main>"])
    channel = channel_from_folder(folder)
    link = approved_url(channel) if channel else None
    linked_cta = False
    placements, doc_placements, markdown_placements = [], [], []
    doc_start = len(document.paragraphs)
    markdown_start = len(lines)

    def paragraph(value, heading=False, last_cta=False, section_number=None):
        nonlocal linked_cta
        placements.append("h2" if heading else "p")
        doc_placements.append("h2" if heading else "p")
        markdown_placements.append("h2" if heading else "p")
        if heading:
            document.add_heading(value, 2)
            out.append(f'<h2 id="section-{section_number}">{escape(value)}</h2>')
            lines.append("**" + value + "**")
        else:
            fragments = split_cta(value, channel, link) if last_cta else None
            if fragments:
                before, label, after = fragments
                paragraph_node = document.add_paragraph()
                paragraph_node.add_run(before)
                add_word_hyperlink(paragraph_node, label, link)
                paragraph_node.add_run(after)
                out.append(f'<p>{escape(before)}<a href="{escape(link, quote=True)}">{escape(label)}</a>{escape(after)}</p>')
                lines.append(before + "[" + label + "](" + link + ")" + after)
                linked_cta = True
            else:
                document.add_paragraph(value)
                out.append(f"<p>{escape(value)}</p>")
                lines.append(value)

    def picture(picture_row):
        name = picture_row["file"]
        raw = (image_folder / name).read_bytes()
        if digest(raw) != picture_row["sha256"]:
            raise ValueError("saved_image_sha_mismatch")
        destination = folder / "images" / name
        write(destination, raw)
        document.add_picture(io.BytesIO(raw), width=Inches(5.95))
        scene = picture_row["scene"]
        out.append(f'<figure><img src="images/{escape(name, quote=True)}" alt="{escape(scene, quote=True)}"></figure>')
        lines.append(f"![{scene}](images/{name})")
        placements.append(f"images/{name}")
        doc_placements.append(picture_row["sha256"])
        markdown_placements.append(f"images/{name}")

    # Dzen screenshot structure: title -> contents -> the best cover -> intro.
    # An image immediately follows every bold section heading, before its prose.
    if hero:
        picture(hero)
    for value in article["intro"]:
        paragraph(value)
    for number, section in enumerate(article["sections"], 1):
        paragraph(section["heading"], True, section_number=number)
        for row in ordered:
            if row["file"] != cover and row["section"] == number:
                picture(row)
        for index, value in enumerate(section["paragraphs"]):
            paragraph(value, last_cta=(number == len(article["sections"])
                                       and index == len(section["paragraphs"]) - 1))
    out.append("</main>")
    doc_end = len(document.paragraphs)
    markdown_end = len(lines)
    evidence = report["facts"]["evidence"]
    if evidence:
        out.append("<aside><h2>Научные источники</h2><ul>")
        document.add_heading("Научные источники", 1)
        for url in dict.fromkeys(x["source"] for x in evidence):
            out.append(f'<li><a href="{escape(url)}">{escape(url)}</a></li>')
            document.add_paragraph(url, "Caption")
        out.append("</ul></aside>")
    credits = [picture for picture in ordered if picture.get("source_page")]
    if credits:
        heading = ("Источники фотографий" if all(p["kind"] == "photo" for p in credits)
                   else "Источники иллюстраций")
        out.append(f"<aside><h2>{heading}</h2><ul>")
        document.add_heading(heading, 1)
        lines.append("## " + heading)
        for picture in credits:
            name = picture["file"]
            credit = " · ".join(str(picture.get(k) or "уточняется")
                                for k in ("source", "creator", "license"))
            if summary.get("image_use") == "private_editorial_review_only" and picture.get(
                    "rights_status") == "rights_review_pending":
                credit += " · Права не подтверждены, кадр для закрытого согласования"
            terms = picture.get("license_url") or ""
            attribution = picture.get("attribution") or ""
            license_link = (f' · <a href="{escape(terms)}">условия использования</a>'
                            if terms.startswith("https://") else "")
            out.append(f'<li>{escape(name)} · {escape(credit)} · {escape(attribution)} · '
                       f'<a href="{escape(picture["source_page"])}">страница снимка</a>{license_link}</li>')
            note = f'{name} · {credit} · {attribution} · {picture["source_page"]}'
            document.add_paragraph(f"{note} · {terms}" if terms else note, "Caption")
            lines.append(f"- {note} · {terms}" if terms else f"- {note}")
        out.append("</ul></aside>")
    out.append("</html>")
    html = "\n".join(out)
    doc_file = io.BytesIO()
    document.save(doc_file)
    word = doc_file.getvalue()

    # Check both exports against the original canonical visible text, using the
    # same bytes sent to QA. Captions and attribution live outside the prose.
    original = canonical(article)
    if digest(original) != report["text_sha256"]:
        raise ValueError("qa_sha_mismatch")
    checker = ArticleHTML()
    checker.feed(html)
    reopened = Document(io.BytesIO(word))
    doc_text = "\n".join(p.text for p in reopened.paragraphs[1:]
                         if p.text and p.style.name in {"Heading 2", "Normal"})
    image_refs = [f'images/{row["file"]}' for row in ordered]
    if (digest("\n".join(checker.lines)) != report["text_sha256"]
            or digest(doc_text) != report["text_sha256"] or image_refs != checker.images
            or len(reopened.inline_shapes) != len(images)):
        raise ValueError("document_roundtrip_failed")
    actual_doc_positions = []
    for paragraph_node in reopened.paragraphs[doc_start:doc_end]:
        images_xml = paragraph_node._p.xpath(".//a:blip")
        if images_xml:
            image_relation = images_xml[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            actual_doc_positions.append(digest(reopened.part.related_parts[image_relation].blob))
        else:
            actual_doc_positions.append("h2" if paragraph_node.style.name == "Heading 2" else "p")
    markdown = "\n\n".join(lines) + "\n"
    rendered_md = markdown.strip().split("\n\n")[markdown_start:markdown_end]
    md_positions = [
        block.rsplit("](", 1)[1][:-1] if block.startswith("![") and "](" in block
        else "h2" if block.startswith("**") and block.endswith("**") else "p" for block in rendered_md
    ]
    if checker.placement != placements or actual_doc_positions != doc_placements or md_positions != markdown_placements:
        raise ValueError("document_image_placement_mismatch")
    with ZipFile(io.BytesIO(word)) as archive:
        embedded = Counter(digest(archive.read(name)) for name in archive.namelist()
                           if name.startswith("word/media/"))
    if embedded != Counter(row["sha256"] for row in ordered):
        raise ValueError("docx_image_sha_mismatch")
    if ready:
        _verify_exported_visual_order(article, ordered, (html.encode("utf-8"), word))
    write(folder / "Статья.html", html.encode("utf-8"))
    write(folder / "Статья.docx", word)
    write(folder / "Статья.md", markdown.encode("utf-8"))
    write(folder / "Текст для SHA-256.txt", original.encode("utf-8"))
    write_json(folder / "Статус и проверки.json", {**summary,
               "layout_status": "verified" if not problems else "layout_blocked",
               "layout_issues": problems,
               "layout": "toc-cover-intro-heading-image-text",
               "subscription_link_status": ("linked" if linked_cta else "channel_url_or_name_missing"),
               "subscription_url": link if linked_cta else None,
               "cover": cover,
               "text_sha256": report["text_sha256"], "document_roundtrip": "completed",
               "visual_article": article,
               "article_checks": {k: v for k, v in report.items() if k != "article"},
               "images": [{k: v for k, v in row.items() if k != "image_embedding"} for row in ordered]})
    visual_checks = [row["image_qa"] for row in ordered if "image_qa" in row]
    if visual_checks:
        write_json(folder / "image_qa.json", {"source_text_sha256": report["text_sha256"],
                                            "images": visual_checks})
    else:
        (folder / "image_qa.json").unlink(missing_ok=True)
    accepted_names = {row["file"] for row in ordered}
    for path in (folder / "images").glob("*"):
        if path.is_file() and path.name not in accepted_names:
            path.unlink()
    versions = folder / "Версии"
    versions.mkdir(exist_ok=True)
    for row in version:
        write(versions / row.name, row.read_bytes())
    hashes = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            hashes.append(f"{digest(path.read_bytes())}  {path.relative_to(folder).as_posix()}")
    write(folder / "SHA256SUMS.txt", ("\n".join(hashes) + "\n").encode("utf-8"))
    return {"text_sha256": report["text_sha256"], "cover": cover, "files": len(hashes)}


def zip_packet(root, destination, *, articles):
    from .editorial_delivery import verify_review

    output = io.BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and (len(relative.parts) == 1 or relative.parts[0] in articles):
                archive.write(path, relative)
    data = output.getvalue()
    verified = verify_review(data, allow_incomplete=True)
    write(destination, data)
    return verified["package_sha256"]
