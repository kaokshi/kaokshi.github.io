#!/usr/bin/env python3
"""Autonomous, evidence-bound film research for the cinema draft queue.

Uses official public Wikidata and MediaWiki APIs only. No keys, scraping,
film downloads, copyrighted still extraction, or publication. This is
source retrieval, not proof that every fictional event happened on screen.
"""
from datetime import date
import json
import re
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from .editorial_providers import Blocked

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://ru.wikipedia.org/w/api.php"
USER_AGENT = "ZenEditorialCinemaResearch/1.0 (source-checking; public articles)"
MAX_BYTES = 1_000_000
FILM_CLASSES = {"Q11424", "Q24869"}  # film, feature film


def movie_identity(topic):
    """Extract a specific film name from the local autonomous planner's task."""
    if not isinstance(topic, str) or not topic.strip():
        raise Blocked("Cinema task has no film title")
    year = re.search(r"\b(?:19|20)\d{2}\b", topic)
    quoted = re.search(r"[«\"]([^»\"]{2,100})[»\"]", topic)
    if quoted:
        name = quoted.group(1).strip()
    else:
        name = re.split(r"\s+[—–-]\s+|\s*\(\s*(?:19|20)\d{2}\s*\)", topic, maxsplit=1)[0]
        name = re.sub(r"^(?:фильм|фильма|обзор фильма|пересказ фильма|рецензия на фильм)\s+", "", name.strip(), flags=re.I)
    if not 2 <= len(name) <= 100:
        raise Blocked("Cinema task has no unambiguous film title")
    return name, int(year.group()) if year else None


