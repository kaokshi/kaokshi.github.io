"""Retrieve public research abstracts through the Europe PMC search API."""

import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen

from editorial_ai_check import extract_text
from editorial_writer import Pending

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PRIMARY_TYPES = {"research-article", "clinical trial", "observational study"}


def europe_pmc(task, payload, *, open_url=urlopen):
    if task != "discover":
        raise ValueError("Europe PMC supports only source discovery")
    query = payload.get("search_query") or payload["topic"]
    params = urlencode({"query": query[:240], "format": "json", "resultType": "core", "pageSize": 8})
    try:
        with open_url(f"{SEARCH_URL}?{params}", timeout=20) as response:
            data = json.loads(response.read(2_000_000))
        articles = data["resultList"]["result"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Pending("fact_check_pending", f"Europe PMC unavailable: {type(exc).__name__}") from exc
    documents = {}
    for article in articles:
        pmcid = article.get("pmcid", "")
        abstract = article.get("abstractText") or ""
        title = article.get("title")
        if not re.fullmatch(r"PMC[0-9]+", pmcid) or not abstract or not title:
            continue
        types = {x.casefold() for x in (article.get("pubTypeList") or {}).get("pubType", [])}
        if "retracted publication" in types or "retraction of publication" in types:
            continue
        primary = bool(types & PRIMARY_TYPES) and "review" not in types and "systematic review" not in types
        url = f"https://europepmc.org/articles/{pmcid}"
        content = extract_text(abstract)
        if len(content.strip()) >= 40:
            documents[url] = {"url": url, "title": extract_text(title),
                              "text": content, "scope": "abstract",
                              "kind": "primary" if primary else "secondary"}
    return {"documents": list(documents.values())}
