"""Finish the already fact-corrected private science article with mixed visuals.

One fixed, source-backed topic. No Groq/Gemini/OVH calls, no publication.
Photos come from independently licensed Wikimedia Commons file pages.
Two conceptual illustrations come from free-account Cloudflare Klein.
All text and images remain in the owner's PRIVATE review repository.
"""
import argparse
from hashlib import sha256, md5
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, quote

from PIL import Image
from docx import Document
from docx.shared import Inches, Pt

from .editorial_chat_plaintext import approval, checked_day, connect, run
from .editorial_images import similarity
from .editorial_memory import digest
from .editorial_providers import Blocked, ProviderError, klein, request
from .editorial_review import canonical, parse_article, structural_checks

ROOT_DIR = "editorial-chat-review"
ARTICLE_SOURCE = "Исправленный текст для сборки.md"
TOC_TITLE = "Как капли воды строят каменные колонны в пещерах"
COMMONS_HOSTS = ("commons.wikimedia.org", "upload.wikimedia.org")
NPS_SOURCES = [
    "https://www.nps.gov/subjects/caves/speleothems.htm",
    "https://www.nps.gov/grba/learn/nature/speleothems-cave-formations.htm",
    "https://www.nps.gov/articles/000/what-s-dripping-in-the-cave.htm",
]
# The first item is the independent cover; the remaining items are aligned
# with section numbers. Section 1 has a cover and its own body image.
VISUALS = [
    {"section": 0, "kind": "photo",
     "file_title": "Stalactites Cave.JPG",
     "scene": "Wide limestone cave with stalactites and stalagmites, real natural underground formations"},
    {"section": 1, "kind": "photo",
     "file_title": "Stalactite Forming (9939820036).jpg",
     "scene": "Close view of real calcite-rich water dripping to form a cave stalactite"},
    {"section": 2, "kind": "illustration",
     "scene": "Accurate conceptual cross-section of limestone cave chemistry, water carrying dissolved calcium bicarbonate from a crack; when water enters air-filled cave CO2 gas escapes as tiny bubbles and calcite deposits as a ring. Clear visual distinction between dissolved minerals and solid calcite. Scientifically plausible fluid flow, no words, no symbols, no text, no arrows. Modern beautiful educational 3D editorial cutaway, blue mineral water, limestone layers, muted amber colors, landscape 16:9"},
    {"section": 3, "kind": "photo",
     "file_title": "Soda Straw.jpg",
     "scene": "Thin hollow soda straw stalactite tubular speleothems hanging from cave ceiling"},
    {"section": 4, "kind": "photo",
     "file_title": "Luray Caves Stalagmite formation.jpg",
     "scene": "Real stalagmite rising vertically from wet cave floor"},
    {"section": 5, "kind": "photo",
     "file_title": "Stalactite and Stalagmite Converging into Column.jpg",
     "scene": "Real cave calcite column where downward stalactite meets upward stalagmite"},
    {"section": 6, "kind": "illustration",
     "scene": "Beautiful realistic scientific educational cutaway showing two adjacent limestone cave drip sites: left steady water dripping growing slender calcite soda straw stalactite, right different flow yielding broad stalagmite mound; soil and cracks above, groundwater path, subtle air circulation visible by dust motes; no unproven growth-rate measurements, no captions or text, no writing, landscape 16:9. Modern restrained teal and sandstone palette"},
]

