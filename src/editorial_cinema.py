"""Film identity, source sufficiency and source provenance for stills for the cinema channel."""
from datetime import date, timedelta
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlencode, urlsplit

from .editorial_commons_images import PlainText
from .editorial_memory import digest, read_json
from .editorial_no_repeat import normalize
from .editorial_http import Blocked, ProviderError, check_url, request
from .editorial_review import sources
from .movie_autonomous_research import (FILM_CLASSES, WIKIDATA_API, claim_ids, fetch_json, label,
                                        movie_sources, release_dates, release_years)

FORMATS = {"retelling", "review", "critique", "plot_analysis"}
DETAILED = {"retelling", "plot_analysis"}
WIKI = ("wikimedia.org",)
STUDIO_DOMAINS = ("paramount.com", "universalpictures.com", "warnerbros.com", "sonypictures.com",
                  "disney.com", "a24films.com", "focusfeatures.com", "lionsgate.com", "bfi.org.uk",
                  "rogerebert.com", "rottentomatoes.com", "bbc.com", "independent.co.uk", "theguardian.com")
COMMONS_DOMAINS = ("commons.wikimedia.org", "upload.wikimedia.org")
ARCHIVE_DOMAINS = ("archive.org",)
WIFI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/ru.wikipedia/all-access/"


_POLICY = json.loads((Path(__file__).resolve().parents[1] / "config/editorial_cinema_policy.json").read_text())
MIN_RELEASE_YEAR = _POLICY["minimum_original_release_year"]
PREFERRED_RELEASE_YEARS = _POLICY["preferred_release_year_count"]


def eligible_year(year):
    return type(year) is int and MIN_RELEASE_YEAR <= year <= date.today().year


def verify_original_year(year, film):
    years = film.get("years")
    if not isinstance(years, list) or not years or any(type(value) is not int for value in years):
        raise Blocked("film_original_release_unverified")
    original = min(years)
    if original < MIN_RELEASE_YEAR:
        raise Blocked("film_original_release_before_minimum")
    if year != original:
        raise Blocked("film_original_release_year_mismatch")
    return original


def interest_signals(today=None):
    """Public audience-interest signal. Identity verification follows selection."""
    previous = (today or date.today()) - timedelta(days=2)
    url = WIFI + previous.strftime("%Y/%m/%d")
    try:
        rows = json.loads(request(url, WIKI, timeout=10, limit=2_000_000))["items"][0]["articles"]
    except (ProviderError, KeyError, IndexError, ValueError):
        return {"status": "unavailable", "articles": []}
    return {"status": "pageviews_available", "date": previous.isoformat(), "articles": [
        {"title": row["article"].replace("_", " "), "views": row["views"]}
        for row in rows[:140] if "(фильм" in row.get("article", "").casefold()
    ][:22]}


def recent_releases(today=None):
    end = today or date.today()
    start = end - timedelta(days=120)
    query = f'''SELECT DISTINCT ?film ?label ?release WHERE {{
      ?film wdt:P31 wd:Q11424; wdt:P577 ?release; rdfs:label ?label.
      FILTER(LANG(?label)="ru" && ?release >= "{start}T00:00:00Z"^^xsd:dateTime
             && ?release <= "{end}T23:59:59Z"^^xsd:dateTime)
      ?article schema:about ?film; schema:isPartOf <https://ru.wikipedia.org/>.
    }} ORDER BY DESC(?release) LIMIT 20'''
    try:
        raw = request("https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"}),
                      ("wikidata.org",), timeout=12, limit=800_000)
        rows = json.loads(raw)["results"]["bindings"]
    except (ProviderError, KeyError, ValueError):
        return {"status": "unavailable", "films": []}
    return {"status": "public_catalog_only", "films": [
        {"title": row["label"]["value"], "date": row["release"]["value"][:10],
         "qid": row["film"]["value"].rsplit("/", 1)[-1]} for row in rows
    ]}


