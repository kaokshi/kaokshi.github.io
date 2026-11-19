#!/usr/bin/env python3
"""Read-only three-channel editorial quality audit. No approval or publication writes.

Local heuristics are advisory. Explicit flags for unavailable integrations are never
reported as a successful LanguageTool/URL/license/fact check.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from editorial_ai_check import extract_text, ROOT, CONTENT

CHANNELS = {"cinema", "legends", "science"}
MIN_CHARS = 3000
STOPWORDS = {"который", "которая", "которые", "потому", "однако", "сейчас", "только", "после", "перед", "через", "когда", "потом", "снова", "можно", "очень", "этого", "также"}
CLICHES = ("не просто", "на самом деле", "стоит отметить", "важно понимать",
           "в современном мире", "таким образом", "в заключение", "погрузимся в")


def _tokens(text):
    try:
        from razdel import tokenize
        return [x.text.lower() for x in tokenize(text) if any(ch.isalpha() for ch in x.text)]
    except ImportError:
        return re.findall(r"[а-яёa-z]+", text.lower())


def text_style(text):
    tokens = _tokens(text)
    long_sentences = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for i, sentence in enumerate(sentences, 1):
        n = len(_tokens(sentence))
        if n > 35:
            long_sentences.append({"sentence": i, "words": n})
    words = [x for x in tokens if len(x) >= 5 and x not in STOPWORDS]
    counts = Counter(words)
    repeated = [{"word": w, "count": n} for w, n in counts.most_common(15) if n >= 8]
    return {
        "tokenizer": "razdel" if _razdel_available() else "regex_fallback",
        "words": len(tokens),
        "long_sentences": long_sentences[:30],
        "repeated_words": repeated[:12],
        "cliche_occurrences": {term: len(re.findall(re.escape(term), text.lower())) for term in CLICHES if term in text.lower()},
        "note": "Suggestions to edit for clarity, not authorship evidence.",
    }


def _razdel_available():
    try:
        import razdel
        return True
    except ImportError:
        return False


def article_urls(article):
    urls = []
    for x in re.findall(r'https?://[^\s"<>]+', article.get("body_html", "")):
        urls.append(x.rstrip(".,);"))
    for item in article.get("image_sources", []):
        if isinstance(item, dict) and item.get("source_page"):
            urls.append(item["source_page"])
    for item in article.get("sources", []):
        if isinstance(item, dict):
            for key in ("url", "source_page"):
                if item.get(key):
                    urls.append(item[key])
        elif isinstance(item, str) and item.startswith("http"):
            urls.append(item)
    return sorted(set(x for x in urls if urlparse(x).scheme in ("https", "http")))


def inspect_article(path):
    article = json.loads(path.read_text(encoding="utf-8"))
    channel = article.get("channel")
    text = extract_text(article.get("body_html", ""))
    chars = len(re.sub(r"\s", "", text))
    imgs = article.get("image_files") or []
    sources = article.get("image_sources") or []
    photo_refs = [item.get("file") for item in sources if isinstance(item, dict)]
    problems = []
    if channel not in CHANNELS:
        problems.append("unknown_channel")
    if chars < MIN_CHARS:
        problems.append("short_article")
    if len(imgs) < 7:
        problems.append("fewer_than_seven_images")
    if len(set(imgs)) != len(imgs):
        problems.append("repeated_image_file")
    missing = [s for s in imgs if not (ROOT / s).is_file()]
    if missing:
        problems.append("missing_image_files")
    if imgs and (not article.get("rights_reviewed") or not article.get("rights_basis")):
        problems.append("image_rights_need_manual_review")
    if channel == "cinema":
        # Fact-checking plot details and licensing film stills remain HUMAN tasks.
        if not sources or any(s not in photo_refs for s in imgs):
            problems.append("film_still_source_traceability_incomplete")
        if not article.get("rights_reviewed"):
            problems.append("film_still_rights_unverified")
    elif channel == "science":
        if not article_urls(article):
            problems.append("science_sources_not_recorded")
    elif channel == "legends":
        if re.search(r"\b(основано на реальных событиях|подлинная история)\b", text.lower()):
            problems.append("fiction_claim_of_reality_review")
    return {
        "slug": article.get("slug", path.stem),
        "channel": channel,
        "characters_without_whitespace": chars,
        "images": {"count": len(imgs), "missing": missing, "sources_recorded": len(sources),
                   "rights_reviewed": article.get("rights_reviewed") is True},
        "urls": article_urls(article),
        "style": text_style(text),
        "warnings": sorted(set(problems)),
        "publication_authorized": False,
        "human_review_needed": channel in ("cinema", "science") or bool(problems),
    }


def similarity_report(entries):
    """Internal duplicate detection across all three channels; not web plagiarism."""
    documents = []
    for path in entries:
        a = json.loads(path.read_text(encoding="utf-8"))
        text = extract_text(a.get("body_html", "")).lower()
        words = set(re.findall(r"[а-яё]{5,}", text))
        documents.append((a.get("slug", path.stem), a.get("title", ""), words))
    similar = []
    for i, (slug, title, words) in enumerate(documents):
        for slug2, title2, words2 in documents[i + 1:]:
            if not words or not words2:
                continue
            overlap = len(words & words2) / len(words | words2)
            title_score = SequenceMatcher(None, title.lower(), title2.lower()).ratio()
            if overlap >= .57 or title_score >= .82:
                similar.append({"a": slug, "b": slug2, "lexical_jaccard": round(overlap, 4),
                                "title_similarity": round(title_score, 4)})
    return similar


def image_hash_audit(paths):
    """Exact hashes for all files; perceptual distance if Pillow is installed."""
    records = []
    for path in paths:
        a = json.loads(path.read_text(encoding="utf-8"))
        for filename in a.get("image_files", []):
            f = ROOT / filename
            if f.is_file():
                records.append((a.get("slug", path.stem), filename, f))
    duplicates = []
    seen = {}
    for slug, name, path in records:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen and seen[digest][1] != name:
            duplicates.append({"method": "sha256", "articles": [seen[digest][0], slug],
                               "images": [seen[digest][1], name]})
        seen[digest] = (slug, name)
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return {"sha256_duplicates": duplicates, "near_duplicate_status": "unavailable (Pillow/imagehash not installed)"}
    hashes = []
    for slug, name, path in records:
        try:
            with Image.open(path) as img:
                hashes.append((slug, name, imagehash.phash(img)))
        except (OSError, ValueError):
            continue
    for i, (slug, name, h) in enumerate(hashes):
        for slug2, name2, h2 in hashes[i + 1:]:
            dist = h - h2
            if name != name2 and dist <= 5:
                duplicates.append({"method": "perceptual_hash", "distance": dist,
                                   "articles": [slug, slug2], "images": [name, name2]})
    return {"duplicates": duplicates, "near_duplicate_status": "completed", "scanned_images": len(hashes)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Check a single existing article")
    parser.add_argument("--output", default=str(ROOT / "editorial-quality-reports"))
    args = parser.parse_args()
    all_files = sorted(CONTENT.glob("*.json"))
    selected = [p for p in all_files if p.stem == args.slug] if args.slug else all_files
    if not selected:
        parser.error("No matching articles found")
    dest = Path(args.output)
    dest.mkdir(parents=True, exist_ok=True)
    results = [inspect_article(p) for p in selected]
    urls = sorted(set(u for row in results for u in row["urls"]))
    # URL checker consumes only external URLs provided by articles.
    (dest / "links.txt").write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "schema": "editorial-quality-v1",
        "channel_coverage": sorted(set(row["channel"] for row in results if row["channel"])),
        "results": results,
        "internal_similar_articles": similarity_report(all_files),
        "image_duplicates": image_hash_audit(all_files),
        "external_links_count": len(urls),
        "external_links_verified": False,
        "grammar_verified": False,
        "film_facts_verified": False,
        "science_facts_verified": False,
        "image_rights_verified_by_this_check": False,
        "ai_detection_separate_workflow": "editorial-ai-checks.yml",
        "publication_authorized": False,
    }
    (dest / "quality-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"articles": len(results), "channels": report["channel_coverage"],
                      "warnings": sum(len(x["warnings"]) for x in results),
                      "similarity_pairs": len(report["internal_similar_articles"]),
                      "links": len(urls), "image_duplicates": len(report["image_duplicates"].get("duplicates", [])),
                      "publication_authorized": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