def _metadata(title):
    if not re.fullmatch(r"[\w ()&.,'\-]+\.jpe?g", title, re.I):
        raise Blocked("science_photo_title_invalid")
    query = urlencode({"action": "query", "format": "json",
                       "prop": "imageinfo", "iiprop": "url|extmetadata",
                       "iiurlwidth": "1400", "titles": "File:" + title})
    data = json.loads(request("https://commons.wikimedia.org/w/api.php?" + query,
                              COMMONS_HOSTS, timeout=28, limit=2_000_000))
    pages = list(data.get("query", {}).get("pages", {}).values())
    if len(pages) != 1 or not pages[0].get("imageinfo"):
        raise Blocked("science_commons_photo_missing")
    row = pages[0]["imageinfo"][0]
    md = row.get("extmetadata") or {}
    license_name = (md.get("LicenseShortName") or {}).get("value", "")
    license_url = (md.get("LicenseUrl") or {}).get("value", "")
    artist = re.sub(r"<[^>]+>", "", (md.get("Artist") or {}).get("value", "")).strip()
    if not artist or not (license_name.startswith(("CC BY ", "CC BY-SA ")) or license_name.startswith("CC0")):
        raise Blocked("science_commons_license_not_verified")
    if "creativecommons.org/" not in license_url:
        raise Blocked("science_commons_license_url_not_verified")
    # Wikimedia's well-defined, content-addressed file path is derived only
    # from the already license-verified Commons page title. It avoids redirect
    # chains through outside hosts and rejects untrusted API asset URLs.
    filename = title.replace(" ", "_")
    digest_path = md5(filename.encode("utf-8")).hexdigest()
    url = ("https://upload.wikimedia.org/wikipedia/commons/"
           + digest_path[0] + "/" + digest_path[:2] + "/"
           + quote(filename, safe="()"))
    page = "https://commons.wikimedia.org/wiki/File:" + quote(title.replace(" ", "_"), safe="()")
    return {"url": url, "title": title, "license": license_name,
            "license_url": license_url, "artist": artist,
            "source_page": page, "rights_status": "license_metadata_verified"}

def _render_image(raw):
    im = Image.open(BytesIO(raw))
    im.load()
    if im.width < 600 or im.height < 480:
        raise Blocked("science_photo_resolution_insufficient")
    im = im.convert("RGB")
    im.thumbnail((1600, 1150))
    out = BytesIO()
    im.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue(), list(im.size)

def _git_commit(root, git, env, filenames, note):
    args = [*git, "-C", str(root)]
    run([*args, "add", "--", *filenames], env)
    if run([*args, "status", "--porcelain", "--", *filenames], env):
        run([*args, "-c", "user.name=Zen Editorial Bot",
             "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit", "-m", note], env)
        run([*args, "push", "origin", "HEAD:refs/heads/main"], env, timeout=100)

def _as_html(article, images):
    parsed = parse_article(article)
    lines = ['<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">',
             '<title>' + escape(TOC_TITLE) + '</title>',
             '<style>body{font-family:Arial,sans-serif;max-width:780px;margin:45px auto;line-height:1.6;color:#17202b} img{width:100%;height:auto;border-radius:8px}h2{margin-top:32px}</style></head><body>',
             '<h1>' + escape(TOC_TITLE) + '</h1>', '<h2>Оглавление</h2><ol>']
    for sect in parsed["sections"]:
        lines.append("<li>" + escape(sect["heading"]) + "</li>")
    lines.extend(['</ol>', '<img src="illustrations/01.jpg" alt="Пещера с натёчными образованиями">'])
    for intro in parsed["intro"]:
        lines.append("<p>" + escape(intro) + "</p>")
    for idx, sect in enumerate(parsed["sections"], 2):
        lines.extend(["<h2>" + escape(sect["heading"]) + "</h2>",
                      f'<img src="illustrations/{idx:02d}.jpg" alt="{escape(VISUALS[idx-1]["scene"])}">'])
        for p in sect["paragraphs"]:
            lines.append("<p>" + escape(p) + "</p>")
    lines.append('</body></html>')
    return "\n".join(lines)

def _as_md(article):
    parsed = parse_article(article)
    lines = ["# " + TOC_TITLE, "", "**Оглавление**", ""]
    for n, sec in enumerate(parsed["sections"], 1):
        lines.append(f"{n}. {sec['heading']}")
    lines.extend(["", "![Обложка: настоящая карстовая пещера](illustrations/01.jpg)", ""])
    lines.extend(parsed["intro"])
    for idx, sec in enumerate(parsed["sections"], 2):
        lines.extend(["", "**" + sec["heading"] + "**", "",
                      f"![Иллюстрация к разделу {idx-1}](illustrations/{idx:02d}.jpg)", ""])
        lines.extend(sec["paragraphs"])
    return "\n\n".join(lines).replace("\n\n\n\n", "\n\n") + "\n"

