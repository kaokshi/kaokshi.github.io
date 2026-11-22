"""Readable owner review packages with round-trip checks on text and images."""
from collections import Counter
from html import escape
from html.parser import HTMLParser
import io
from zipfile import ZIP_DEFLATED, ZipFile

from .editorial_memory import digest, write, write_json
from .editorial_review import canonical
from .editorial_images import select_cover


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.pending = None
        self.lines = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.inside = True
        if self.inside and tag in {"h2", "p"}:
            self.pending = []
        if self.inside and tag == "img":
            self.images.append(dict(attrs).get("src"))

    def handle_endtag(self, tag):
        if tag in {"h2", "p"} and self.pending is not None:
            self.lines.append("".join(self.pending))
            self.pending = None
        if tag == "main":
            self.inside = False

    def handle_data(self, text):
        if self.pending is not None:
            self.pending.append(text)


def package(folder, title, version, report, images, image_folder, summary):
    from docx import Document
    from docx.shared import Inches

    folder.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading(title, 0)
    out = ['<!doctype html><html lang="ru"><meta charset="utf-8">',
           f"<title>{escape(title)}</title>",
           "<style>body{font:18px/1.6 system-ui;margin:2rem auto;max-width:760px;padding:0 1rem}"
           "img{max-width:100%;height:auto;display:block}figure{margin:0 0 1rem}"
           "figcaption{font-size:.83em;color:#555}</style>",
           f"<h1>{escape(title)}</h1><main>"]
    article = report["article"]
    cover = select_cover(images)
    ordered = sorted(images, key=lambda row: (1 if row["file"] == cover else row["section"],
                                               row["file"] != cover, row["file"]))
    lines = []

    def paragraph(value, heading=False):
        if heading:
            document.add_heading(value, 2)
            out.append(f"<h2>{escape(value)}</h2>")
            lines.append("**" + value + "**")
        else:
            document.add_paragraph(value)
            out.append(f"<p>{escape(value)}</p>")
            lines.append(value)

    for value in article["intro"]:
        paragraph(value)
    for number, section in enumerate(article["sections"], 1):
        paragraph(section["heading"], True)
        for picture in ordered:
            target_section = 1 if picture["file"] == cover else picture["section"]
            if target_section != number:
                continue
            name = picture["file"]
            raw = (image_folder / name).read_bytes()
            if digest(raw) != picture["sha256"]:
                raise ValueError("saved_image_sha_mismatch")
            destination = folder / "images" / name
            write(destination, raw)
            document.add_picture(io.BytesIO(raw), width=Inches(5.95))
            label = picture.get("label")
            if label:
                document.add_paragraph(label, "Caption")
            out.append(f'<figure><img src="images/{name}" alt="{escape(picture["scene"])}">')
            if label:
                out.append(f"<figcaption>{escape(label)}</figcaption>")
            out.append("</figure>")
        for value in section["paragraphs"]:
            paragraph(value)
    out.append("</main>")
    evidence = report["facts"]["evidence"]
    if evidence:
        out.append("<aside><h2>Научные источники</h2><ul>")
        document.add_heading("Научные источники", 1)
        for url in dict.fromkeys(x["source"] for x in evidence):
            out.append(f'<li><a href="{escape(url)}">{escape(url)}</a></li>')
            document.add_paragraph(url, "Caption")
        out.append("</ul></aside>")
    photos = [picture for picture in ordered if picture["kind"] == "photo"]
    if photos:
        out.append("<aside><h2>Источники фотографий</h2><ul>")
        document.add_heading("Источники фотографий", 1)
        for picture in photos:
            name = picture["file"]
            credit = " · ".join(str(picture.get(k) or "уточняется")
                                for k in ("source", "creator", "license"))
            terms = picture.get("license_url") or ""
            attribution = picture.get("attribution") or ""
            link = (f' · <a href="{escape(terms)}">условия использования</a>'
                    if terms.startswith("https://") else "")
            out.append(f'<li>{escape(name)} · {escape(credit)} · {escape(attribution)} · '
                       f'<a href="{escape(picture["source_page"])}">страница снимка</a>{link}</li>')
            document.add_paragraph(f'{name} · {credit} · {attribution} · {picture["source_page"]} · {terms}',
                                   "Caption")
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
    with ZipFile(io.BytesIO(word)) as archive:
        embedded = Counter(digest(archive.read(name)) for name in archive.namelist()
                           if name.startswith("word/media/"))
    if embedded != Counter(row["sha256"] for row in ordered):
        raise ValueError("docx_image_sha_mismatch")
    write(folder / "Статья.html", html.encode("utf-8"))
    write(folder / "Статья.docx", word)
    write(folder / "Статья.md", ("\n\n".join(lines) + "\n").encode("utf-8"))
    write(folder / "Текст для SHA-256.txt", original.encode("utf-8"))
    write_json(folder / "Статус и проверки.json", {**summary, "cover": cover,
               "text_sha256": report["text_sha256"], "document_roundtrip": "completed",
               "article_checks": {k: v for k, v in report.items() if k != "article"},
               "images": ordered})
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


def zip_packet(root, destination):
    with ZipFile(destination, mode="w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("review_zip_integrity_failure")
    return digest(destination.read_bytes())
