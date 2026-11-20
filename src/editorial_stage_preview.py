#!/usr/bin/env python3
"""Create a safe offline owner preview for a READY draft only.

Output is a GitHub Actions artifact, never a web page in site/ or an RSS entry.
User text is escaped, source image paths are constrained to repository assets.
"""
import argparse
from html import escape
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_ai_check import extract_text
from editorial_auto_route import digest_article

ALLOWED_EXT={".jpg",".jpeg",".png",".webp",".gif",".svg"}


def make_preview(article,verdict,output,root=ROOT):
    if verdict.get("disposition")!="ready_for_next_stage":
        raise ValueError("Owner preview is allowed only after all earlier editorial stages")
    if verdict.get("publication_authorized") is not False:
        raise ValueError("Preview cannot authorize publication")
    if verdict.get("article_sha256")!=digest_article(article):
        raise ValueError("Article changed since evaluation: rerun checks")
    if article.get("channel") not in {"legends","science"}:
        raise ValueError("Paused or unknown channel")
    target=Path(output)
    target.mkdir(parents=True,exist_ok=True)
    folder=target/"images"
    folder.mkdir(exist_ok=True)
    pictures=[]
    for i,name in enumerate(article.get("image_files") or [],1):
        p=Path(name)
        if (not isinstance(name,str) or not name.startswith("content/assets/")
                or p.is_absolute() or ".." in p.parts or p.suffix.lower() not in ALLOWED_EXT):
            raise ValueError("Untrusted image path")
        full=root/p
        if not full.is_file():
            raise ValueError("Missing approved image asset")
        suffix=p.suffix.lower()
        dest=folder/f"image-{i:02d}{suffix}"
        shutil.copyfile(full,dest)
        pictures.append(f'<figure><img loading="lazy" src="images/{dest.name}" '
                        f'alt="{escape(p.stem,quote=True)}"></figure>')
    title=escape(article.get("title","Редакционный черновик"))
    channel=escape(article.get("channel",""))
    text=escape(extract_text(article.get("body_html","")))
    html='''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'">
<title>Редакторский предпросмотр</title><style>
body{margin:0;background:#f5f2ee;color:#252b32;font:19px/1.7 Georgia,serif}
main{max-width:840px;margin:50px auto;padding:0 28px 90px}
h1{line-height:1.24}img{width:100%;max-height:480px;object-fit:contain;border-radius:12px}
figure{margin:22px 0 34px}.text{white-space:pre-wrap}
small{font:13px/1.5 Arial,sans-serif;color:#626b74}
.status{border:1px solid #cdd2d5;padding:12px 16px;border-radius:10px;margin-bottom:30px}
</style></head><body><main>
<div class="status"><strong>Черновик для согласования, НЕ опубликовано</strong><p>
Следующий этап: решение владельца. Не является страницей сайта или RSS.</p></div>
''' + f'<small>{channel}</small><h1>{title}</h1>' + f'<section class="text">{text}</section>' + ''.join(pictures) + '''<footer><small>
Материал создан только для внутреннего рассмотрения в GitHub Actions.
</small></footer></main></body></html>'''
    file=target/"index.html"
    file.write_text(html,encoding="utf-8")
    return {"preview_file":str(file),"images":len(pictures),
            "publication_authorized":False,"rss_modified":False}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--slug",required=True)
    ap.add_argument("--decision-dir",default="editorial-stage-results")
    ap.add_argument("--output-dir",default="editorial-stage-results/owner-preview")
    args=ap.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}",args.slug):
        ap.error("Invalid slug")
    article=json.loads((ROOT/"content/articles"/(args.slug+".json")).read_text("utf-8"))
    verdict=json.loads((Path(args.decision_dir)/(args.slug+".json")).read_text("utf-8"))
    result=make_preview(article,verdict,Path(args.output_dir)/args.slug)
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":
    main()
