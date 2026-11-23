"""Film identity, source sufficiency and sourced stills for private review."""
from datetime import date, timedelta
import io
import json
import re
from urllib.parse import urlencode, urlsplit

from .editorial_commons_images import search as commons_search
from .editorial_memory import digest
from .editorial_no_repeat import normalize
from .editorial_providers import Blocked, ProviderError, request
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


def commons_stills(scene, film):
    title = film["original_title"]
    query = scene.get("query") or scene["subject"]
    for search_phrase in (f'"{title}" {query}', f'"{title}" film screenshot'):
        if len(search_phrase) > 150:
            continue
        rows = commons_search(search_phrase, limit=12, opener=lambda req, timeout: io.BytesIO(
            request(req.full_url, ("commons.wikimedia.org",), timeout=timeout, limit=3_000_000)))
        for row in rows["candidates"]:
            context = normalize(" ".join(row[key] for key in ("title", "description", "categories")))
            filename = normalize(row["title"])
            if (normalize(title) not in context or not re.search(r"\b(?:still|frame|screenshot)\b", context)
                    or re.search(r"\b(?:poster|fan art|dvd cover|logo)\b", filename)):
                continue
            yield {**row, "scene": row["description"], "source": "Wikimedia Commons",
                   "source_type": "public_reference", "year": film["year"], "original_title": title,
                   "creator": row["artist"], "license": row["license_label"],
                   "rights_status": "rights_review_pending"}


def stills(scene, subject, path, index, film, rights_registry, downloader=request):
    record = rights_registry.get(film["qid"], {})
    assets = set()
    planned = set(normalize(scene["subject"]).split())

    def candidate_rows():
        yield from sorted(record.get("stills", []), key=lambda x: -len(
            planned & set(normalize(x.get("scene", "")).split())))
        try:
            yield from commons_stills(scene, film)
        except (ValueError, OSError, ProviderError):
            return

    for row in candidate_rows():
        if index.memory.time_left() < 75:
            raise Blocked("production_deadline")
        if (row.get("year") != film["year"] or row.get("original_title") != film["original_title"]
                or not row.get("source_page") or not row.get("asset_url")
                or row["asset_url"] in assets):
            continue
        assets.add(row["asset_url"])
        host = urlsplit(row["asset_url"]).hostname
        source_host = urlsplit(row["source_page"]).hostname
        if not host or not source_host or not row["source_page"].startswith("https://"):
            continue
        # Curated domains come from the repository registry, never a model reply.
        # Wikimedia candidates are confined to the existing Wikimedia image CDN.
        domains = ("upload.wikimedia.org",) if row.get("source") == "Wikimedia Commons" else (host,)
        permitted = (row.get("rights_status") == "permission_verified"
                     and all(row.get(k) for k in ("sha256", "rights_holder", "rights_basis",
                                                  "restrictions", "permission_ref")))
        try:
            raw = downloader(row["asset_url"], domains, timeout=10)
            if row.get("sha256") and digest(raw) != row["sha256"]:
                continue
            metadata = {**row, "kind": "photo", "film_id": film["qid"],
                        "source": row.get("source") or source_host,
                        "creator": row.get("creator") or row.get("rights_holder"),
                        "rights_status": "permission_verified" if permitted else "rights_review_pending"}
            metadata.pop("sha256", None)
            return index.save(raw, metadata, scene, subject, path)
        except (ValueError, OSError, ProviderError):
            continue
    raise Blocked("film_scene_still_not_found")
