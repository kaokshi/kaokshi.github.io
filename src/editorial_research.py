"""Retrieve public research abstracts through the Europe PMC search API."""

import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError
from xml.etree import ElementTree as ET

from editorial_ai_check import extract_text
from editorial_writer import Pending

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PRIMARY_TYPES = {"research-article", "clinical trial", "observational study"}


def read_primary(pmcid, open_url):
    """Use results only from an identified original research article."""
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    with open_url(url, timeout=20) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        return None
    article = ET.fromstring(raw)
    if article.tag != "article" or article.get("article-type") != "research-article":
        return None
    ids = {(node.text or "").strip().removeprefix("PMC") for node in
           article.findall("./front/article-meta/article-id") if node.get("pub-id-type") in {"pmc", "pmcid"}}
    if pmcid.removeprefix("PMC") not in ids:
        return None
    body = article.find("body")
    if body is None:
        return None
    findings = [s for s in body.iter("sec") if "result" in
                (s.get("sec-type", "") + " " + (s.findtext("title") or "")).casefold()]
    if not findings:
        return None
    seen = set()
    passages = []
    for section in findings:
        for paragraph in section.iter("p"):
            text = " ".join("".join(paragraph.itertext()).split())
            if text and text not in seen:
                passages.append(text)
                seen.add(text)
    return "\n".join(passages)[:16000] or None


def europe_pmc(task, payload, *, open_url=urlopen):
    if task != "discover":
        raise ValueError("Europe PMC supports only source discovery")
    query = payload.get("search_query") or payload["topic"]
    params = urlencode({"query": query[:240], "format": "json", "resultType": "core", "pageSize": 8})
    for attempt in range(2):
        try:
            with open_url(f"{SEARCH_URL}?{params}", timeout=20) as response:
                data = json.loads(response.read(2_000_000))
            articles = data["resultList"]["result"]
            break
        except (OSError, ValueError, KeyError, TypeError) as exc:
            transient = (isinstance(exc, OSError) and
                         (not isinstance(exc, HTTPError) or exc.code in {502, 503, 504}))
            if not transient or attempt:
                raise Pending("fact_check_pending",
                              f"Europe PMC unavailable after {attempt + 1} request(s): {type(exc).__name__}") from exc
    documents = {}
    full_reads = 0
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
        scope = "abstract"
        if article.get("isOpenAccess") == "Y" and full_reads < 2 and not any("review" in t for t in types):
            full_reads += 1
            try:
                results = read_primary(pmcid, open_url)
            except (OSError, ET.ParseError):
                results = None
            if results:
                content += "\n" + results
                primary = True
                scope = "abstract_and_results"
        if len(content.strip()) >= 40:
            documents[url] = {"url": url, "title": extract_text(title),
                              "text": content, "scope": scope,
                              "kind": "primary" if primary else "secondary"}
    return {"documents": list(documents.values())}

def topic_sources(seed, research):
    """Broaden one exhausted constrained search, retaining retrieved texts for the writer."""
    failures = []
    for query in (f'TITLE_ABS:"{seed}" AND OPEN_ACCESS:Y', seed):
        try:
            rows = research("discover", {"topic": seed, "search_query": query})["documents"]
        except Pending as exc:
            failures.append(str(exc))
            continue
        terms = seed.casefold().split()
        related = [row for row in rows if row.get("kind") == "primary" and
                   all(word in (row["title"] + " " + row["text"]).casefold() for word in terms)]
        if related:
            return related
        failures.append("No matching primary source text")
    raise Pending("topic_selection_pending", "; ".join(failures))
