"""Bounded PubMed fallback when Europe PMC is temporarily unavailable.

Official NCBI ESearch and EFetch XML responses; no LLM-generated citations.
Only abstracts actually returned by NCBI may become article source documents.
"""
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from editorial_ai_check import extract_text
from editorial_writer import Pending

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_RECORDS = 5
MAX_BYTES = 1_500_000
PRIMARY_PUBLICATION_TYPES = {
    "randomized controlled trial", "clinical trial", "controlled clinical trial",
    "observational study", "comparative study",
}
REJECT_PUBLICATION_TYPES = {
    "retracted publication", "retraction of publication", "published erratum",
    "review", "systematic review", "meta-analysis",
}


def _read_xml(open_url, url):
    try:
        with open_url(url, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("PubMed response exceeded size limit")
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("PubMed response contains forbidden XML declarations")
        return ET.fromstring(raw)
    except (OSError, ValueError, ET.ParseError) as exc:
        raise Pending("fact_check_pending", "PubMed request failed: " + type(exc).__name__) from exc


def pubmed_sources(task, payload, *, open_url=urlopen):
    if task != "discover":
        raise ValueError("PubMed fallback supports only source discovery")
    topic = payload.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("PubMed fallback needs a concrete topic")
    # Europe PMC-specific syntax is intentionally not passed into PubMed ESearch.
    query = topic.strip()[:180]
    search = EUTILS + "esearch.fcgi?" + urlencode({
        "db": "pubmed", "term": query, "retmax": MAX_RECORDS,
        "retmode": "xml", "tool": "zen_editorial_reader",
    })
    index = _read_xml(open_url, search)
    if index.tag != "eSearchResult":
        raise Pending("fact_check_pending", "PubMed ESearch result has unexpected format")
    # Treat ESearch XML as untrusted: retmax is a request, not a response bound.
    # Keep the first five valid unique IDs, in stable order, before EFetch.
    ids = list(dict.fromkeys(
        row.text for row in index.findall("./IdList/Id")
        if row.text and re.fullmatch(r"[0-9]{1,12}", row.text)
    ))[:MAX_RECORDS]
    if not ids:
        return {"documents": []}
    # Exactly two sequential requests; no parallelism or unbounded retries.
    fetch = EUTILS + "efetch.fcgi?" + urlencode({
        "db": "pubmed", "id": ",".join(ids), "retmode": "xml",
        "tool": "zen_editorial_reader",
    })
    records = _read_xml(open_url, fetch)
    if records.tag != "PubmedArticleSet":
        raise Pending("fact_check_pending", "PubMed EFetch result has unexpected format")
    documents = []
    allowed = set(ids)
    seen = set()
    for article in records.findall("./PubmedArticle"):
        citation = article.find("./MedlineCitation")
        if citation is None:
            continue
        pmid = citation.findtext("PMID", default="").strip()
        if pmid not in allowed or pmid in seen:
            continue
        paper = citation.find("./Article")
        if paper is None:
            continue
        title = " ".join("".join(paper.find("ArticleTitle").itertext()).split()) if paper.find("ArticleTitle") is not None else ""
        abstract = paper.find("Abstract")
        if abstract is None:
            continue
        # Retain the actual source wording, including any structured abstract headings.
        sections = [" ".join("".join(element.itertext()).split())
                    for element in abstract.findall("AbstractText")]
        prose = "\n".join(section for section in sections if section)
        types = {(" ".join("".join(row.itertext()).split())).casefold()
                 for row in paper.findall("./PublicationTypeList/PublicationType")}
        if not title or len(prose.strip()) < 40 or types & REJECT_PUBLICATION_TYPES:
            continue
        # "Journal Article" alone does not prove original research.
        kind = "primary" if types & PRIMARY_PUBLICATION_TYPES else "secondary"
        documents.append({
            "url": "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/",
            "title": extract_text(title),
            "text": extract_text(prose)[:16000],
            "scope": "abstract",
            "kind": kind,
        })
        seen.add(pmid)
    return {"documents": documents}
