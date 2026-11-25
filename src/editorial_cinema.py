"""Film identity, source sufficiency and source provenance for stills for the cinema channel."""
from datetime import date, timedelta
import json
import re
from urllib.parse import urlencode

from .editorial_memory import digest, read_json
from .editorial_no_repeat import normalize
from .editorial_http import Blocked, ProviderError, check_url, request
from .editorial_review import sources
from .movie_autonomous_research import movie_sources

FORMATS = {"retelling", "review", "critique", "plot_analysis"}
DETAILED = {"retelling", "plot_analysis"}
WIKI = ("wikimedia.org",)
STUDIO_DOMAINS = ("paramount.com", "universalpictures.com", "warnerbros.com", "sonypictures.com",
                  "disney.com", "a24films.com", "focusfeatures.com", "lionsgate.com", "bfi.org.uk",
                  "rogerebert.com", "rottentomatoes.com", "bbc.com", "independent.co.uk", "theguardian.com")
WIFI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/ru.wikipedia/all-access/"


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


def evidence(plan, rights_registry, resolver=movie_sources, fetch=sources):
    film_title, year = plan.get("film_title"), plan.get("year")
    if (not isinstance(film_title, str) or not 2 <= len(film_title.strip()) <= 100
            or type(year) != int or not 1880 <= year <= date.today().year + 1
            or plan.get("format") not in FORMATS or type(plan.get("spoilers")) != bool):
        raise Blocked("cinema_plan_missing_exact_identity_or_format")
    initial = resolver("discover", {"topic": f"«{film_title}» ({year})"})
    identity = initial["film"]
    today = date.today()
    dated = [value for value in identity.get("release_dates", []) if value[:4] == str(year)]
    if year > today.year or (dated and min(dated) > today.isoformat()) or (year == today.year and not dated):
        raise Blocked("film_release_not_confirmed")
    if year not in identity["years"] or not identity["directors"] or not identity["cast"]:
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
            and row.get("source_type") in {"official_press", "rights_holder"}]


def still_candidates(scene, film, registry):
    query = set(normalize(scene["subject"]).split())
    return sorted(registered_stills(film, registry), key=lambda row: -len(
        query & set(normalize(row["scene"]).split())))


def fetch_still(row, film, downloader=request):
    check_url(row["source_page"], STUDIO_DOMAINS)
    check_url(row["asset_url"], STUDIO_DOMAINS)
    raw = downloader(row["asset_url"], STUDIO_DOMAINS, timeout=10)
    if digest(raw) != row["sha256"]:
        raise Blocked("film_still_sha_mismatch")
    metadata = {"kind": "photo", "film_id": film["qid"], "source": row["source_type"],
                "source_page": row["source_page"], "asset_url": row["asset_url"],
                "label": "Кадр из фильма", "ai_generated": False, "rights_status": "not_checked"}
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
