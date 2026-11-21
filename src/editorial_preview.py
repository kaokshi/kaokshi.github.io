"""Export a verified, self-contained private reading copy without building the site."""
from base64 import b64encode
from html import escape
from xml.etree import ElementTree as ET

from editorial_cover import asset, verify_cover
from editorial_layout import docx_body, parse_sections
from editorial_revision import save_text


def export_preview(article, asset_root, output):
    from docx import Document
    verify_cover(article, asset_root)
    body = parse_sections(article["body_html"])
    pictures = [node for node in body.iter("img")]
    for node, name in zip(pictures, article["image_files"], strict=True):
        node.set("src", "data:image/jpeg;base64," + b64encode(asset(asset_root, name).read_bytes()).decode())
    title = escape(article["title"])
    links = []
    doc = Document()
    doc.add_heading(article["title"], 0)
    docx_body(doc, article, asset_root)
    for source in article.get("sources", []):
        url = source.get("url") if isinstance(source, dict) else source
        if isinstance(url, str) and url.startswith("https://"):
            links.append(f'<li><a href="{escape(url, quote=True)}">{escape(url)}</a></li>')
            doc.add_paragraph(url)
    sources = '<h2>Источники</h2><ul>' + ''.join(links) + '</ul>' if links else ''
    # Imported drafts may contain inline markup. Emit only the reading-copy elements.
    for section in body:
        for node in list(section):
            if node.tag == "p":
                plain = "".join(node.itertext())
                node.clear()
                node.text = plain
    text = "".join(ET.tostring(s, encoding="unicode", method="html") for s in body)
    html = (f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{title}</title>'
            '<style>body{max-width:760px;margin:3rem auto;padding:0 1rem;color:#192422;'
            'font:19px/1.6 Georgia,serif}figure{margin:1.8rem 0}img{max-width:100%;height:auto}'
            'figcaption,li{font:14px/1.5 system-ui;color:#43514d;overflow-wrap:anywhere}</style>'
            f'<main><h1>{title}</h1>{text}{sources}</main></html>')
    output.mkdir(parents=True, exist_ok=True)
    save_text(output / "article.html", html)
    doc.save(output / "article.docx")


def verify_preview(article, asset_root, output):
    """Inspect saved Word/HTML and invoke the unchanged RSS serializer in memory."""
    from datetime import datetime, timezone
    from hashlib import sha256
    from html.parser import HTMLParser
    import importlib.util
    from pathlib import Path
    from docx import Document

    class ReadingCopy(HTMLParser):
        def __init__(self):
            super().__init__()
            self.images = []

        def handle_starttag(self, tag, attrs):
            if tag == "img":
                self.images.append(dict(attrs).get("src"))

    verify_cover(article, asset_root)
    rows = article["cover_selection"]["images"]
    doc = Document(output / "article.docx")
    headings = [p for p in doc.paragraphs if p.style.name == "Heading 2"]
    names = [s[0][0].text for s in parse_sections(article["body_html"])]
    if [p.text for p in headings] != names or any(not all(r.bold for r in p.runs) for p in headings):
        raise ValueError("Word section headings differ from approved layout")
    from base64 import b64decode
    reader = ReadingCopy()
    reader.feed((output / "article.html").read_text())
    if len(rows) != len(doc.inline_shapes) or len(reader.images) != len(rows):
        raise ValueError("Missing embedded illustrations")
    for img, shape, row in zip(reader.images, doc.inline_shapes, rows):
        rel = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
        if (not img.startswith("data:image/jpeg;base64,")
                or sha256(b64decode(img.split(",", 1)[1], validate=True)).hexdigest() != row["sha256"]
                or sha256(doc.part.related_parts[rel].blob).hexdigest() != row["sha256"]):
            raise ValueError("Illustration bytes or order differ between reading copies")

    # Isolate the existing serializer's module-level asset base from a concurrent site build.
    spec = importlib.util.spec_from_file_location("editorial_preview_rss", Path(__file__).with_name("build.py"))
    serializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serializer)
    serializer.BASE = asset_root
    url = "https://preview.invalid"
    post = dict(article, path="/preview/", date=datetime.now(timezone.utc))
    feed = serializer.rss(article["channel"], serializer.CHANNELS[article["channel"]], [post], url)
    item = ET.fromstring(feed).find("channel/item")
    body = parse_sections(item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded"))
    expected = [url + "/" + row["file"].removeprefix("content/") for row in rows]
    if ([s[0][0].text for s in body] != names or [i.get("src") for i in body.iter("img")] != expected
            or item.find("enclosure").get("url") != expected[0]):
        raise ValueError("Offline RSS preview differs from article")
    return {"headings": len(names), "embedded_images": len(rows), "cover_sha256": rows[0]["sha256"],
            "html_docx_rss_order_verified": True}


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    from editorial_revision import save_case
    from editorial_writer import private_path
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("article", "asset-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    article = json.loads(args.article.read_text())
    dest = private_path(args.output)
    export_preview(article, args.asset_root, dest)
    save_case(dest / "format-report.json", verify_preview(article, args.asset_root, dest))
