"""Advisory cinema image-to-section matching, never image or rights approval."""
import re
from pathlib import Path
from hashlib import sha256

from editorial_agent_v1 import article_digest
from editorial_semantic import paragraphs

STOP = {"фильм", "фильма", "кадр", "кадры", "обзор", "сюжет", "кино",
        "изображение", "фотография", "фото", "герой", "герои", "история"}


def terms(value):
    return {word for word in re.findall(r"[a-zа-яё]{4,}", value.casefold())
            if word not in STOP}


def match_images(article, bundle):
    """Produce transparent lexical proposals, not confirmed scene matches.

    It does not download images or use an unreliable vision model as proof.
    An image without meaningful textual overlap remains unmatched.
    """
    if article.get("channel") != "cinema":
        raise ValueError("Cinema-only image alignment")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("candidates"), list):
        raise ValueError("Expected the actual Commons candidate manifest")
    blocks = paragraphs(article)
    candidates = bundle["candidates"][:50]
    output = []
    seen_urls = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = item.get("source_page")
        if not isinstance(url, str) or not url.startswith("https://commons.wikimedia.org/wiki/") or url in seen_urls:
            continue
        seen_urls.add(url)
        if item.get("candidate_only") is not True:
            # A metadata-only tool cannot create approved rights/scene statuses.
            continue
        label = Path(str(item.get("title", ""))).stem.replace("_", " ")
        desc = item.get("description", "")
        if not isinstance(desc, str):
            desc = ""
        image_terms = terms(label + " " + desc[:600])
        scored = []
        for idx, block in enumerate(blocks, 1):
            block_terms = terms(block["text"])
            common = image_terms & block_terms
            if len(common) >= 2:
                scored.append({"paragraph": idx, "shared_terms": sorted(common)[:8],
                               "overlap": round(len(common) / max(1, len(image_terms)), 3)})
        scored.sort(key=lambda x: (-len(x["shared_terms"]), -x["overlap"], x["paragraph"]))
        output.append({"source_page": url, "candidate_title": item.get("title"),
                       "suggested_paragraphs": scored[:2],
                       "status": "textual_candidate_only" if scored else "no_meaningful_text_overlap",
                       "scene_match_verified": False, "rights_verified": False,
                       "image_pixels_inspected": False})
    return {"schema": "cinema-image-section-proposals-v1",
            "text_sha256": article_digest(article),
            "candidate_manifest_digest": sha256(
                __import__("json").dumps(bundle, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "candidates_examined": len(output), "matches_proposed": sum(
                bool(x["suggested_paragraphs"]) for x in output),
            "proposals": output, "publication_authorized": False,
            "all_scene_matches_verified": False}
