"""Read-only live discovery of photo candidates for the mixed science and cinema pilot.

No image is republished, copyrighted still downloaded, or rights automatically cleared.
The result reports candidate availability only, never claims successful semantic CLIP QA.
"""
import json
from pathlib import Path

from .editorial_cinema import commons_stills
from .editorial_images import commons_photos, nasa_photos
from .editorial_providers import Blocked, ProviderError

ROOT = Path(__file__).resolve().parents[1]


def probe():
    result = {"schema": "zen-photo-probe-v1", "publication_authorized": False,
              "semantic_matching": "requires_article_level_visual_check",
              "science": {}, "cinema": {}}

    for source, fn in (
        ("commons", lambda: commons_photos("noctilucent clouds", kind="photo")),
        ("nasa", lambda: nasa_photos("noctilucent clouds")),
    ):
        try:
            rows = list(fn())[:14]
            result["science"][source] = {
                "candidate_count": len(rows),
                "rights_pending_count": sum(r.get("rights_status") == "rights_review_pending"
                                            for r in rows),
                "photo_only": all(r.get("kind") == "photo" for r in rows),
            }
        except (Blocked, ProviderError, OSError, ValueError) as exc:
            result["science"][source] = {
                "candidate_count": 0,
                "status": "source_error_" + type(exc).__name__,
            }

    # Stills are film-specific *references* for private editorial review.
    # They are not offered as royalty-free assets.
    try:
        rows = list(commons_stills(
            {"query": "film screenshot", "subject": "Arrival 2016 film scene"},
            {"original_title": "Arrival", "year": 2016}
        ))[:14]
        result["cinema"]["commons"] = {
            "candidate_count": len(rows),
            "rights_pending_count": sum(r.get("rights_status") == "rights_review_pending"
                                        for r in rows),
        }
    except (Blocked, ProviderError, OSError, ValueError) as exc:
        result["cinema"]["commons"] = {
            "candidate_count": 0,
            "status": "source_error_" + type(exc).__name__,
        }

    manifest = json.loads((ROOT / "content/review/cinema-images.json").read_text(encoding="utf-8"))
    arrival = [r for r in manifest.get("items", [])
               if "/2016/Arrival/" in r.get("gallery", "")]
    result["cinema"]["archive_references"] = {
        "candidate_count": len(arrival), "rights": "not_cleared",
        "usable_for_private_selection": True,
        "downloaded_or_published": False,
    }
    return result


if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, sort_keys=True), flush=True)
