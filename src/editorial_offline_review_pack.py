"""Build an entirely offline, owner-private review archive from existing images.

Never re-generates text or illustrations. Never publishes to public GitHub.
Requires an authenticated private owner review repository; all assets remain
in that repository and the ZIP is committed only to its private default branch.
"""
import argparse
from html import escape
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from hashlib import sha256
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image
from docx import Document
from docx.shared import Inches, Pt

from .editorial_chat_plaintext import approval, checked_day, connect, push_private_main, run
from .editorial_providers import Blocked
from .editorial_scene_qa import SCHEMA, verified_model_evidence

AUDIT_DIR = "image-qa-audit-v4"
CHANNELS = ("legends-01", "cinema-01", "science-01")
TITLES = {
    "legends-01": "Тени над подземным парком",
    "cinema-01": "Пленницы — обзор без спойлеров",
    "science-01": "Как капли воды строят каменные колонны в пещерах",
}
FILE_NAME = "Статья со встроенными ссылками на изображения.md"
OUTPUT = "Проверенный_комплект_3_статей_v1.zip"
IMAGE_LINK = re.compile(r"!\[([^\]\n]*)\]\(illustrations/(\d{2})\.jpg\)")
BAD_EXTERNAL_IMAGE = re.compile(r"!\[[^\]\n]*\]\((?:https?://|/)", re.I)


