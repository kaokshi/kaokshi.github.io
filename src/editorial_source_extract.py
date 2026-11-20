#!/usr/bin/env python3
"""Inspect publicly accessible cited pages with Trafilatura, no content republication.

Evidence is metadata about what was accessible, not a fact-check verdict.
Source text is extracted transiently; only bounded snippets are included in the
ephemeral diagnostic report. Runs separately from any publication pathway.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from editorial_ai_check import CONTENT, ROOT
from editorial_quality import article_urls

MAX_URLS = 12


def inspect(urls, timeout=15, fetch=None, extract=None):
    if fetch is None or extract is None:
        import requests
        import trafilatura
        def real_fetch(url):
            response = requests.get(url, timeout=timeout,
                    headers={"User-Agent": "ZenEditorialSourceReview/1.0"},
                    allow_redirects=True)
            response.raise_for_status()
            return response.text, response.url
        fetch = fetch or real_fetch
        extract = extract or (lambda html: trafilatura.extract(html, include_comments=False))
    reports = []
    for url in urls[:MAX_URLS]:
        row = {"source_url": url, "status": "unavailable",
               "extracted_chars": 0, "snippet": None,
               "is_authoritative_for_claim": "not_evaluated"}
        if urlparse(url).scheme != "https":
            row["reason"] = "Non-HTTPS source skipped"
        else:
            try:
                html, resolved = fetch(url)
                text = extract(html)
                row["resolved_url"] = resolved
                if text and len(text.strip()) >= 100:
                    row.update(status="extracted", extracted_chars=len(text),
                               snippet=re.sub(r"\s+", " ", text.strip())[:150])
                else:
                    row["reason"] = "No substantial article text on this page"
            except Exception as exc:
                row["reason"] = type(exc).__name__ + ": " + str(exc)[:180]
        reports.append(row)
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output", default=str(ROOT / "editorial-movie-reports"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        parser.error("Unsafe slug")
    path = CONTENT / (args.slug + ".json")
    if not path.is_file():
        parser.error("Unknown article")
    article = json.loads(path.read_text(encoding="utf-8"))
    sources = article_urls(article)
    reports = inspect(sources)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "editorial-source-extraction-v1",
        "article_slug": args.slug,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources": reports,
        "total": len(sources),
        "scanned": len(reports),
        "extracted": sum(r["status"] == "extracted" for r in reports),
        "fact_checked": False,
        "image_rights_verified": False,
        "publication_authorized": False,
        "notice": "Extracted source snippets are pointers for editorial checking, not proof of any claim.",
    }
    (output / (args.slug + "-sources.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("article_slug", "total", "scanned", "extracted", "fact_checked")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
