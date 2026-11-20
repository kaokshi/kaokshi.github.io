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