def fetch_json(endpoint, params):
    url = endpoint + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("API response too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError("Invalid API response")
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise Blocked("Film source API unavailable: " + type(exc).__name__) from exc


def claim_ids(entity, property_id):
    result = []
    for claim in entity.get("claims", {}).get(property_id, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and re.fullmatch(r"Q[1-9][0-9]*", str(value.get("id", ""))):
            result.append(value["id"])
    return result


def release_dates(entity):
    """Wikidata P577 at day precision. Future dates never prove a release."""
    dates = []
    for claim in entity.get("claims", {}).get("P577", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if not isinstance(value, dict) or value.get("precision", 0) < 11 or claim.get("rank") == "deprecated":
            continue
        raw = value.get("time", "")
        if re.fullmatch(r"\+\d{4}-\d{2}-\d{2}T.*", raw):
            try:
                dates.append(date.fromisoformat(raw[1:11]).isoformat())
            except ValueError:
                continue
    return sorted(set(dates))


def release_years(entity):
    years = []
    for claim in entity.get("claims", {}).get("P577", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        time = value.get("time", "") if isinstance(value, dict) else ""
        match = re.match(r"^[+](\d{4})-", time)
        if match:
            years.append(int(match.group(1)))
    return years


def label(entity):
    labels = entity.get("labels", {})
    for language in ("ru", "en"):
        value = labels.get(language, {}).get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _matching_title(entity, title):
    needle = title.casefold().replace("ё", "е")
    names = [v.get("value", "") for v in entity.get("labels", {}).values()]
    names.extend(a.get("value", "") for group in entity.get("aliases", {}).values() for a in group)
    return any(isinstance(name, str) and name.casefold().replace("ё", "е") == needle for name in names)


def movie_sources(task, payload, *, get_json=fetch_json):
    """Return retrieved documents only after identifying one concrete film.

    Never uses Wikipedia plot overlap as a factual 'verified' claim verdict.
    """
    if task != "discover":
        raise ValueError("Cinema research supports only discover")
    title, requested_year = movie_identity(payload.get("topic"))
    search = get_json(WIKIDATA_API, {
        "action": "wbsearchentities", "search": title, "language": "ru",
        "uselang": "ru", "type": "item", "limit": 8, "format": "json",
    })
    candidates = [item.get("id") for item in search.get("search", [])
                  if isinstance(item, dict) and re.fullmatch(r"Q[1-9][0-9]*", str(item.get("id", "")))]
    if not candidates:
        raise Blocked("No film found in Wikidata")
    data = get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(candidates[:8]),
        "props": "claims|labels|aliases|sitelinks", "languages": "ru|en",
        "format": "json",
    })
    entities = data.get("entities", {})
    matches = []
    for qid in candidates:
        entity = entities.get(qid)
        if not isinstance(entity, dict) or not (FILM_CLASSES & set(claim_ids(entity, "P31"))):
            continue
        if not _matching_title(entity, title):
            continue
        years = release_years(entity)
        if requested_year is not None and requested_year not in years:
            continue
        wiki_title = entity.get("sitelinks", {}).get("ruwiki", {}).get("title")
        if isinstance(wiki_title, str) and wiki_title:
            matches.append((qid, entity, wiki_title, years))
    if len(matches) != 1:
        raise Blocked("Film identity ambiguous or no Russian plot source")
    qid, movie, wiki_title, years = matches[0]
    page_data = get_json(WIKIPEDIA_API, {
        "action": "query", "prop": "extracts", "explaintext": 1,
        "format": "json", "formatversion": 2, "redirects": 1,
        "titles": wiki_title,
    })
    pages = page_data.get("query", {}).get("pages", [])
    page = pages[0] if isinstance(pages, list) and pages else {}
    extract = page.get("extract", "") if isinstance(page, dict) else ""
    match = re.search(r"(?ms)^==\s*(?:Сюжет|Содержание)\s*==\s*(.*?)(?=^==\s*[^=]|\Z)", extract)
    plot = match.group(1).strip() if match else ""
    # P453 is a character-role qualifier of an individual cast (P161) claim,
    # not an actor name. Keep the mapping attached to its actual actor statement.
    cast_claims = movie.get("claims", {}).get("P161", [])[:8]
    character_pairs = []
    for statement in cast_claims:
        actor = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        for role in statement.get("qualifiers", {}).get("P453", [])[:2]:
            character = role.get("datavalue", {}).get("value", {})
            role_id = character.get("id") if isinstance(character, dict) else None
            if actor_id in claim_ids(movie, "P161") and isinstance(role_id, str) and re.fullmatch(r"Q[1-9][0-9]*", role_id):
                character_pairs.append((actor_id, role_id))
    linked = claim_ids(movie, "P57")[:3] + claim_ids(movie, "P161")[:8]
    linked += [role for _, role in character_pairs]
    people = {}
    if linked:
        names = get_json(WIKIDATA_API, {
            "action": "wbgetentities", "ids": "|".join(dict.fromkeys(linked)),
            "props": "labels", "languages": "ru|en", "format": "json",
        }).get("entities", {})
        people = {ident: label(names.get(ident, {})) for ident in linked if isinstance(names.get(ident), dict)}
    directors = [people[q] for q in claim_ids(movie, "P57")[:3] if people.get(q)]
    cast = [people[q] for q in claim_ids(movie, "P161")[:8] if people.get(q)]
    cast_roles = [{"actor": people[actor], "character": people[role]}
                  for actor, role in character_pairs if people.get(actor) and people.get(role)]
    movie_name = label(movie) or title
    fact_lines = [f"Фильм: {movie_name}", "Wikidata ID: " + qid]
    if years:
        fact_lines.append("Даты первого показа (годы в карточке): " + ", ".join(map(str, years[:5])))
    if directors:
        fact_lines.append("Режиссёры: " + ", ".join(directors))
    if cast:
        fact_lines.append("Актёры (не имена персонажей): " + ", ".join(cast))
    if cast_roles:
        fact_lines.append("Актёры и роли по отдельным утверждениям Wikidata P161/P453 (возможны ошибки): " +
                          "; ".join(row["actor"] + " — " + row["character"] for row in cast_roles))
    identity_url = "https://www.wikidata.org/wiki/" + qid
    plot_url = "https://ru.wikipedia.org/wiki/" + quote(wiki_title.replace(" ", "_"))
    return {
        "status": "source_documents_retrieved_not_plot_verified",
        "film": {"qid": qid, "title": movie_name,
                 "title_ru": movie.get("labels", {}).get("ru", {}).get("value"),
                 "original_title": (movie.get("claims", {}).get("P1476") or [{}])[0]
                     .get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("text")
                     or movie.get("labels", {}).get("en", {}).get("value") or movie_name,
                 "imdb_id": (movie.get("claims", {}).get("P345") or [{}])[0]
                     .get("mainsnak", {}).get("datavalue", {}).get("value"),
                 "years": years, "release_dates": release_dates(movie),
                 "directors": directors, "cast": cast, "cast_roles": cast_roles,
                 "wikipedia_title": wiki_title},
        "documents": [
            {"url": identity_url, "title": "Wikidata: " + movie_name,
             "text": "\n".join(fact_lines), "kind": "secondary"},
            {"url": plot_url, "title": "Описание сюжета: " + wiki_title,
             "text": plot[:18000], "kind": "secondary"},
        ],
        "plot_events_verified_against_film": False,
        "character_roles_verified": False,
        "publication_authorized": False,
    }
