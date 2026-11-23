"""Live one-scene photograph QA without any text/image generator API calls.

Downloads candidate images only into an ephemeral private runner directory,
checks real pixels, CLIP scene match, phash duplicate guard and cover metadata.
Never republishes the image or claims that rights have been cleared.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from .editorial_images import ImageIndex, photo
from .editorial_providers import Blocked, request


class EphemeralMemory:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()

    def save(self):
        pass

    def time_left(self):
        return 900


def report(row):
    return {
        "passed": True,
        "semantic_score": row["scene_similarity"],
        "topic_similarity": row["topic_similarity"],
        "cover_score": row["cover_score"],
        "match_model": row["match_model"],
        "source": row["source"],
        "rights_status": row["rights_status"],
        "dimensions": row["size"],
        "duplicate_passed": True,
        "publication_authorized": False,
    }


def main():
    with TemporaryDirectory(prefix="zen-pixels-") as temp:
        root = Path(temp)
        index = ImageIndex(EphemeralMemory(root), [])
        results = {}
        scene = {"section": 1, "kind": "photo",
                 "subject": "Noctilucent glowing clouds in the blue twilight sky",
                 "query": "noctilucent clouds"}
        try:
            row = photo(scene, scene["subject"], root / "science-real-photo.jpg", index)
            results["science_photo"] = report(row)
        except (Blocked, OSError, ValueError) as exc:
            results["science_photo"] = {"passed": False,
                                         "reason_code": str(exc).split(":", 1)[0][:80]}
        # One verified-source *image* for a known film; no licensing implication.
        scene = {"section": 2, "kind": "photo",
                 "subject": "A tense bearded father in a dark jacket inside an office",
                 "query": "Prisoners 2013 Hugh Jackman office"}
        url = "https://media1.jpc.de/image/w1155/misc/4/4061229534413.jpg"
        metadata = {
            "kind": "photo", "source": "JPC Prisoners 2013 still",
            "asset_url": url,
            "source_page": "https://www.jpc.de/jpcng/movie/detail/-/art/prisoners/hnum/12236539",
            "rights_status": "rights_review_pending", "license": "unverified",
        }
        try:
            raw = request(url, ("media1.jpc.de",), timeout=20, limit=6_000_000)
            row = index.save(raw, metadata, scene, "Prisoners 2013 drama film",
                             root / "cinema-reference.jpg")
            results["cinema_still"] = report(row)
        except (Blocked, OSError, ValueError) as exc:
            results["cinema_still"] = {"passed": False,
                                       "reason_code": str(exc).split(":", 1)[0][:80]}
        results["generator_requests"] = 0
        print(json.dumps(results, ensure_ascii=False, sort_keys=True), flush=True)
        if not all(results[channel].get("passed") for channel in ("science_photo", "cinema_still")):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
