#!/usr/bin/env python3
"""Research-only audit of candidate RUSSIAN genre corpora, before training.

Downloads public dataset metadata/content on an ephemeral runner, outputs counts
and provenance warnings only. NEVER uploads texts, training labels for release,
or any site/RSS assets.
"""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

LITERARY = "JoyKad/human-or-aI-littexts"
ZEN = "ru-dataset/dzen-russian-articles"
HISTORIC_CINEMA = "MihaoModea/kinopoisk"
MAX_ROWS = 24000
GHOST_WORDS = (
    "призрак", "привид", "мистик", "проклят", "кладбищ", "покойник",
    "мертвец", "демон", "нечист", "дух", "тень", "страшн", "ужас",
    "сверхъест", "потусторон", "призрач", "пропал без вести", "хоррор",
    "колдов", "ведьм", "ночью", "заброшен", "необъясним", "кошмар",
)
BAD_SUBJECTS = ("научн", "технологи", "природ", "космос", "атмосфер", "физик")


def ru_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) / len(letters) if letters else 0


def check_length(text):
    return isinstance(text, str) and 2000 <= len(text) <= 12000 and ru_ratio(text) > 0.83


def horror_match(text):
    lower = text.lower()
    matched = [w for w in GHOST_WORDS if w in lower]
    return len(matched) >= 3


def genres(ds):
    # Streaming avoids materializing many gigabytes of copyrighted text.
    from datasets import load_dataset
    data = load_dataset(ds, split="train", streaming=True)
    for n, row in enumerate(data):
        if n >= MAX_ROWS:
            break
        yield row


def probe_literary():
    counts = collections.Counter()
    examples = []
    for i, row in enumerate(genres(LITERARY)):
        counts["scanned"] += 1
        text = row.get("text")
        if not check_length(text):
            counts["wrong_length_or_language"] += 1
            continue
        label = row.get("label")
        if label in (0, "0", "human", "Human"):
            name = "human"
        elif label in (1, "1", "ai", "AI"):
            name = "ai"
        else:
            counts["unrecognized_label"] += 1
            continue
        counts["russian_literary_" + name] += 1
        if horror_match(text):
            counts["horror_keyword_proxy_" + name] += 1
            if len(examples) < 15:
                examples.append({"hash": hashlib.sha256(text.encode()).hexdigest()[:16],
                    "label": name, "length": len(text), "horror_keyword_count":
                    sum(k in text.lower() for k in GHOST_WORDS)})
    return {"dataset": LITERARY, "counts": dict(counts), "sample_hashes_only": examples,
        "status": "GENRE_KEYWORD_PROXY_NOT_HUMAN_VERIFIED",
        "warning": "Horror keywords do not establish actual mystery narrative or trustworthy author labels."}


def probe_zen_science():
    # Dataset features include nested comments with inconsistent Arrow types.
    # Parse its published JSON directly instead of using Arrow schema inference.
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(repo_id=ZEN, filename="dzen-data.json", repo_type="dataset")
    with open(path, encoding="utf-8") as reader:
        payload = json.load(reader)
    if isinstance(payload, dict):
        observations = payload.get("data") or payload.get("articles") or payload.get("records") or []
    elif isinstance(payload, list):
        observations = payload
    else:
        raise ValueError("Unsupported Dzen raw file format")
    counts = collections.Counter()
    ids = set()
    for row in observations[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        counts["scanned"] += 1
        category = str(row.get("category") or "").strip().lower()
        if category != "science":
            continue
        counts["science_category"] += 1
        text = row.get("text")
        if not check_length(text):
            counts["science_wrong_length_or_language"] += 1
            continue
        uid = str(row.get("author") or row.get("source") or "").strip()
        if uid:
            ids.add(uid)
        counts["science_long_eligible"] += 1
    return {"dataset": ZEN, "counts": dict(counts),
        "distinct_source_or_author_claims": len(ids),
        "status": "SCIENCE_GENRE_MATCH_AUTHORSHIP_NOT_VERIFIED",
        "warning": "A Dzen article category is not proof that it was written by a human; extraction uses an LLM."}


def probe_cinema():
    from datasets import load_dataset
    data = load_dataset(HISTORIC_CINEMA, split="train", streaming=True)
    counts = collections.Counter()
    groups = set()
    for i, row in enumerate(data):
        if i >= MAX_ROWS:
            break
        counts["scanned"] += 1
        text = row.get("content")
        date = str(row.get("date") or "")
        title = str(row.get("movie_name") or "").strip()
        if not (check_length(text) and date[:4].isdigit() and date[:4] <= "2012" and title):
            continue
        groups.add(title)
        counts["historical_film_reviews"] += 1
    return {"dataset": HISTORIC_CINEMA, "counts": dict(counts),
        "distinct_movie_groups": len(groups),
        "status": "FILM_REVIEWS_NOT_PLOT_RECAPS",
        "warning": "Historic reviews are not long spoiler-rich recaps. These cannot validate cinema genre fit."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="editorial-genre-probe/report.json")
    args = parser.parse_args()
    result = {
        "schema": "genre-candidate-inventory-v1",
        "max_rows_per_corpus": MAX_ROWS,
        "release_authorized": False, "publication_authorized": False,
        "threshold30_enabled": False, "corpora": [], "errors": []
    }
    for name, fn in (("literary", probe_literary), ("science", probe_zen_science), ("cinema", probe_cinema)):
        try:
            item = fn()
            result["corpora"].append(item)
            print("GENRE_CANDIDATE", json.dumps(item, ensure_ascii=False), flush=True)
        except Exception as exc:
            error = {"source": name, "error_type": type(exc).__name__,
                     "reason": str(exc)[:300]}
            result["errors"].append(error)
            print("GENRE_CANDIDATE_ERROR", json.dumps(error, ensure_ascii=False), flush=True)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result["errors"]:
        raise SystemExit("Candidate corpus audit incomplete; training labels must not be assumed")


if __name__ == "__main__":
    main()
