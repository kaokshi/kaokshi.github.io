#!/usr/bin/env python3
"""Optional, non-blocking reading-quality notes and OpenAlex evidence discovery.

Never modifies source articles, their acceptance/AI scores, or publication flags.
Scientific sources are discovery leads, not proof a paragraph is correct.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from editorial_ai_check import ROOT, CONTENT, extract_text

PRESETS = {"legends": "fiction", "science": "academic", "cinema": "plainrussian"}
CURATED_RESEARCH = {
    "science-01-krasnye-spraity": "red sprites upper atmosphere"
}

def readability(article, tool=None):
    channel = article.get("channel")
    if channel not in PRESETS:
        return {"status": "unsupported_channel"}
    try:
        if tool is None:
            from ruts import ReadabilityStats
            tool = ReadabilityStats
        text = extract_text(article.get("body_html", ""))
        score = tool(text, preset=PRESETS[channel])
        return {
            "status": "completed",
            "channel": channel,
            "preset": PRESETS[channel],
            "flesch_reading_easy": round(float(score.flesch_reading_easy), 2),
            "purpose": "advisory",
            "threshold": None,
            "editor_instruction": ("Перечитать сложные отрывки; если они перегружают статью, "
                                   "упростить их по смыслу и ритму, сохраняя голос автора. "
                                   "Не переписывать автоматически по числу."),
        }
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)[:220], "purpose": "advisory"}

def science_sources(article, works_client=None):
    if article.get("channel") != "science":
        return {"status": "not_applicable"}
    slug = article.get("slug")
    query = CURATED_RESEARCH.get(slug)
    if not query:
        return {"status": "query_needs_editor", "note": "A verified topic query has not been approved; do not guess sources."}
    try:
        if works_client is None:
            from pyalex import Works
            works_client = Works
        entries = works_client().search(query).get(per_page=5)
        works = []
        for entry in entries:
            works.append({
                "title": entry.get("display_name"),
                "year": entry.get("publication_year"),
                "doi": entry.get("doi"),
                "openalex_id": entry.get("id"),
            })
        return {
            "status": "completed",
            "query": query,
            "works": works,
            "fact_verified": False,
            "note": "These are relevant scholarly candidates, not direct evidence of every claim.",
        }
    except Exception as exc:
        return {"status": "unavailable", "query": query, "reason": str(exc)[:220], "fact_verified": False}

def inspect(articles, research=True):
    results = []
    for path in articles:
        a = json.loads(path.read_text(encoding="utf-8"))
        results.append({
            "slug": a.get("slug", path.stem),
            "channel": a.get("channel"),
            "readability": readability(a),
            "science_sources": science_sources(a) if research else {"status": "not_run"},
            "publication_authorized": False,
        })
    return {
        "schema": "editorial-enhancements-v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "publication_authorized": False,
        "article_modified": False,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", action="append")
    parser.add_argument("--output", default=str(ROOT / "editorial-enhancement-reports"))
    args = parser.parse_args()
    if args.slug:
        paths = []
        for slug in dict.fromkeys(args.slug):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
                parser.error("Invalid slug")
            path = CONTENT / (slug + ".json")
            if not path.is_file():
                parser.error("Article not found: " + slug)
            paths.append(path)
    else:
        paths = [
            CONTENT / "legends-01-prohodnaya-17.json",
            CONTENT / "science-01-krasnye-spraity.json",
            CONTENT / "cinema-01-truman-show-real-photos.json",
        ]
    report = inspect(paths)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "enhancements.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for a in report["results"]:
        print(json.dumps(a, ensure_ascii=False))
    print("publication_authorized=false; readability never blocks release")

if __name__ == "__main__":
    main()
