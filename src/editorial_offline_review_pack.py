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
from markdown_it import MarkdownIt

from .editorial_chat_plaintext import approval, checked_day, connect, run
from .editorial_providers import Blocked

CHANNELS = ("legends-01", "cinema-01", "science-01")
TITLES = {
    "legends-01": "Тени над подземным парком",
    "cinema-01": "Пленницы — обзор без спойлеров",
    "science-01": "Как капли воды строят каменные колонны в пещерах",
}
FILE_NAME = "Статья со встроенными ссылками на изображения.md"
OUTPUT = "Полный_комплект_3_статей_с_изображениями.zip"
IMAGE_LINK = re.compile(r"!\[([^\]\n]*)\]\(illustrations/(\d{2})\.jpg\)")
BAD_EXTERNAL_IMAGE = re.compile(r"!\[[^\]\n]*\]\((?:https?://|/)", re.I)


def text_to_html(title, raw):
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


def package(day, token):
    approval()
    checked_day(day)
    if not token:
        raise Blocked("private_review_token_missing")
    with TemporaryDirectory(prefix="zen-offline-review-") as work:
        path=Path(work)
        repo=path/"private"
        git,env=connect(token,repo)
        base=repo/"editorial-chat-review"/day
        zipfile=base/OUTPUT
        summary=[]
        with ZipFile(zipfile,"w",ZIP_DEFLATED,compresslevel=7) as archive:
            for channel in CHANNELS:
                folder=base/channel
                doc=folder/FILE_NAME
                if not doc.is_file():
                    raise Blocked("offline_review_source_missing_"+channel)
                raw=doc.read_text(encoding="utf-8")
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
        rel=zipfile.relative_to(repo).as_posix()
        args=[*git,"-C",str(repo)]
        run([*args,"add","--",rel],env)
        run([*args,"-c","user.name=Zen Editorial Bot",
             "-c","user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit","-m","Private offline three-channel review package with all 23 images"],env)
        run([*args,"push","origin","HEAD:refs/heads/main"],env,timeout=120)
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
    a=p.parse_args()
    import os
    package(a.date,os.environ.get("EDITORIAL_PRIVATE_TOKEN"))
