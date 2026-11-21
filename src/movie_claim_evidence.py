"""Source-bound cinema claim audit; secondary plot references never prove the film."""
from hashlib import sha256

from editorial_agent_v1 import article_digest
from editorial_factcheck import valid_sources
from editorial_semantic import paragraphs
from editorial_writer import Pending


def audit_claims(article, documents, provider):
    digest = article_digest(article)
    result = {"schema": "cinema-claim-evidence-v1", "text_sha256": digest,
              "status": "fact_check_pending", "claims": [],
              "film_scene_verified": False, "publication_authorized": False}
    if article["channel"] != "cinema":
        raise ValueError("Cinema claim audit is cinema-only")
    try:
        sources = valid_sources(documents or [])
        if not sources or not any("сюжет" in s["title"].casefold() for s in sources.values()):
            raise ValueError("No retrieved film plot source")
        blocks = paragraphs(article)
        response = provider("extract_claims", {
            "title": article["title"], "paragraphs": blocks,
            "instruction": ("Выдели основные проверяемые сведения о героях и действиях, "
                            "включая финал и спойлеры. Верни только точные цитаты "
                            "из пронумерованных абзацев, не придумывай цитат."),
        })
        claims, seen = [], set()
        for row in response["claims"]:
            ident, index, quote = row["id"], row["paragraph"], row["quote"]
            if (not isinstance(ident, str) or not ident.strip() or ident in seen
                    or type(index) is not int or not 1 <= index <= len(blocks)
                    or not isinstance(quote, str) or len(quote.strip()) < 8
                    or quote not in blocks[index - 1]["text"]):
                raise ValueError("Claim must quote an actual paragraph exactly")
            seen.add(ident)
            claims.append({"id": ident, "paragraph": index, "quote": quote})
        if not claims or len(claims) > 24:
            raise ValueError("Cinema audit requires 1 to 24 grounded claims")
        response = provider("compare_claims", {
            "claims": claims, "documents": list(sources.values()),
            "instruction": ("Сопоставь каждую цитату с предоставленными источниками. "
                            "Для любого confirmed/disputed дай настоящую цитату из источника. "
                            "Отсутствие прямого доказательства означает unconfirmed. "
                            "Википедия и Wikidata не подтверждают происходящее на экране."),
        })
        found = {}
        for item in response["verdicts"]:
            ident, verdict = item["id"], item["verdict"]
            if ident not in seen or ident in found or verdict not in {"confirmed", "disputed", "unconfirmed"}:
                raise ValueError("Unexpected or repeated movie claim verdict")
            evidence = item["evidence"]
            if not isinstance(evidence, list):
                raise ValueError("Claim evidence must be a list")
            for proof in evidence:
                if (not isinstance(proof, dict)
                        or proof.get("url") not in sources
                        or not isinstance(proof.get("quote"), str)
                        or len(proof["quote"].strip()) < 5
                        or proof["quote"] not in sources[proof["url"]]["text"]):
                    raise ValueError("Movie evidence must quote retrieved source verbatim")
            if verdict != "unconfirmed" and not evidence:
                raise ValueError("Nonempty evidence required for movie claim verdict")
            found[ident] = {"source_verdict": verdict,
                            "status": {"confirmed": "secondary_text_support_not_verified",
                                       "disputed": "source_disagreement_needs_review",
                                       "unconfirmed": "no_direct_source_support"}[verdict],
                            "evidence": evidence}
        if set(found) != seen:
            raise ValueError("All movie claims must receive a verdict")
        result["claims"] = [{**c, **found[c["id"]]} for c in claims]
        result["source_hashes"] = [{"url": url,
             "sha256": sha256(source["text"].encode("utf-8")).hexdigest()}
             for url, source in sources.items()]
        result["status"] = "source_comparison_completed_not_film_verified"
    except (Pending, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["reason"] = type(exc).__name__ + ": " + str(exc)[:180]
    return result
