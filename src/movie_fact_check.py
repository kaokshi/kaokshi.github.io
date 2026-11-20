#!/usr/bin/env python3
"""Evidence-aided movie review without downloading film video.

This is a triage report, NOT automated verification of the plot or of what a
particular film still depicts. Unavailable third-party data stays unavailable.
All article files and publication flags are read-only.
"""
import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

from editorial_ai_check import ROOT, CONTENT, extract_text

REFS = ROOT / "config" / "movie_reference_index.json"
REPORTS = ROOT / "editorial-movie-reports"
USER_AGENT = "ZenEditorialMovieFactCheck/0.1 (editorial source evidence; read-only)"


def load_reference(slug, path=REFS):
    if not path.is_file():
        return None
    refs = json.loads(path.read_text(encoding="utf-8"))
    return refs.get(slug)


def fetch_wikipedia(title, lang="ru", timeout=18):
    """Fetch Wikipedia plain-text extract. No scraping behind paywalls."""
    try:
        import requests
        url = f"https://{lang}.wikipedia.org/w/api.php"
        response = requests.get(
            url,
            params={"action": "query", "prop": "extracts", "explaintext": 1,
                    "format": "json", "formatversion": 2, "titles": title,
                    "redirects": 1},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        page = pages[0] if pages else {}
        extract = page.get("extract", "")
        if page.get("missing") or not extract.strip():
            return {"status": "unavailable", "reason": "page missing or empty", "url": response.url}
        # Wikipedia sections vary by language. Preserve full extract and compare
        # paragraphs; do not claim snippets prove what happens on screen.
        return {"status": "completed", "url": response.url, "title": page.get("title"),
                "extract": extract, "length": len(extract)}
    except Exception as exc:
        return {"status": "unavailable", "reason": str(exc)[:320],
                "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"}


def fetch_cinemagoer(imdb_id, timeout=50):
    """Isolated IMDb metadata lookup. Never trust an unavailable lookup."""
    if not re.fullmatch(r"tt\d{7,9}", imdb_id or ""):
        return {"status": "unavailable", "reason": "IMDb title ID missing or malformed"}
    script = """
import json, sys
try:
    from imdb import Cinemagoer
    movie = Cinemagoer().get_movie(sys.argv[1][2:], info=['main','plot'])
    keys = ('title','original title','year','genres','plot')
    out = {key: movie.get(key) for key in keys if movie.get(key)}
    directors = movie.get('directors', [])
    out['directors'] = [str(x) for x in directors[:5]]
    print(json.dumps(out, ensure_ascii=False, default=str))
except Exception as exc:
    print(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)[:300]}))
"""
    try:
        run = subprocess.run([sys.executable, "-c", script, imdb_id],
                             capture_output=True, text=True, timeout=timeout)
        if run.returncode != 0:
            return {"status": "unavailable", "reason": (run.stderr or run.stdout)[-350:]}
        data = json.loads(run.stdout.strip())
        if data.get("error") or not data.get("title"):
            return {"status": "unavailable", "reason": data.get("error", "IMDb metadata empty")}
        return {"status": "completed", "data": data,
                "source": f"https://www.imdb.com/title/{imdb_id}/"}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": str(exc)[:350]}


def sections(html):
    """Return article's semantic sections and image positions in their original order."""
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.current_title = "Вступление"
            self.current_text = []
            self.parts = []
            self.tag = ""
            self.images = []
            self.figcaption = None

        def flush(self):
            if self.current_text:
                text = " ".join(self.current_text).strip()
                if text:
                    self.parts.append({"section": self.current_title, "text": text})
            self.current_text = []

        def handle_starttag(self, tag, attrs):
            self.tag = tag
            if tag in ("h2", "h3"):
                self.flush()
            if tag == "img":
                image = dict(attrs)
                self.images.append({"src": image.get("src", ""),
                                    "alt": image.get("alt", ""),
                                    "section": self.current_title})
            if tag == "figcaption":
                self.figcaption = []

        def handle_data(self, data):
            data = data.strip()
            if not data:
                return
            if self.tag in ("h2", "h3"):
                self.current_title = data
            elif self.tag == "figcaption":
                if self.figcaption is not None:
                    self.figcaption.append(data)
            elif self.tag in ("p", "strong", "em"):
                self.current_text.append(data)

        def handle_endtag(self, tag):
            if tag == "figcaption" and self.figcaption is not None:
                if self.images:
                    self.images[-1]["caption"] = " ".join(self.figcaption)
                self.figcaption = None
            self.tag = ""

    parser = Parser()
    parser.feed(html)
    parser.flush()
    return parser.parts, parser.images


def normalized_words(text):
    return set(re.findall(r"[а-яё]{4,}", text.lower()))


def plot_only(extract):
    """Isolate the film plot from encyclopedia metadata and critical reception."""
    match = re.search(r"(?ms)^==\s*(?:Сюжет|Plot)\s*==\s*(.*?)(?=^==\s*[^=]|\Z)", extract)
    return match.group(1).strip() if match else ""


def metadata_from_wikipedia(source, reference):
    if source.get("status") != "completed" or not reference:
        return {"status": "unavailable"}
    opening = source["extract"].split("==")[0][:3500]
    year = reference.get("original_year")
    director_ru = reference.get("director_ru", "")
    return {
        "status": "evaluated",
        "year_appears_in_encyclopedia_intro": bool(year and str(year) in opening),
        "director_appears_in_encyclopedia_intro": bool(director_ru and director_ru.lower() in opening.lower()),
        "note": "Text mentions only; not independent primary-source authentication."
    }


def comparison(article_sections, source):
    """Weak lexical evidence; never label absent plot detail as factually false."""
    if source.get("status") != "completed":
        return [{"section": row["section"], "status": "source_unavailable",
                 "source": source.get("url")} for row in article_sections]
    whole = plot_only(source["extract"])
    if not whole:
        return [{"section": row["section"], "status": "plot_section_unavailable",
                 "source": source.get("url")} for row in article_sections]
    # Wikipedia may contain English plot under other headings. Russian source
    # for Russian article is primary; all scores are review hints, not truth.
    chunks = [x.strip() for x in re.split(r"\n\s*\n+", whole) if len(x.strip()) > 70]
    if not chunks:
        chunks = [whole]
    out = []
    for row in article_sections:
        if re.search(r"(?i)^(моё мнение|почему|джим керри)", row["section"]):
            out.append({"section": row["section"], "status": "commentary_not_suitable_for_plot_lookup",
                        "source_url": source.get("url"),
                        "notice": "Editorial commentary and acting analysis cannot be established from plot summary."})
            continue
        text = row["text"]
        words = normalized_words(text)
        candidates = []
        for idx, part in enumerate(chunks):
            srcwords = normalized_words(part)
            overlap = len(words & srcwords) / max(1, min(len(words), len(srcwords)))
            candidates.append((overlap, idx, part))
        score, idx, matched = max(candidates, default=(0, -1, ""))
        out.append({
            "section": row["section"],
            "status": "plot_textual_overlap" if score >= .23 else "requires_editor_review",
            "lexical_overlap": round(score, 3),
            "source_url": source.get("url"),
            "source_extract_excerpt": matched[:240],
            "notice": "Lexical overlap is NOT validation of narrative events or chronology."
        })
    return out


def stills(article, html_images, reference):
    """Validate caption/source bookkeeping, not film-image visual identity."""
    image_files = article.get("image_files") or []
    sources = {x.get("file"): x for x in article.get("image_sources", [])
               if isinstance(x, dict) and x.get("file")}
    results = []
    imdb_id = reference.get("imdb_id", "") if reference else ""
    for idx, path in enumerate(image_files):
        item = sources.get(path, {})
        matching = [x for x in html_images
                    if Path(x.get("src", "")).name == Path(path).name]
        url = item.get("source_page", "")
        ref_id = re.search(r"/title/(tt\d{7,9})/", url)
        warnings = []
        if not (ROOT / path).is_file():
            warnings.append("file_missing")
        if not url.startswith(("https://", "http://")):
            warnings.append("source_url_missing")
        if not matching:
            warnings.append("not_used_in_article")
        if ref_id and imdb_id and ref_id.group(1) != imdb_id:
            warnings.append("imdb_title_id_conflict")
        recorded = item.get("caption", "").strip()
        shown = matching[0].get("caption", "").strip() if matching else ""
        if recorded and shown and recorded != shown:
            warnings.append("source_caption_differs_from_article")
        results.append({
            "index": idx + 1,
            "file": path,
            "source_url": url or None,
            "source_caption": recorded or None,
            "article_caption": shown or None,
            "article_section": matching[0].get("section") if matching else None,
            "structural_status": "review" if warnings else "metadata_consistent",
            "warnings": warnings,
            "visual_scene_match": "not_verified",
            "film_identity_from_pixels": "not_verified",
            "image_rights": "not_verified_by_this_tool",
            "reason": "No film video or verified reference-frame dataset; captions and URLs do not prove on-screen scene identity."
        })
    return results


def analyze(path, use_imdb=False):
    article = json.loads(path.read_text(encoding="utf-8"))
    if article.get("channel") != "cinema":
        raise ValueError("Movie Fact Check only works for cinema")
    slug = article.get("slug", path.stem)
    ref = load_reference(slug)
    article_sections, html_images = sections(article.get("body_html", ""))
    if ref:
        wiki = fetch_wikipedia(ref.get("wikipedia_title_ru", ref["film_title"]), "ru")
        imdb = fetch_cinemagoer(ref.get("imdb_id"), timeout=40) if use_imdb else {"status": "not_run"}
    else:
        wiki = {"status": "unavailable", "reason": "No film reference metadata indexed"}
        imdb = {"status": "unavailable", "reason": "No IMDb ID indexed"}
    detailed = comparison(article_sections, wiki)
    images = stills(article, html_images, ref)
    imdb_data = imdb.get("data") or {}
    metadata = {"expected": ref, "imdb_lookup": imdb, "wikipedia_metadata": metadata_from_wikipedia(wiki, ref), "status": "needs_human_review"}
    if imdb.get("status") == "completed":
        year = imdb_data.get("year")
        metadata["year_matches_reference"] = (year == ref["original_year"]) if isinstance(year, int) else None
        metadata["director_names_recorded"] = imdb_data.get("directors", [])
    return {
        "schema": "movie-evidence-pilot-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "article_slug": slug, "channel": "cinema",
        "source_file": str(path.relative_to(ROOT)),
        "reference_catalog": metadata,
        "wikipedia": {k: v for k, v in wiki.items() if k != "extract"},
        "article_sections": detailed,
        "stills": images,
        "summary": {
            "sections_reviewed": len(detailed),
            "sections_with_textual_overlap": sum(x["status"] == "plot_textual_overlap" for x in detailed),
            "sections_needing_review": sum(x["status"] == "requires_editor_review" for x in detailed),
            "commentary_sections_excluded": sum(x["status"] == "commentary_not_suitable_for_plot_lookup" for x in detailed),
            "images_indexed": len(images),
            "images_with_metadata_warnings": sum(bool(x["warnings"]) for x in images),
            "film_plot_verified": False,
            "frame_scene_match_verified": False,
            "article_authorized_for_publication": False,
        },
        "limits": [
            "Wikipedia/IMDb are secondary sources and may omit scenes or be inaccurate.",
            "Keyword/lexical similarity is a triage hint, not factual verification.",
            "Still-frame mapping checks existing filenames/captions/source URLs only; it does not recognise image content.",
            "No full film, audio, subtitles or screenplay were downloaded.",
            "The tool never changes original articles, editorial approval, rights, or publication state."
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--imdb", action="store_true", help="Optional Cinemagoer online metadata lookup")
    parser.add_argument("--output", default=str(REPORTS))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        parser.error("Invalid slug")
    path = CONTENT / (args.slug + ".json")
    if not path.is_file():
        parser.error("Article not found")
    report = analyze(path, args.imdb)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    file = out / f"{args.slug}.json"
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(file), "slug": report["article_slug"],
                      "wikipedia": report["wikipedia"].get("status"),
                      "imdb": report["reference_catalog"]["imdb_lookup"].get("status"),
                      "imdb_reason": report["reference_catalog"]["imdb_lookup"].get("reason"),
                      **report["summary"]}, ensure_ascii=False))
    # Incomplete external lookups are displayed as unavailable, never fabricated.


if __name__ == "__main__":
    main()