def archive_film_identity(qid, film, get_json=fetch_json):
    """Resolve the exact catalogue QID without requiring a Russian Wikipedia page."""
    if not re.fullmatch(r"Q[1-9][0-9]*", qid or ""):
        raise Blocked("cinema_catalogue_qid_invalid")
    movie = get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": qid, "props": "claims|labels",
                                   "languages": "ru|en", "format": "json"})["entities"][qid]
    page = urlsplit(film["source_page"])
    if page.hostname == "commons.wikimedia.org" and page.path.startswith("/wiki/File:"):
        property_id, identifier = "P10", unquote(page.path).removeprefix("/wiki/File:").replace("_", " ")
    elif page.hostname == "archive.org" and page.path.startswith("/details/"):
        property_id, identifier = "P724", unquote(page.path).removeprefix("/details/")
    else:
        raise Blocked("film_archive_identity_conflict")
    matches = [row.get("mainsnak", {}).get("datavalue", {}).get("value")
               for row in movie.get("claims", {}).get(property_id, []) if row.get("rank") != "deprecated"]
    if property_id == "P10":
        matches = [str(name).replace("_", " ") for name in matches]
    if identifier not in matches or not FILM_CLASSES.intersection(claim_ids(movie, "P31")):
        raise Blocked("film_archive_identity_conflict")
    labels = movie.get("labels", {})
    if film["original_title"] != labels.get("en", {}).get("value"):
        raise Blocked("film_archive_title_conflict")
    directors, actors = claim_ids(movie, "P57")[:3], claim_ids(movie, "P161")[:8]
    if not directors or not actors:
        raise Blocked("film_metadata_incomplete")
    people = get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(dict.fromkeys(directors + actors)),
                                     "props": "labels", "languages": "ru|en", "format": "json"})["entities"]
    names = {key: label(people[key]) for key in people}
    years = release_years(movie)
    resolved = {"qid": qid, "title": label(movie), "original_title": film["original_title"],
                "years": years, "release_dates": release_dates(movie),
                "directors": [names[q] for q in directors if names.get(q)],
                "cast": [names[q] for q in actors if names.get(q)]}
    text = (f"Film: {resolved['original_title']}. Release dates (years): {', '.join(map(str, years))}. "
            f"Directors: {', '.join(resolved['directors'])}. Cast: {', '.join(resolved['cast'])}.")
    return {"film": resolved, "documents": [{"url": "https://www.wikidata.org/wiki/" + qid,
                                               "text": text, "kind": "structured_catalogue"}]}


def archive_source_record(url):
    page = urlsplit(url)
    if page.hostname == "commons.wikimedia.org" and page.path.startswith("/wiki/File:"):
        params = urlencode({"action": "query", "titles": unquote(page.path).removeprefix("/wiki/"),
                            "prop": "imageinfo", "iiprop": "extmetadata", "formatversion": 2, "format": "json"})
        raw = request("https://commons.wikimedia.org/w/api.php?" + params, COMMONS_DOMAINS, limit=1_000_000)
        entry = json.loads(raw)["query"]["pages"][0]
        metadata = entry["imageinfo"][0]["extmetadata"]
        title = entry["title"]
        fields = [metadata.get(key, {}).get("value", "") for key in ("ImageDescription", "DateTimeOriginal", "Artist")]
    elif page.hostname == "archive.org" and page.path.startswith("/details/"):
        identifier = page.path.removeprefix("/details/")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", identifier):
            raise Blocked("film_archive_identity_conflict")
        raw = request("https://archive.org/metadata/" + identifier, ARCHIVE_DOMAINS, limit=2_000_000)
        metadata = json.loads(raw)["metadata"]
        title = metadata["title"]
        fields = [metadata.get(key, "") for key in ("description", "date", "creator")]
    else:
        raise Blocked("film_archive_identity_conflict")
    parser = PlainText()
    for field in fields:
        parser.feed(" " + str(field))
    text = " ".join("".join(parser.data).split())
    if len(text) < 25:
        raise Blocked("film_archive_description_missing")
    return {"url": url, "status": "accessible", "text": title + "\n" + text[:10_000],
            "body_sha256": digest(raw), "kind": "archive_media_metadata"}