def _as_docx(article, images, destination):
    parsed = parse_article(article)
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = Inches(0.82); sec.right_margin = Inches(0.82)
    doc.styles["Normal"].font.name = "Arial"; doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(TOC_TITLE, 0)
    doc.add_heading("Оглавление", 2)
    for idx, section in enumerate(parsed["sections"], 1):
        doc.add_paragraph(f"{idx}. {section['heading']}")
    def picture(path):
        with Image.open(path) as im:
            w,h = im.size
        width = min(6.4, 6.4*h/w*1.7)
        width = max(3.2, width)
        doc.add_picture(str(path), width=Inches(width))
    picture(images[0])
    for para in parsed["intro"]:
        doc.add_paragraph(para)
    for idx, section in enumerate(parsed["sections"], 1):
        doc.add_heading(section["heading"], 2)
        picture(images[idx])
        for paragraph in section["paragraphs"]:
            doc.add_paragraph(paragraph)
    doc.save(destination)

def finish(day, token):
    checked_day(day); approval()
    if not token or os.environ.get("EDITORIAL_CF_FREE_ONLY") != "true":
        raise Blocked("science_private_free_klein_required")
    with TemporaryDirectory(prefix="zen-science-final-") as td:
        root = Path(td)/"repo"
        git, env = connect(token, root)
        folder = root/ROOT_DIR/day/"science-01"
        source = folder/ARTICLE_SOURCE
        if not source.is_file():
            raise Blocked("science_corrected_source_missing")
        full = source.read_text(encoding="utf-8")
        if not full.startswith("# " + TOC_TITLE + "\n"):
            raise Blocked("science_article_topic_mismatch")
        article = full.split("\n", 1)[1].lstrip()
        p = parse_article(article)
        checks = structural_checks(p, [])
        if checks["issues"] or checks["section_count"] != 6 or not 3500 <= checks["chars_without_spaces"] <= 6000:
            raise Blocked("science_article_structural_or_length_invalid")
        if any(phrase in article.lower() for phrase in (
                "в сотни раз больше", "капля, прикреплённая к потолку, медленно испаряется",
                "в более влажных условиях рост ускоряется")):
            raise Blocked("science_old_fact_errors_still_present")
        target = folder/"illustrations"; target.mkdir(parents=True, exist_ok=True)
        manifest_path = target/"source-manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else []
        entries = {r["file"]: r for r in manifest}
        sha_seen = []
        generated_now = 0
        for idx, entry in enumerate(VISUALS, 1):
            filename = f"{idx:02d}.jpg"
            img_path = target/filename
            saved = entries.get(filename)
            if (saved and img_path.is_file()
                    and saved.get("sha256") == digest(img_path.read_bytes())
                    and saved.get("text_sha256") == digest(canonical(p))):
                sha_seen.append(saved["sha256"])
                continue
            if entry["kind"] == "photo":
                metadata = _metadata(entry["file_title"])
                raw = request(metadata["url"], COMMONS_HOSTS, timeout=30, limit=8_000_000)
            else:
                if generated_now >= 2:
                    raise Blocked("science_klein_two_call_budget_exhausted")
                raw, params = klein(entry["scene"])
                metadata = {"source": "Cloudflare Klein", "model": "@cf/black-forest-labs/flux-2-klein-4b",
                            "rights_status": "generated_for_owner_review",
                            "parameters": params, "license": "review_cloudflare_terms"}
                generated_now += 1
            rendered, size = _render_image(raw)
            content_sha = digest(rendered)
            if content_sha in sha_seen:
                raise Blocked("science_duplicate_image")
            with Image.open(BytesIO(rendered)) as pil:
                # An independently measurable visual semantic checkpoint. A
                # reasonable CLIP score is not human fact verification.
                model, scores, emb = similarity(pil, [
                    entry["scene"], "Cave water minerals geological speleothems",
                    "unrelated street automobile or city skyline"])
            if scores[0] < .17 or scores[0] < scores[2]:
                raise Blocked("science_visual_semantic_mismatch_" + filename)
            img_path.write_bytes(rendered)
            record = {
                "file": filename, "sha256": content_sha,
                "text_sha256": digest(canonical(p)),
                "size": size, "kind": entry["kind"],
                "section": entry["section"], "scene": entry["scene"],
                "scene_similarity": round(scores[0], 4),
                "match_model": model, **metadata}
            entries[filename] = record
            manifest_path.write_text(json.dumps(
                [entries[f"{j:02d}.jpg"] for j in range(1,8)
                 if f"{j:02d}.jpg" in entries], ensure_ascii=False, indent=2))
            _git_commit(root, git, env,
                        [img_path.relative_to(root).as_posix(),
                         manifest_path.relative_to(root).as_posix()],
                        f"Private science mixed visual {idx} of 7")
            sha_seen.append(content_sha)
            print(json.dumps({"image": idx, "kind": entry["kind"],
                              "passed": True, "semantic_score": round(scores[0],4)}), flush=True)
        if len(entries) != 7 or len([r for r in entries.values() if r["kind"]=="photo"]) != 5:
            raise Blocked("science_mixed_seven_images_incomplete")
        images = [target/f"{j:02d}.jpg" for j in range(1,8)]
        final_md = folder/"Финальная редакция.md"
        final_html = folder/"Финальная редакция.html"
        final_docx = folder/"Финальная редакция.docx"
        final_md.write_text(_as_md(article), encoding="utf-8")
        final_html.write_text(_as_html(article, images), encoding="utf-8")
        _as_docx(article, images, final_docx)
        credits = folder/"Источники и лицензии изображений.md"
        lines = ["# Источники изображений и авторские права", ""]
        for j in range(1,8):
            row=entries[f"{j:02d}.jpg"]
            if row["kind"]=="photo":
                lines.append(f'{j}. Фото: {row["artist"]}. {row["license"]}. {row["source_page"]}. Лицензия: {row["license_url"]}. Изменения: уменьшение размера и перекодирование JPEG.')
            else:
                lines.append(f'{j}. Объяснительная ИИ-иллюстрация: Cloudflare Klein. Не является документальным снимком; условия использования подлежат проверке.')
        credits.write_text("\n\n".join(lines)+"\n",encoding="utf-8")
        state = {"status":"owner_review_ready_with_qualifications",
                 "publication_authorized":False,
                 "text_sha256": digest(canonical(p)),
                 "corrected_by":"editorial_fact_review_of_previous_Groq_draft",
                 "chars_without_spaces":checks["chars_without_spaces"],
                 "sections":checks["section_count"],
                 "images_total":len(entries),
                 "real_photos":5,"klein_explanatory_illustrations":2,
                 "visual_qa":"CLIP_scene_similarity_passed_not_human_verified",
                 "scientific_sources": NPS_SOURCES,
                 "manual_review_needed":["independent final Gemini audit not completed",
                                         "review final layout and image context",
                                         "verify Cloudflare generation terms"],
                 "source_attributions_file": credits.name}
        status_file=folder/"Статус финальной редакции.json"
        status_file.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        files=[final_md,final_html,final_docx,credits,status_file]
        _git_commit(root,git,env,[v.relative_to(root).as_posix() for v in files],
                    "Private science fact-corrected final editorial package with seven mixed images")
        print(json.dumps({"status":state["status"],"photos":5,"klein":2,
                          "chars":checks["chars_without_spaces"],
                          "private_dir":f"{ROOT_DIR}/{day}/science-01",
                          "publication_authorized":False}, ensure_ascii=False))
        return state

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--date",required=True)
    args=parser.parse_args()
    finish(args.date,os.environ.get("EDITORIAL_PRIVATE_TOKEN"))