def text_to_html(title, raw):
    from markdown_it import MarkdownIt

    renderer = MarkdownIt("commonmark", {"html": False, "linkify": False})
    inner = renderer.render(raw)
    return ("""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + escape(title) + """</title>
<style>body{font:17px/1.65 -apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;
max-width:820px;margin:2rem auto;padding:0 1.2rem;color:#202832;background:#fff}
h1,h2{line-height:1.25}img{display:block;width:100%;height:auto;border-radius:8px;
margin:1rem auto 2rem}p{margin:0 0 1.2rem}li{margin:.3rem 0}
@media(prefers-color-scheme:dark){body{background:#161819;color:#f3f4f5}}</style>
</head><body>""" + inner + "</body></html>")


def write_docx(raw, folder, output):
    doc = Document()
    section = doc.sections[0]
    section.left_margin,section.right_margin = Inches(.8), Inches(.8)
    doc.styles["Normal"].font.name="Arial"
    doc.styles["Normal"].font.size=Pt(10.5)
    for line in raw.splitlines():
        stripped=line.strip()
        if not stripped:
            continue
        match=IMAGE_LINK.fullmatch(stripped)
        if match:
            image=folder/"illustrations"/(match[2]+".jpg")
            with Image.open(image) as im:
                ratio=im.height/im.width
            doc.add_picture(str(image), width=Inches(6.7))
            doc.paragraphs[-1].alignment=1
            continue
        if stripped.startswith("# "):
            doc.add_heading(stripped[2:],0)
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped)>4:
            doc.add_heading(stripped[2:-2],2)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:],2)
        else:
            paragraph=doc.add_paragraph()
            for part in re.split(r"(\*\*[^*]+\*\*)",stripped):
                if part.startswith("**") and part.endswith("**"):
                    paragraph.add_run(part[2:-2]).bold=True
                elif part: paragraph.add_run(part)
    doc.save(output)


def with_contents(raw):
    from .editorial_review import HEADING

    lines = raw.splitlines()
    first = [line.strip() for line in lines if line.strip()]
    if not first or not first[0].startswith("# "):
        raise Blocked("offline_bundle_title_missing")
    if len(first) > 1 and first[1] in {"**Оглавление**", "## Оглавление"}:
        return raw
    headings = [match[1] for line in lines if (match := HEADING.fullmatch(line.strip()))]
    if not 6 <= len(headings) <= 7:
        raise Blocked("offline_bundle_invalid_contents")
    title_line = next(i for i, line in enumerate(lines) if line.strip())
    contents = [f"{index}. {heading}" for index, heading in enumerate(headings, 1)]
    return "\n".join([*lines[:title_line + 1], "", "**Оглавление**", "", *contents, "",
                       *lines[title_line + 1:]]) + "\n"


def require_audited_bundle(base):
    from .editorial_images import image_hashes
    from .editorial_memory import digest, json_bytes, read_json
    from .editorial_scene_audit import input_sections
    from .editorial_review import HEADING

    evidence = read_json(base / AUDIT_DIR / "image_qa.json")
    if not evidence or evidence.get("schema") != SCHEMA:
        raise Blocked("offline_bundle_requires_current_image_audit")
    fingerprints = []
    for channel in CHANNELS:
        folder = base / channel
        markdown = (folder / FILE_NAME).read_text(encoding="utf-8")
        sections, slots = input_sections(markdown)
        blocks = [line.strip() for line in with_contents(markdown).splitlines() if line.strip()]
        if len(blocks) < 3 or blocks[1] not in {"**Оглавление**", "## Оглавление"}:
            raise Blocked("offline_bundle_missing_contents_" + channel)
        opening = [line for line in blocks[2:] if not re.match(r"^\d+\.\s+", line)]
        if not opening or not IMAGE_LINK.fullmatch(opening[0]):
            raise Blocked("offline_bundle_misplaced_cover_" + channel)
        if any(not IMAGE_LINK.fullmatch(following) for line, following in zip(blocks[2:], blocks[3:])
               if HEADING.fullmatch(line)):
            raise Blocked("offline_bundle_misplaced_section_image_" + channel)
        article = evidence.get("articles", {}).get(channel, {})
        source = digest("\n\n".join(sections.values()))
        rows = article.get("images", [])
        entries = {row["section_id"]: row for row in rows}
        if (article.get("source_text_sha256") != source
                or set(entries) != set(slots) or len(rows) != len(slots)):
            raise Blocked("offline_bundle_image_audit_text_or_slots_mismatch_" + channel)
        for section, slot in slots.items():
            row = entries[section]
            card = row.get("scene_card")
            path = folder / "illustrations" / f"{slot}.jpg"
            sha, fingerprint = image_hashes(path)
            if (not card or row.get("status") != "accepted" or row.get("slot") != slot
                    or row.get("schema") != SCHEMA or row.get("source_text_sha256") != source
                    or card.get("article_id") != channel or card.get("section_id") != section
                    or card.get("section_text_sha256") != digest(sections[section])
                    or card.get("source_text_sha256") != source
                    or row.get("scene_card_sha256") != digest(json_bytes(card))
                    or not verified_model_evidence(row) or not row.get("policy")
                    or row.get("visual_quality", {}).get("passed") is not True
                    or row.get("image_sha256") != sha):
                raise Blocked("offline_bundle_image_review_required_" + channel)
            if any((int(fingerprint, 16) ^ int(previous, 16)).bit_count() <= 6 for previous in fingerprints):
                raise Blocked("offline_bundle_duplicate_image")
            fingerprints.append(fingerprint)


def build_archive(base, path, zipfile):
    summary=[]
    with ZipFile(zipfile,"w",ZIP_DEFLATED,compresslevel=7) as archive:
        for channel in CHANNELS:
            folder=base/channel
            doc=folder/FILE_NAME
            if not doc.is_file():
                raise Blocked("offline_review_source_missing_"+channel)
            raw=with_contents(doc.read_text(encoding="utf-8"))
            refs=IMAGE_LINK.findall(raw)
            expected=7 if channel=="science-01" else 8
            if BAD_EXTERNAL_IMAGE.search(raw) or len(refs)!=expected:
                raise Blocked("offline_review_image_reference_invalid_"+channel)
            ids=[int(num) for _,num in refs]
            if sorted(ids)!=list(range(1,expected+1)):
                raise Blocked("offline_review_image_sequence_invalid_"+channel)
            images=[]
            for image_num in range(1,expected+1):
                image=folder/"illustrations"/f"{image_num:02d}.jpg"
                if not image.is_file():
                    raise Blocked("offline_review_photo_missing_"+channel)
                with Image.open(image) as im:
                    im.verify()
                with Image.open(image) as im:
                    if min(im.size)<320:
                        raise Blocked("offline_review_photo_too_small_"+channel)
                images.append(image)
            html=path/(channel+".html")
            html.write_text(text_to_html(TITLES[channel],raw),encoding="utf-8")
            word=path/(channel+".docx")
            write_docx(raw,folder,word)
            from .editorial_package import ArticleHTML

            expected_refs = [f"illustrations/{slot}.jpg" for _, slot in refs]
            html_check = ArticleHTML()
            html_check.feed(html.read_text(encoding="utf-8"))
            reopened = Document(word)
            if html_check.all_images != expected_refs or html_check.extra_media or len(reopened.inline_shapes) != len(refs):
                raise Blocked("offline_bundle_export_images_invalid_" + channel)
            media_hashes = []
            for p in reopened.paragraphs:
                for image_xml in p._p.xpath(".//a:blip"):
                    rel = image_xml.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                    media_hashes.append(sha256(reopened.part.related_parts[rel].blob).hexdigest())
            if media_hashes != [sha256((folder / ref).read_bytes()).hexdigest() for ref in expected_refs]:
                raise Blocked("offline_bundle_docx_image_order_invalid_" + channel)
            name={
                "legends-01":"01_Мистика",
                "cinema-01":"02_Кино",
                "science-01":"03_Научпоп",
            }[channel]
            archive.writestr(name+"/Статья.md",raw)
            archive.write(html,arcname=name+"/Открыть статью.html")
            archive.write(word,arcname=name+"/Статья_с_фотографиями.docx")
            for img in images:
                archive.write(img,arcname=name+"/illustrations/"+img.name)
            manifest=folder/"illustrations"/"source-manifest.json"
            if manifest.is_file():
                archive.write(manifest,arcname=name+"/illustrations/source-manifest.json")
            summary.append({"channel":channel,"images":len(images),
                            "document":True,"html":True,
                            "offline_relative_images":True})
        archive.write(base / AUDIT_DIR / "image_qa.json", arcname="image_qa.json")
        archive.writestr("НАЧНИТЕ_ЗДЕСЬ.txt",
            "Редакция Дзен — тестовые статьи для личного согласования.\n\n"
            "Откройте файл «Открыть статью.html» в каждой папке или "
            "«Статья_с_фотографиями.docx». Для просмотра интернет не нужен.\n"
            "Исходные .md используют ссылки на соседнюю папку illustrations, "
            "не перемещайте фотографии отдельно от Markdown.\n"
            "В архиве 3 статьи и 23 изображения. Не является разрешением на "
            "публичную публикацию без финальной проверки.\n"
            "Кадры фильма могут быть защищены авторским правом.\n"
        )
    assert len(summary)==3 and sum(x["images"] for x in summary)==23
    return summary


def package(day, token, *, historical_repair=False):
    approval()
    checked_day(day)
    if not token:
        raise Blocked("private_review_token_missing")
    with TemporaryDirectory(prefix="zen-offline-review-") as work:
        path=Path(work)
        repo=path/"private"
        git,env=connect(token,repo)
        base=repo/"editorial-chat-review"/day
        zipfile = base / OUTPUT
        input_base = base
        if historical_repair:
            from .editorial_historical_release import materialize_verified_selection

            input_base = path / "verified"
            materialize_verified_selection(base, input_base)
        require_audited_bundle(input_base)
        summary = build_archive(input_base, path, zipfile)
        with ZipFile(zipfile) as archive:
            if archive.testzip():
                raise Blocked("offline_bundle_crc_invalid")
        rel=zipfile.relative_to(repo).as_posix()
        args=[*git,"-C",str(repo)]
        run([*args,"add","--",rel],env)
        run([*args,"-c","user.name=Zen Editorial Bot",
             "-c","user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit","-m","Private offline three-channel review package with all 23 images"],env)
        push_private_main(args, env)
        out={"status":"private_offline_review_ready",
             "archive":rel,"size_bytes":zipfile.stat().st_size,
             "sha256":sha256(zipfile.read_bytes()).hexdigest(),
             "articles":summary,
             "publication_authorized":False}
        print(json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
        return out


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--date",required=True)
    p.add_argument("--historical-repair", action="store_true")
    a=p.parse_args()
    import os
    package(a.date, os.environ.get("EDITORIAL_PRIVATE_TOKEN"), historical_repair=a.historical_repair)
