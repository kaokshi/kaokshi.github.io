#!/usr/bin/env python3
"""Non-blocking Russian readability and reader-interest diagnostics.

ruTS: actual optional Python library; no false 'completed' when missing.
ru-text and russian-editor-skill: independently authored editorial checks,
not installation or execution of those external skills.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from editorial_length import extract_text

RULE_SOURCES = {
    "ruts": "https://github.com/SergeyShk/ruTS",
    "ru_text_guidance": "https://github.com/talkstream/ru-text",
    "russian_editor_guidance": "https://github.com/SingeltonAttero/russian-editor-skill",
}
# Short, deliberately narrow editorial cues; not an extracted skill catalogue.
FORMULAE = (
    ("empty_introduction", re.compile(r"^(?:в современном мире|ни для кого не секрет|стоит отметить|в этой статье мы)", re.I)),
    ("bureaucratic_phrase", re.compile(r"\b(?:в целях осуществления|на сегодняшний день|в рамках данного|имеет место быть)\b", re.I)),
    ("formulaic_transition", re.compile(r"\b(?:таким образом|подводя итоги|в заключение)\b", re.I)),
)
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁ«\"])")
WORD_RE = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)?", re.UNICODE)


def _finite(number):
    return (round(float(number), 4) if isinstance(number, (int, float))
            and not isinstance(number, bool) and math.isfinite(number) else None)


def heuristics(text, channel):
    """Return quoted, situated advisory findings; never rewrite automatically."""
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    findings = []
    for i, para in enumerate(paragraphs, 1):
        for rule, regex in FORMULAE:
            for match in list(regex.finditer(para))[:2]:
                findings.append({"rule": rule, "paragraph": i,
                                 "quote": match.group(0), "advisory": True})
    for n, sentence in enumerate(SENTENCE_RE.split(text), 1):
        if len(WORD_RE.findall(sentence)) > 38:
            findings.append({"rule": "long_sentence", "sentence": n,
                             "words": len(WORD_RE.findall(sentence)),
                             "quote": sentence.strip()[:160], "advisory": True})
    result = {"status": "completed", "channel": channel,
              "paragraphs": len(paragraphs), "findings": findings[:25],
              "interpretation": "Editorial heuristics are suggestions, not proof of poor writing or AI authorship"}
    return result


def ruts_metrics(text, channel):
    """Real ruTS metrics, no guessed fallback that masquerades as ruTS."""
    try:
        from ruts import BasicStats, ReadabilityStats, DiversityStats
    except ImportError:
        return {"status": "not_installed", "reason": "Install package ruts separately"}
    try:
        basic = BasicStats(text).get_stats()
        readability = ReadabilityStats(text)
        diversity = DiversityStats(text)
        return {"status": "completed", "engine": "ruts",
                "channel": channel,
                "n_words": basic.get("n_words"),
                "n_sentences": basic.get("n_sents"),
                "mean_readability_flesch": _finite(readability.flesch_reading_easy),
                "lexical_ttr": _finite(diversity.ttr),
                "warning": ("Formulas describe surface text only; compare matched genres and lengths. "
                            "None of these scores measures interest or human authorship.")}
    except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as exc:
        return {"status": "error", "reason": type(exc).__name__}


def analyze(article):
    text = extract_text(article.get("body_html", ""))
    if not text.strip():
        raise ValueError("Article has no visible text")
    return {"schema": "zen-reader-interest-diagnostic-v1",
            "slug": article.get("slug"), "channel": article.get("channel"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "ruts": ruts_metrics(text, article.get("channel")),
            "editorial_guidance": heuristics(text, article.get("channel")),
            "reference_projects": RULE_SOURCES,
            "is_publication_gate": False, "is_authorship_detector": False,
            "limitations": "No licensed third-party skills are executed; linked skills only inform custom reviewer guidance"}


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--article", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    rows = []
    for path in args.article:
        article = json.loads(path.read_text(encoding="utf-8"))
        rows.append(analyze(article))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(json.dumps({"slug":row["slug"],"ruts":row["ruts"]["status"],
                          "findings":len(row["editorial_guidance"]["findings"]),
                          "words":row["ruts"].get("n_words"),
                          "flesch":row["ruts"].get("mean_readability_flesch"),
                          "ttr":row["ruts"].get("lexical_ttr")},ensure_ascii=False))


if __name__ == "__main__":
    main()
