"""Opt-in photograph search for the existing editor. Production keeps its current route."""
from concurrent.futures import ThreadPoolExecutor
from itertools import chain, zip_longest
from pathlib import Path
from time import monotonic

from .editorial_images import commons_photos, decode_candidate, nasa_photos
from .editorial_memory import digest, write_json
from .editorial_http import Blocked, ProviderError, request
from .editorial_visual_matching import MAX_CANDIDATES, Scene, compare_pixels, describe_pixels, shortlist, verified_order


def originals(row):
    if original := row.get("original_asset_url"):
        return original
    if row.get("asset_manifest"):
        import json
        data = request(row["asset_manifest"], row["domains"], timeout=8, limit=1_000_000)
        urls = [row.get("href", "") for row in json.loads(data)["collection"]["items"]]
        return next((url for url in urls if url.split("?")[0].endswith(("~orig.jpg", "~orig.jpeg", "~orig.png"))), None)
    return None


def source_candidates(query, diagnostics):
    groups = []
    for source in (commons_photos, nasa_photos):
        try:
            groups.append(list(source(query)))
        except (ProviderError, ValueError, OSError) as exc:
            diagnostics.append({"source": source.__name__, "reason": str(exc) if isinstance(exc, ProviderError)
                                else type(exc).__name__})
    for row in chain.from_iterable(zip_longest(*groups)):
        if row:
            yield row


def ranked_photo(scene, topic, path, index, *, finder=source_candidates, scorer=compare_pixels,
                 captioner=describe_pixels, download=request):
    """Score a bounded pool, verify the best five, then apply normal image/duplicate gates."""
    spec = Scene.read(scene)
    report = []
    start = monotonic()
    limit = min(120, index.memory.time_left() - 30)
    if limit <= 0:
        raise Blocked("production_deadline")
    rows, seen = [], set()

    def open_candidate(row):
        raw = download(row["asset_url"], row["domains"], timeout=7)
        return raw, decode_candidate(raw)[0]

    try:
        pool = []
        for row in finder(scene["query"], report):
            if row["asset_url"] in seen:
                continue
            seen.add(row["asset_url"])
            pool.append(row)
            if len(pool) == MAX_CANDIDATES:
                break
        with ThreadPoolExecutor(max_workers=4) as downloads:
            pending = [(item, downloads.submit(open_candidate, item)) for item in pool]
            for row, result in pending:
                trace = {"id": digest(row["asset_url"])[:20], "source_page": row["source_page"],
                         "license": row["license"], "rights_status": row["rights_status"]}
                report.append(trace)
                if monotonic() - start >= limit:
                    trace["reason"] = "deadline"
                    result.cancel()
                    continue
                try:
                    body, image = result.result()
                    scores, _ = scorer("siglip", image, [spec.subject, topic, "unrelated image", *spec.forbidden])
                except (ProviderError, ValueError, OSError) as exc:
                    trace["reason"] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
                    continue
                trace.update(scores=scores, original_sha256=digest(body), reason="below_top_five")
                rows.append({"id": trace["id"], "scores": scores, "row": row, "trace": trace,
                             "image": image, "body": body})
        captions = {}
        shortlisted = shortlist(rows)
        for item in shortlisted:
            if monotonic() - start >= limit:
                item["trace"]["reason"] = "deadline"
                continue
            captions[item["id"]] = captioner(item["image"])
        accepted, assessed = verified_order(spec, [row for row in shortlisted if row["id"] in captions], captions)
        for item in assessed:
            item["trace"].update(reason="independent_caption_" + item["caption_check"]["verdict"],
                                 caption_check=item["caption_check"])
        for item in accepted:
            if monotonic() - start >= limit:
                item["trace"]["reason"] = "deadline"
                break
            row = item["row"]
            try:
                original = originals(row)
                if not original:
                    item["trace"]["reason"] = "original_url_missing"
                    continue
                download(original, row["domains"], timeout=6, limit=1, method="HEAD")
                metadata = {k: v for k, v in row.items() if k != "domains"}
                metadata["original_asset_url"] = original
                metadata["original_availability"] = "http_head_verified"
                metadata["visual_experiment"] = item["caption_check"]
                # The existing gate still verifies contrast, perceptual/semantic duplicates and archive history.
                chosen = index.save(item["body"], metadata, scene, topic, path)
                item["trace"]["reason"] = "selected"
                return chosen
            except (ProviderError, ValueError, OSError) as exc:
                item["trace"]["reason"] = str(exc)
        raise Blocked("science_explainer_illustration_candidate" if scene.get("allow_illustration") is True
                      else "semantic_review_required")
    finally:
        diagnostics = Path(path).parent.parent / "image-diagnostics" / (Path(path).stem + ".json")
        write_json(diagnostics, report)