def evidence(plan, rights_registry, resolver=movie_sources, fetch=sources):
    film_title, year = plan.get("film_title"), plan.get("year")
    if (not isinstance(film_title, str) or not 2 <= len(film_title.strip()) <= 100
            or type(year) != int
            or plan.get("format") not in FORMATS or type(plan.get("spoilers")) != bool):
        raise Blocked("cinema_plan_missing_exact_identity_or_format")
    if year < MIN_RELEASE_YEAR:
        raise Blocked("film_original_release_before_minimum")
    qid = plan.get("qid")
    archive_row = rights_registry.get(qid, {})
    initial = (archive_film_identity(qid, archive_row) if archive_row.get("source_page") else
               resolver("discover", {"topic": f"«{film_title}» ({year})"}))
    identity = initial["film"]
    today = date.today()
    dated = [value for value in identity.get("release_dates", []) if value[:4] == str(year)]
    if year > today.year or (dated and min(dated) > today.isoformat()) or (year == today.year and not dated):
        raise Blocked("film_release_not_confirmed")
    verify_original_year(year, identity)
    if not identity["directors"] or not identity["cast"]:
        raise Blocked("film_metadata_incomplete")
    if plan.get("qid") and plan["qid"] != identity["qid"]:
        raise Blocked("movie_catalog_id_conflict")
    if plan.get("original_title") and normalize(plan["original_title"]) != normalize(identity["original_title"]):
        raise Blocked("movie_original_title_conflict")
    approved = rights_registry.get(identity["qid"], {})
    submitted = plan.get("sources", [])
    public_urls = [url for url in submitted if isinstance(url, str)] if isinstance(submitted, list) else []
    trusted = [row for row in approved.get("plot_sources", []) if row.get("reviewed") is True
               and row.get("version_year") == year and row.get("kind") == "complete_plot"]
    deep_urls = [row["url"] for row in trusted]
    fetched = fetch(list(dict.fromkeys([*deep_urls, *public_urls])), STUDIO_DOMAINS)
    sources_ok = [row for row in fetched if row["status"] == "accessible"
                  and any(normalize(name) in normalize(row["text"])
                          for name in (identity["title"], identity["original_title"]) if len(normalize(name)) > 3)]
    if archive_source := approved.get("source_page"):
        sources_ok.append(archive_source_record(archive_source))
    deep = [row for row in sources_ok if row["url"] in deep_urls and len(row["text"]) > 1200]
    # Never infer complete event coverage from the length of a trailer or synopsis.
    permitted_detail = (len(deep) >= 2 and len({row["url"].split("/")[2] for row in deep}) >= 2
                        and plan["spoilers"])
    output_format = plan["format"] if plan["format"] not in DETAILED or permitted_detail else "review"
    spoiler_mode = plan["spoilers"] and permitted_detail
    if not sources_ok:
        raise Blocked("film_independent_source_unavailable")
    result = [{"status": "accessible", **initial["documents"][0]}]
    result.extend(deep if spoiler_mode else sources_ok[:4])
    return {"film": {**identity, "year": year}, "format": output_format, "spoilers": spoiler_mode,
            "detailed_plot_evidence": permitted_detail, "records": result}


def registered_stills(film, registry):
    return [row for row in registry.get(film["qid"], {}).get("stills", [])
            if isinstance(row, dict)
            and row.get("year") == film["year"] and row.get("original_title") == film["original_title"]
            and re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256", "")))
            and all(isinstance(row.get(k), str) and row[k].strip()
                    for k in ("source_page", "asset_url", "scene"))
            and row.get("source_type") in {"official_press", "rights_holder", "commons_film", "internet_archive_film"}]


def has_usable_stills(film, registry, *, private_read, minimum=7):
    """Skip films with missing, corrupt or near-duplicate source images before drafting."""
    candidates = registered_stills(film, registry)
    if len({row["sha256"] for row in candidates}) < minimum:
        return False

    from imagehash import phash

    from .editorial_images import decode_candidate

    signatures = []
    seen = set()
    for row in candidates:
        sha = row["sha256"]
        if sha in seen:
            continue
        try:
            raw, _ = fetch_still(row, film, private_read=private_read)
            image, _ = decode_candidate(raw)
        except (Blocked, TypeError, ValueError, OSError):
            continue
        seen.add(sha)
        signature = phash(image)
        if any(signature - previous <= 6 for previous in signatures):
            continue
        signatures.append(signature)
        if len(signatures) >= minimum:
            return True
    return False


def still_candidates(scene, film, registry):
    query = set(normalize(scene["subject"]).split())
    return sorted(registered_stills(film, registry), key=lambda row: -len(
        query & set(normalize(row["scene"]).split())))


def fetch_still(row, film, downloader=request, *, private_read=None):
    if row["source_type"] in {"commons_film", "internet_archive_film"}:
        domains = COMMONS_DOMAINS if row["source_type"] == "commons_film" else ARCHIVE_DOMAINS
        check_url(row["source_page"], domains)
        check_url(row["asset_url"], domains)
        path = f"sources/cinema/{film['qid']}/{row['sha256']}.jpg"
        if private_read is None or row.get("private_path") != path:
            raise Blocked("film_still_private_path_invalid")
        raw = private_read(path)
    else:
        check_url(row["source_page"], STUDIO_DOMAINS)
        check_url(row["asset_url"], STUDIO_DOMAINS)
        raw = downloader(row["asset_url"], STUDIO_DOMAINS, timeout=10)
    if raw is None or digest(raw) != row["sha256"]:
        raise Blocked("film_still_sha_mismatch")
    metadata = {"kind": "photo", "film_id": film["qid"], "source": row["source_type"],
                "source_page": row["source_page"], "asset_url": row["asset_url"],
                "label": "Кадр из фильма", "ai_generated": False, "rights_status": "not_checked"}
    metadata.update({key: row[key] for key in ("license", "license_url", "timestamp_seconds") if key in row})
    return raw, metadata


def stills(scene, subject, path, index, film, rights_registry, downloader=request):
    for row in still_candidates(scene, film, rights_registry):
        if index.memory.time_left() < 75:
            raise Blocked("production_deadline")
        try:
            raw, metadata = fetch_still(row, film, downloader)
            return index.save(raw, metadata, scene, subject, path)
        except (ValueError, OSError, Blocked):
            continue
    raise Blocked("no_verified_official_still_for_scene")
