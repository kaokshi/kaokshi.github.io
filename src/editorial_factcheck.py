"""Store claim-level evidence grounded in text actually supplied by a source reader."""

from hashlib import sha256
from urllib.parse import urlsplit

from editorial_agent_v1 import article_digest
from editorial_semantic import paragraphs
from editorial_writer import Pending

VERDICTS = {"confirmed", "disputed", "unconfirmed"}


def valid_sources(rows):
    if not isinstance(rows, list):
        raise ValueError("Expected a source documents list")
    sources = {}
    for row in rows:
        url, title, text, kind = (row[k] for k in ("url", "title", "text", "kind"))
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or not isinstance(title, str) or not title.strip()
                or not isinstance(text, str) or len(text.strip()) < 40
                or kind not in {"primary", "secondary"} or url in sources):
            raise ValueError("Each source needs a unique HTTPS URL, title, kind and retrieved text")
        sources[url] = {"url": url, "title": title, "text": text[:24000], "kind": kind,
                        "scope": "abstract" if row.get("scope") == "abstract" else "supplied_text"}
    return sources


def fact_check(article, provider, research=None, documents=None, reviewed=None):
    digest = article_digest(article)
    report = {"text_sha256": digest, "status": "fact_check_pending", "claims": [], "sources": []}
    if article["channel"] != "science":
        return {**report, "status": "not_applicable"}
    manual = reviewed is not None and reviewed.get("text_sha256") == digest
    blocks = paragraphs(article)
    try:
        response = reviewed if manual else provider("extract_claims", {
            "title": article["title"], "paragraphs": blocks,
            "instruction": "Перечисли все основные проверяемые утверждения. "
                           "Верни claims с id, paragraph и точной, полной цитатой quote, "
                           "и search_query на английском для поиска первоисточников.",
        })
        ids = set()
        for item in response["claims"]:
            key, number, quote = (item[k] for k in ("id", "paragraph", "quote"))
            if (not isinstance(key, str) or not key.strip() or key in ids
                    or type(number) is not int or not 1 <= number <= len(blocks)
                    or not isinstance(quote, str) or not quote.strip()
                    or quote not in blocks[number - 1]["text"]):
                raise ValueError("A claim needs a unique id and an exact paragraph quote")
            ids.add(key)
            report["claims"].append({"id": key, "paragraph": number, "quote": quote,
                                     "verdict": "unconfirmed", "evidence": []})
        if not ids:
            raise ValueError("No verifiable claims inventoried for science draft")
        rows = documents
        if rows is None and research is not None:
            query = response.get("search_query")
            rows = research("discover", {"topic": article["title"], "claims": report["claims"],
                                         "search_query": query if isinstance(query, str) else None,
                                         "instruction": "Найди и прочитай первоисточники. Верни "
                                                        "documents с url, title, kind и прочитанным text."})["documents"]
        sources = valid_sources(rows or [])
        if not sources:
            raise Pending("fact_check_pending", "No source reader or retrieved documents are available")
        report["sources"] = [{k: v for k, v in s.items() if k != "text"} |
                             {"source_sha256": sha256(s["text"].encode()).hexdigest()}
                             for s in sources.values()]
        if manual:
            results = reviewed["verdicts"]
        else:
            # A full article can exceed the local model's structured output budget.
            results = []
            for start in range(0, len(report["claims"]), 6):
                group = report["claims"][start:start + 6]
                response = provider("compare_claims", {
                    "claims": group, "documents": list(sources.values()),
                    "instruction": ("Сопоставь каждое утверждение с прочитанными источниками. "
                                    "Верни verdicts с id, verdict (confirmed/disputed/unconfirmed), "
                                    "кратким reason и evidence с url и точной цитатой первоисточника.")})
                if {r["id"] for r in response["verdicts"]} != {c["id"] for c in group}:
                    raise ValueError("Incomplete claim comparison batch")
                results.extend(response["verdicts"])
        verdicts = {}
        for result in results:
            key, verdict, reason = (result[k] for k in ("id", "verdict", "reason"))
            if (key not in ids or key in verdicts or verdict not in VERDICTS
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError("Invalid claim verdict")
            evidence = result["evidence"]
            if not isinstance(evidence, list):
                raise ValueError("Expected evidence list")
            for proof in evidence:
                url, quote = proof["url"], proof["quote"]
                if (url not in sources or not isinstance(quote, str) or not quote.strip()
                        or quote not in sources[url]["text"]):
                    raise ValueError("Evidence quote must come from the retrieved source text")
            if verdict in {"confirmed", "disputed"} and not any(
                sources[p["url"]]["kind"] == "primary" for p in evidence
            ):
                raise ValueError("Confirmed or disputed claim lacks quoted primary evidence")
            verdicts[key] = {"verdict": verdict, "reason": reason,
                             "evidence": [{"url": p["url"], "quote": p["quote"]} for p in evidence]}
        if verdicts.keys() != ids:
            raise ValueError("Every inventoried claim needs a verdict")
        for claim in report["claims"]:
            claim.update(verdicts[claim["id"]])
    except (Pending, ValueError, TypeError, KeyError, AttributeError) as exc:
        report["reason"] = str(exc)
        return report
    report["status"] = "completed"
    report["origin"] = "operator" if manual else "model_grounded_in_sources"
    return report
