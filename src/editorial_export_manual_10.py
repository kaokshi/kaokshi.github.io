#!/usr/bin/env python3
"""Prepare ten EXISTING draft articles as accessible DOCX for manual Yandex Zen posting.

This is NOT approval, publication, or rights verification. Sources of truth:
content/articles/legends-01..05 and science-01..05, tracked by sha256.
No repository site or RSS output is modified.
"""
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from editorial_ai_check import extract_text

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/"editorial-manual-launch-10"
BATCH={
  "legends":[
    "legends-01-prohodnaya-17",
    "legends-02-poslednyaya-elektrichka",
    "legends-03-lift-dvoynoy-zvonok",
    "legends-04-okno-na-cherdake",
    "legends-05-dvor-na-fotografii",
  ],
  "science":[
    "science-01-krasnye-spraity",
    "science-02-svetyascheesya-more",
    "science-03-tardigrady",
    "science-04-temnaya-materiya",
    "science-05-polyarnoe-siyanie",
  ],
}
TITLES={"legends":"Легенды тёмных улиц","science":"За гранью очевидного"}

def clean_paragraphs(raw):
    pieces=re.findall(r"<p(?:\\s[^>]*)?>(.*?)</p>",raw,flags=re.IGNORECASE|re.DOTALL)
    if not pieces:
        return [p.strip() for p in re.split(r"\\n+",extract_text(raw)) if p.strip()]
    return [extract_text(part) for part in pieces if extract_text(part).strip()]

def make_one(channel,slug,place):
    source=ROOT/"content"/"articles"/f"{slug}.json"
    raw=source.read_bytes()
    a=json.loads(raw)
    if a.get("slug")!=slug or a.get("channel")!=channel:
        raise ValueError("Mismatched article slug or channel")
    if a.get("status")!="draft":
        raise ValueError("Expected owner-review draft")
    paras=clean_paragraphs(a.get("body_html",""))
    body="\\n".join(paras)
    n=len(re.sub(r"\\s","",body))
    if n<3000:
        raise ValueError(f"{slug} too short: {n}")
    doc=Document()
    sec=doc.sections[0]
    sec.top_margin=Cm(2.2)
    sec.bottom_margin=Cm(2.2)
    sec.left_margin=Cm(2.5)
    sec.right_margin=Cm(2.2)
    normal=doc.styles["Normal"]
    normal.font.name="Calibri"
    normal.font.size=Pt(11)
    normal.paragraph_format.space_after=Pt(8)
    normal.paragraph_format.line_spacing=1.18
    doc.add_heading(a["title"],0).alignment=WD_ALIGN_PARAGRAPH.CENTER
    for i,paragraph in enumerate(paras):
        if len(paragraph)<75 and i>0 and i<len(paras)-1 and not paragraph.endswith((".", "!", "?", "»")):
            doc.add_heading(paragraph,level=2)
        else:
            doc.add_paragraph(paragraph)
    footer=sec.footer.paragraphs[0]
    footer.text=TITLES[channel]+" | Редакционный черновик для ручной публикации, не утверждён"
    place.parent.mkdir(parents=True,exist_ok=True)
    doc.save(place)
    loaded=Document(place)
    content="\\n".join(p.text for p in loaded.paragraphs)
    for para in paras:
        if para not in content:
            raise ValueError("Word conversion dropped text: "+slug)
    return {
      "slug":slug,"title":a["title"],"channel":channel,
      "text_length_nonspace":n,"source_sha256":hashlib.sha256(raw).hexdigest(),
      "docx_sha256":hashlib.sha256(place.read_bytes()).hexdigest(),
      "editorial_reviewed":bool(a.get("editorial_reviewed")),
      "rights_reviewed":bool(a.get("rights_reviewed")),
      "attached_images":len(a.get("image_files") or []),
      "content_status":a["status"],"manual_owner_approval_required":True,
      "final_four_detectors_completed":False,
      "primary_sources_verified":False,
    }

def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    manifest={"schema":"owner-manual-launch-batch-10-v1",
      "created_at":datetime.now(timezone.utc).isoformat(),
      "purpose":"OWNER_REVIEW_MANUAL_PUBLICATION_ONLY",
      "publication_authorized_by_export":False,
      "warning":"No rights-cleared images attached, editorial approval pending, automated checks not guaranteed on current file hashes",
      "articles":[]}
    for channel,slugs in BATCH.items():
        for i,slug in enumerate(slugs,start=1):
            path=OUTPUT/"DOCX"/TITLES[channel]/f"{i:02d}_{slug}.docx"
            manifest["articles"].append(make_one(channel,slug,path))
    (OUTPUT/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
    guide=(
      "ПАКЕТ ДЛЯ РУЧНОГО ЗАПУСКА ДВУХ КАНАЛОВ ЯНДЕКС ДЗЕН\\n"
      "Десять существующих редакционных черновиков: пять мистических + пять научных.\\n"
      "Все тексты полные (от 3000 символов без пробелов), документы Word с проверкой сохранения абзацев.\\n\\n"
      "Перед публикацией каждой статьи обязательно:\\n"
      "1. Прочитать и одобрить текст, проверить непротиворечивость и заголовок.\\n"
      "2. Для научпопа проверить числовые и научные утверждения по первичным источникам.\\n"
      "3. Самостоятельно подобрать и проверить права на изображения; в текущих DOCX фотографии НЕ приложены.\\n"
      "4. Убедиться, что выбран правильный канал, оформление и ссылка на подписку.\\n"
      "5. Опубликовать вручную в Дзене; записать URL и дату в журнал запуска.\\n"
      "6. Не включать автоматический выпуск или RSS в GitHub.\\n\\n"
      "МИНИ-ЖУРНАЛ: manual-publication-log.csv предназначен для заполнения владельцем.\\n"
      "Показатели ИИ-детекторов здесь не придуманы: финальные показатели только из отдельных успешных проверок тех же SHA.\\n"
    )
    (OUTPUT/"README.txt").write_text(guide,encoding="utf-8")
    rows=["channel,slug,date_published_msk,zen_article_url,owner_approved,images_rights_checked,science_facts_checked,notes"]
    for ch,slugs in BATCH.items():
        for slug in slugs:
            rows.append(f"{ch},{slug},,,,,,")
    (OUTPUT/"manual-publication-log.csv").write_text("\\n".join(rows)+"\\n",encoding="utf-8")
    final=OUTPUT/"zen_10_manual_launch_review_DOCX.zip"
    with zipfile.ZipFile(final,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=7) as archive:
        for p in sorted((OUTPUT/"DOCX").rglob("*.docx")):
            archive.write(p,p.relative_to(OUTPUT))
        for name in ("README.txt","MANIFEST.json","manual-publication-log.csv"):
            archive.write(OUTPUT/name,name)
    with zipfile.ZipFile(final) as z:
        assert z.testzip() is None
        assert len([p for p in z.namelist() if p.endswith(".docx")])==10
    print(json.dumps({"archive":str(final),"size":final.stat().st_size,
        "article_count":len(manifest["articles"]),
        "all_3000_plus":all(a["text_length_nonspace"]>=3000 for a in manifest["articles"]),
        "publication_authorized":False},ensure_ascii=False))

if __name__=="__main__":
    main()
