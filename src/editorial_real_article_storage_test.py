"""Archive a real, previously drafted film article privately for owner-only review.

This diagnostic uses repository-local source material and images, not providers.
It must never change the public article, release controls, or production schedule.
"""
import base64
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from docx.shared import Inches

from .editorial_delivery import owner_key, state_key, verify_review
from .editorial_encrypted_review import encrypt
from .editorial_memory import digest
from .editorial_providers import Blocked
from .editorial_storage_probe import EXPECTED_REPOSITORY, api

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "content/articles/cinema-01-truman-show-real-photos.json"
ARTICLE_ID = "cinema-01"
DAY = "2026-11-23"


class ArticleBody(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pending = None
        self.events = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "h2", "h3"}:
            self.pending = [tag, []]
        elif tag == "img":
            self.events.append(("image", dict(attrs).get("src", "")))

    def handle_data(self, data):
        if self.pending is not None:
            self.pending[1].append(data)

    def handle_endtag(self, tag):
        if self.pending is not None and self.pending[0] == tag:
            txt = "".join(self.pending[1]).strip()
            if txt:
                self.events.append((tag, txt))
            self.pending = None


def archive_real_article():
    article = json.loads(SOURCE.read_text(encoding="utf-8"))
    if (article.get("channel") != "cinema" or article.get("release_hold") is not True
            or article.get("publication_authorized") is not False):
        raise Blocked("real_article_release_hold_required")
    photos = article.get("image_files", [])
    if len(photos) != 8 or len(set(photos)) != 8:
        raise Blocked("real_article_requires_eight_local_images")
    if not all(isinstance(p, str) and p.startswith("content/assets/truman/")
               and re.fullmatch(r"[a-zA-Z0-9_./-]+", p) for p in photos):
        raise Blocked("invalid_real_article_asset_paths")

    parser = ArticleBody()
    parser.feed(article["body_html"])
    img_refs = [src for kind, src in parser.events if kind == "image"]
    assets = {}
    html_body = article["body_html"]
    for relative in photos:
        name = Path(relative).name
        raw = (ROOT / relative).read_bytes()
        if not raw or len(raw) > 5_000_000:
            raise Blocked("invalid_real_article_image_bytes")
        assets[name] = raw
        orig = "/assets/truman/" + name
        if orig not in img_refs:
            raise Blocked("missing_real_article_image_in_body")
        html_body = html_body.replace('src="' + orig + '"', 'src="images/' + name + '"')
    if set(img_refs) != {"/assets/truman/" + n for n in assets}:
        raise Blocked("unexpected_image_reference")

    from html import escape
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>'
            + escape(article["title"]) + '</title><h1>' + escape(article["title"])
            + '</h1><main>' + html_body + '</main></html>').encode("utf-8")
    doc = Document()
    doc.add_heading(article["title"], 0)
    canonical_lines = []
    for kind, content in parser.events:
        if kind == "image":
            name = Path(content).name
            doc.add_picture(BytesIO(assets[name]), width=Inches(4.8))
        elif kind in {"h2", "h3"}:
            doc.add_heading(content, 2 if kind == "h2" else 3)
            canonical_lines.append("**" + content + "**")
        elif kind == "p":
            doc.add_paragraph(content)
            canonical_lines.append(content)
    doc_buffer = BytesIO()
    doc.save(doc_buffer)
    text = "\n".join(canonical_lines).encode("utf-8")
    raw_chars = len(re.sub(r"\s+", "", text.decode("utf-8")))
    image_rows = [{"file": name, "sha256": digest(raw)} for name, raw in assets.items()]
    qa = {
        "title": article["title"],
        "text_sha256": digest(text),
        "images": image_rows,
        "article_checks": {"characters_without_spaces": raw_chars, "length_exception_reason": ""},
        "storage_only": True,
        "quality_status": "requires_editorial_review",
        "rights_status": "requires_independent_rights_confirmation",
        "length_blocked": raw_chars > 7000,
        "publication_authorized": False,
        "source_slug": article["slug"],
    }
    files = {
        "Статья.docx": doc_buffer.getvalue(),
        "Статья.html": html,
        "Текст для SHA-256.txt": text,
        "Статус и проверки.json": json.dumps(qa, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    files.update({"images/" + name: raw for name, raw in assets.items()})
    manifest = "".join(digest(raw) + "  " + name + "\n" for name, raw in sorted(files.items()))
    index = {"day": DAY, "articles": {ARTICLE_ID: "rights_review_pending"},
             "storage_test": True, "publication_authorized": False}

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("Статус комплекта.json", json.dumps(index, ensure_ascii=False).encode("utf-8"))
        for name, raw in files.items():
            archive.writestr(ARTICLE_ID + "/" + name, raw)
        archive.writestr(ARTICLE_ID + "/SHA256SUMS.txt", manifest.encode("utf-8"))
    result = buffer.getvalue()
    verified = verify_review(result, allow_incomplete=True)
    if verified["date"] != DAY or verified["package_sha256"] != digest(result):
        raise Blocked("real_article_archive_verification_failed")
    return result, {"article_id": ARTICLE_ID, "image_count": len(assets),
                    "characters_without_spaces": raw_chars, "archive_sha256": digest(result),
                    "length_blocked": raw_chars > 7000}


def main():
    token = os.environ.get("EDITORIAL_PRIVATE_TOKEN", "")
    repository = os.environ.get("EDITORIAL_PRIVATE_REPOSITORY", "")
    if not token or repository != EXPECTED_REPOSITORY:
        raise Blocked("private_real_article_store_not_configured")
    public_key = owner_key()
    if state_key() is None:
        raise Blocked("state_encryption_key_missing")
    # Check the exact private destination before reading the real article.
    metadata = api(token, f"repos/{repository}")
    if metadata.get("visibility") != "private" or metadata.get("full_name") != repository:
        raise Blocked("real_article_destination_not_private")
    raw, report = archive_real_article()
    sealed = encrypt(raw, public_key)
    if sealed.startswith(b"PK") or sealed == raw:
        raise Blocked("real_article_encryption_failure")
    path = "editorial-sealed/storage-tests/" + DAY + "/" + ARTICLE_ID + "-" + uuid4().hex + ".enc"
    endpoint = f"repos/{repository}/contents/{path}"
    saved = api(token, endpoint, method="PUT", data={
        "message": "Encrypted private review of existing article (not for publication)",
        "content": base64.b64encode(sealed).decode("ascii")})
    commit_sha = (saved.get("commit") or {}).get("sha")
    if not isinstance(commit_sha, str) or not re.fullmatch("[0-9a-f]{40}", commit_sha):
        raise Blocked("real_article_storage_write_unconfirmed")
    returned = api(token, endpoint)
    try:
        decoded = base64.b64decode(returned["content"], validate=False)
    except (ValueError, KeyError, TypeError):
        raise Blocked("real_article_storage_read_invalid") from None
    if digest(decoded) != digest(sealed):
        raise Blocked("real_article_storage_ciphertext_sha_mismatch")
    print(json.dumps({
        "status": "real_article_encrypted_storage_verified",
        **report,
        "encrypted_sha256": digest(sealed),
        "private_path": path,
        "private_commit": commit_sha,
        "owner_decryption_required": True,
        "publication_authorized": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
