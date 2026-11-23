"""Replay the old visual checks on the existing 23 private images."""
from collections import Counter
from pathlib import Path
from time import monotonic

from PIL import Image

from .editorial_images import similarity
from .editorial_memory import digest, read_json
from .editorial_scene_audit import input_sections, manifest_by_file
from .editorial_offline_review_pack import CHANNELS, FILE_NAME
from .editorial_providers import Blocked
from .editorial_visual_matching import FLORENCE, Scene, describe_pixels, verify_caption


def difference_hash(im):
    pixels = list(im.convert("L").resize((9, 8)).getdata())
    return sum((pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
               for y in range(8) for x in range(8))


def clip_gate(scores, threshold=.19, margin=.015):
    return scores[0] >= threshold and scores[0] > scores[2] + margin


def benchmark(base):
    references = read_json(base / "image-qa-reference.json")
    if not references:
        raise Blocked("legacy_replay_missing_reference_rows")
    started = monotonic()
    articles, counters = {}, Counter()
    controls = {}
    for channel in CHANNELS:
        folder = base / channel
        sections, slots = input_sections((folder / FILE_NAME).read_text(encoding="utf-8"))
        expected = references[channel]
        if expected["source_text_sha256"] != digest("\n\n".join(sections.values())):
            raise Blocked("legacy_replay_text_sha_mismatch")
        metadata = manifest_by_file(folder)
        rows, seen_sha, fingerprints = [], set(), []
        for key, slot in slots.items():
            filename = f"{slot}.jpg"
            path = folder / "illustrations" / filename
            meta = metadata[filename]
            raw = path.read_bytes()
            image_sha = digest(raw)
            if image_sha != meta["sha256"]:
                raise Blocked("legacy_replay_image_sha_mismatch")
            with Image.open(path) as file:
                image = file.convert("RGB")
            original = meta["scene"]
            card = expected["cards"][key]
            grounded = ", ".join([card["location"], *card["must_have"]])
            topic = "Cave water minerals geological speleothems" if channel == "science-01" else original
            unrelated = ("unrelated street automobile or city skyline" if channel == "science-01"
                         else "an unrelated image with no connection with this topic")
            model, scores, _ = similarity(image, [original, topic, unrelated, grounded])
            fp = difference_hash(image)
            private_route = (image.width >= 480 and image.height >= 320
                             and all((prior ^ fp).bit_count() > 5 for prior in fingerprints))
            old_science_route = image_sha not in seen_sha and scores[0] >= .17 and scores[0] >= scores[2]
            old = old_science_route if channel == "science-01" else private_route
            fingerprints.append(fp)
            seen_sha.add(image_sha)
            row = {
                "slot": slot, "section": key, "image_sha256": image_sha, "model": model,
                "legacy_route_passed": old,
                "legacy_route": "dimensions_plus_duplicate_plus_clip" if channel == "science-01"
                                else "dimensions_plus_dhash_only",
                "clip_with_original_description": scores[0], "clip_with_section": scores[3],
                "clip_unrelated": scores[2],
                "clip_original_description_passed": clip_gate(scores),
                "clip_section_description_passed": clip_gate([scores[3], scores[1], scores[2]]),
            }
            if (channel, slot) in {("legends-01", "08"), ("legends-01", "04"), ("science-01", "06")}:
                try:
                    caption = describe_pixels(image)
                    aliases = {value: [value.removeprefix("a ").removeprefix("an ")]
                               for value in [card["location"], *card["must_have"]]}
                    check = verify_caption(Scene(grounded, aliases, tuple(card["must_not_have"])), caption)
                    controls[channel + "/" + slot] = {"model": list(FLORENCE), **check}
                except (Blocked, ValueError, OSError, RuntimeError) as exc:
                    controls[channel + "/" + slot] = {"model": list(FLORENCE), "status": "unavailable",
                                                       "reason": type(exc).__name__}
            rows.append(row)
            counters.update({field: int(row[field]) for field in
                             ("legacy_route_passed", "clip_original_description_passed",
                              "clip_section_description_passed")})
        articles[channel] = rows
    return {"articles": articles, "count": sum(map(len, articles.values())), "passed": dict(counters),
            "controls_florence": controls, "seconds": round(monotonic() - started, 2),
            "publication_authorized": False, "klein_calls": 0, "gemini_calls": 0}
