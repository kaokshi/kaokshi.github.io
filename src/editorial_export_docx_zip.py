#!/usr/bin/env python3
"""Read-only Word export of six editorial drafts with a ZIP and QA manifest.

NOT a publication step. The archive contains only existing source text.
No third-party images or rights status are fabricated.
"""
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE

from editorial_ai_check import extract_text
from editorial_layout import docx_body
from editorial_length import MIN_CHARS, MAX_CHARS, count_characters, length_finding

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT / "editorial-word-export"
SLUGS={
    "legends": ["legends-pilot-midnight-bus-20261120","legends-pilot-photo-studio-20261120"],
    "science": ["science-pilot-noctilucent-clouds-20261120","science-pilot-hair-ice-20261120"],
    "cinema": ["cinema-pilot-inception-20261120","cinema-pilot-shutter-island-20261120"],
}
CHANNEL_NAMES={"legends":"Легенды тёмных улиц","science":"За гранью очевидного","cinema":"Кадр за Кадром"}

def export_article(channel, slug, target):
    src=ROOT/"content"/"articles"/(slug+".json")
    a=json.loads(src.read_text(encoding="utf-8"))
    if a["slug"]!=slug or a["channel"]!=channel:
        raise ValueError("Article identity mismatch")
    if a.get("publication_authorized") is not False or a.get("release_hold") is not True:
        raise ValueError("Refusing to export article with altered publication safety flags")
    body=extract_text(a.get("body_html",""))
    chars = count_characters(a.get("body_html", ""))
    if length_finding(chars):
        raise ValueError(f"Article length {chars} outside {MIN_CHARS}–{MAX_CHARS}")
    doc=Document()
    sec=doc.sections[0]
    sec.top_margin=Cm(2.1); sec.bottom_margin=Cm(2.1)
    sec.left_margin=Cm(2.4); sec.right_margin=Cm(2.2)
    styles=doc.styles
    normal=styles["Normal"]
    normal.font.name="Calibri"; normal.font.size=Pt(11)
    normal.paragraph_format.space_after=Pt(8)
    normal.paragraph_format.line_spacing=1.18
    title=doc.add_heading(a["title"],level=0)
    title.alignment=WD_ALIGN_PARAGRAPH.CENTER
    if channel=="cinema":
        note=doc.add_paragraph("Полный пересказ фильма со спойлерами")
        note.alignment=WD_ALIGN_PARAGRAPH.CENTER
    elif channel=="legends":
        note=doc.add_paragraph("Оригинальная вымышленная история")
        note.alignment=WD_ALIGN_PARAGRAPH.CENTER
    paragraphs=[p.strip() for p in re.split(r"\n+",body) if p.strip()]
    if a.get("layout_schema") or "<section>" in a["body_html"]:
        docx_body(doc, a, ROOT)
    else:
        for paragraph in paragraphs:
            doc.add_paragraph(paragraph)
    if a.get("sources"):
        doc.add_heading("Источники для редакционной сверки",level=1)
        for url in a["sources"]:
            doc.add_paragraph(url)
    footer=sec.footer.paragraphs[0]
    footer.text=CHANNEL_NAMES[channel] + "  •  Редакционный экземпляр, не опубликовано"
    target.parent.mkdir(parents=True,exist_ok=True)
    doc.save(target)
    check=Document(target)
    checktext="\n".join(p.text for p in check.paragraphs)
    for p in paragraphs:
        if p not in checktext:
            raise ValueError("Word export lost source paragraph")
    return {"slug":slug,"title":a["title"],"channel":channel,
            "source_sha256":hashlib.sha256(src.read_bytes()).hexdigest(),
            "docx_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
            "character_count_without_spaces":chars,
            "paragraph_count":len(paragraphs),"images_count":len(a.get("image_files",[])),
            "publication_authorized":False}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    docs=OUT/"DOCX"; docs.mkdir(exist_ok=True)
    manifest={"schema":"six-article-word-export-v1",
              "generated_at":datetime.now(timezone.utc).isoformat(),
              "status":"EDITORIAL_DRAFTS_NOT_PUBLICATION_READY",
              "hard_blockers":["Seven rights-verified illustrations per article not supplied","Independent article-specific approval and full source verification pending","No calibrated four-detector AI index proven"],
              "articles":[]}
    for channel,slugs in SLUGS.items():
        for slug in slugs:
            manifest["articles"].append(export_article(channel,slug,docs/channel/(slug+".docx")))
    (OUT/"README.txt").write_text(
        "Шесть статей для редакционного рассмотрения. Файлы DOCX содержат полный текст.\n"
        "До разрешения публикации: проверка прав на иллюстрации и полная редакционная верификация.\n"
        "Объединённая модель ИИ на данный момент не подтверждена независимой проверкой.\n"
        "Прямой выпуск через RSS запрещён.\n",encoding="utf-8")
    manifest_file=OUT/"MANIFEST.json"
    manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    final=OUT/"six_articles_editorial_drafts_DOCX.zip"
    with zipfile.ZipFile(final,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=7) as arc:
        for f in sorted(docs.rglob("*.docx")):
            arc.write(f,f.relative_to(OUT))
        arc.write(manifest_file,manifest_file.name)
        arc.write(OUT/"README.txt","README.txt")
    with zipfile.ZipFile(final) as arc:
        assert arc.testzip() is None
        names=arc.namelist()
        assert len([n for n in names if n.endswith(".docx")])==6
    print(json.dumps({"zip":str(final),"archive_size":final.stat().st_size,
                      "article_count":len(manifest["articles"]),
                      "publication_authorized":False},ensure_ascii=False))

if __name__=="__main__":
    main()
