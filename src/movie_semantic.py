#!/usr/bin/env python3
"""Candidate semantic matches for a movie review against a plot synopsis.

Local multilingual embedding search. A high cosine score is NOT verification,
entailment, contradiction detection or a source for on-screen frame identity.
"""
import re

MODEL_ID = "intfloat/multilingual-e5-small"
MIN_WORDS = 8
MAX_CHARS = 800


def split_passages(text, max_words=85):
    """Paragraph/sentence-based short windows, to avoid encoder truncation."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", text) if x.strip()]
    if not sentences:
        return []
    results, current, length = [], [], 0
    for sentence in sentences:
        n = len(sentence.split())
        if length + n > max_words and current:
            results.append(" ".join(current)[:MAX_CHARS])
            current, length = [], 0
        if n > max_words:
            # Hard fallback for unusually long sentences.
            words = sentence.split()
            for j in range(0, len(words), max_words):
                results.append(" ".join(words[j:j + max_words])[:MAX_CHARS])
            continue
        current.append(sentence)
        length += n
    if current:
        results.append(" ".join(current)[:MAX_CHARS])
    return [x for x in results if len(x.split()) >= MIN_WORDS]


def semantic_candidates(article_sections, source, model=None, top_k=3):
    """Rank available reference passages; never label any statement as true."""
    import movie_fact_check as movie
    if source.get("status") != "completed":
        return {"status": "source_unavailable", "model": MODEL_ID, "matches": []}
    plot = movie.plot_only(source.get("extract", ""))
    passages = split_passages(plot)
    eligible = [
        row for row in article_sections
        if not re.search(r"(?i)^(моё мнение|почему|джим керри)", row["section"])
    ]
    if not passages:
        return {"status": "plot_section_unavailable", "model": MODEL_ID, "matches": []}
    if not eligible:
        return {"status": "no_plot_sections", "model": MODEL_ID, "matches": []}
    try:
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(MODEL_ID, device="cpu")
        # E5 uses query/passage prefixes. Queries can be split so an entire
        # multi-paragraph section does not silently truncate at max tokens.
        groups = [split_passages(row["text"]) for row in eligible]
        query_texts = [p for group in groups for p in group]
        if not query_texts:
            return {"status": "short_or_empty_sections", "model": MODEL_ID, "matches": []}
        import numpy as np
        query_vec = model.encode(["query: " + p for p in query_texts],
                                 normalize_embeddings=True, batch_size=8, show_progress_bar=False)
        source_vec = model.encode(["passage: " + p for p in passages],
                                  normalize_embeddings=True, batch_size=8, show_progress_bar=False)
        query_vec = np.asarray(query_vec, dtype=float)
        source_vec = np.asarray(source_vec, dtype=float)
        similarity = query_vec @ source_vec.T
        out, cursor = [], 0
        for row, group in zip(eligible, groups):
            if not group:
                out.append({"section": row["section"], "status": "short_or_empty_section",
                            "candidate_matches": []})
                continue
            scores = similarity[cursor:cursor + len(group)]
            cursor += len(group)
            # Keep best evidence per source passage, plus which part of article
            # produced it. This is a retrieval score, NOT a truth probability.
            scored = []
            for source_index in range(len(passages)):
                query_index = int(np.argmax(scores[:, source_index]))
                scored.append((float(scores[query_index, source_index]),
                               query_index, source_index))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates = [{
                "cosine_similarity": round(score, 4),
                "article_excerpt": group[query_index][:240],
                "source_excerpt": passages[source_index][:360],
                "source_passage_index": source_index,
                "source_url": source.get("url"),
            } for score, query_index, source_index in scored[:top_k]]
            out.append({"section": row["section"], "status": "semantic_candidates_found",
                        "candidate_matches": candidates,
                        "fact_verified": False,
                        "chronology_verified": False})
        return {"status": "completed", "model": MODEL_ID,
                "source_passages": len(passages),
                "article_passages": len(query_texts), "matches": out,
                "notice": "Embedding cosine is a ranking aid; it does not establish a scene happened, or that its depiction is accurate."}
    except Exception as exc:
        return {"status": "unavailable", "model": MODEL_ID, "matches": [],
                "reason": type(exc).__name__ + ": " + str(exc)[:350]}
