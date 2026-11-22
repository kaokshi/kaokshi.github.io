"""Scene-level mixed image discovery, generation, visual matching and cover selection."""
from collections import Counter
from functools import lru_cache
import io
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import quote, urlencode

from . import editorial_commons_images as commons
from .editorial_memory import digest, read_json, write, write_json
from .editorial_providers import KLEIN, Blocked, ProviderError, klein, request

COMMONS = ("commons.wikimedia.org", "upload.wikimedia.org")
NASA = ("images-api.nasa.gov", "images-assets.nasa.gov", "images.nasa.gov")
NASA_TERMS = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
NONPHOTO = re.compile(
    r"illustration|artist.s (?:impression|concept)|artwork|rendering|infographic|graphic|"
    r"diagram|ai.generated|synthetic image|computer.generated|simulation|montage|"
    r"drawing|generated with|concept art|collage|blender|diagramm", re.I)
RESTRICTED = re.compile(r"all rights reserved|copyright(?:ed| notice)|third.party|©|editorial use only", re.I)
LICENSES = {
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
}


def validate_plan(plan, section_count, channel, settings):
    blocks = plan.get("images")
    if not isinstance(blocks, list) or not settings["minimum_images"] <= len(blocks) <= settings["maximum_images"]:
        raise Blocked("image_plan_count")
    subject = plan.get("subject")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 240:
        raise Blocked("image_plan_subject")
    sections, descriptions, kinds = set(), set(), set()
    for row in blocks:
        if not isinstance(row, dict):
            raise Blocked("image_plan_entry_invalid")
        section, kind = row.get("section"), row.get("kind")
        if type(section) != int or section not in range(1, section_count + 1):
            raise Blocked("image_plan_section_invalid")
        if kind not in {"photo", "illustration"} or (channel == "legends" and kind != "illustration") or (channel == "cinema" and kind != "photo"):
            raise Blocked("image_plan_kind_invalid")
        if any(not isinstance(row.get(k), str) or not 5 <= len(row[k].strip()) <= limit
               for k, limit in (("subject", 180), ("brief", 950))):
            raise Blocked("image_plan_brief_invalid")
        if kind == "photo" and (not isinstance(row.get("query"), str)
                                or not 2 <= len(row["query"].strip()) <= 150):
            raise Blocked("image_plan_query_invalid")
        brief = row["brief"].casefold().strip()
        if brief in descriptions:
            raise Blocked("image_plan_duplicate_prompt")
        sections.add(section)
        descriptions.add(brief)
        kinds.add(kind)
    if len(sections) != section_count or (channel == "science" and len(kinds) != 2):
        raise Blocked("image_plan_missing_section_or_medium")


def commons_photos(query):
    for row in commons.search(query + " filetype:bitmap" if len(query) < 133 else query, limit=12,
                              opener=lambda req, timeout: io.BytesIO(
                                  request(req.full_url, COMMONS, timeout=timeout, limit=3_000_000)))["candidates"]:
        meta = " ".join(row[k] for k in ("title", "description", "categories"))
        terms = row["license_url"].replace("http://", "https://").removesuffix("/deed.en")
        verified = (row["license_claimed"] in LICENSES
                    and terms.rstrip("/") + "/" == LICENSES[row["license_claimed"]]
                    and row["artist"] and not row["restrictions"])
        if NONPHOTO.search(meta) or RESTRICTED.search(row["credit"]):
            continue
        yield {"kind": "photo", "source": "Wikimedia Commons", "source_page": row["source_page"],
               "asset_url": row["asset_url"], "creator": row["artist"], "credit": row["credit"],
               "rights_status": "license_metadata_verified" if verified else "rights_review_pending",
               "license": row["license_claimed"], "license_url": terms,
               "attribution": row["attribution"] or row["artist"],
               "description": row["title"] + ": " + row["description"], "domains": COMMONS}


def nasa_photos(query):
    params = urlencode({"q": query, "media_type": "image", "page_size": 14})
    url = "https://images-api.nasa.gov/search?" + params
    result = json.loads(request(url, NASA, timeout=15, limit=3_000_000))
    for item in result.get("collection", {}).get("items", []):
        meta = (item.get("data") or [{}])[0]
        description = f"{meta.get('title', '')}: {meta.get('description', '')}"
        author = meta.get("photographer") or meta.get("secondary_creator") or ""
        asset = next((x.get("href") for x in item.get("links", [])
                      if x.get("rel") == "preview"), None)
        if (not meta.get("nasa_id") or not asset or NONPHOTO.search(description)
                or RESTRICTED.search(description) or meta.get("copyright")):
            continue
        # NASA's general policy has exceptions. Review the specific credit before approval.
        yield {"kind": "photo", "source": "NASA Image and Video Library",
               "source_page": "https://images.nasa.gov/details/" + quote(meta["nasa_id"], safe=""),
               "asset_url": asset, "creator": author, "credit": meta.get("center", ""),
               "rights_status": "rights_review_pending", "license": "NASA media guidelines",
               "license_url": NASA_TERMS, "attribution": author,
               "description": description[:1300], "domains": NASA}


@lru_cache(maxsize=1)
def clip_model():
    try:
        from transformers import CLIPModel, CLIPProcessor
        model_id = "openai/clip-vit-base-patch32"
        processor = CLIPProcessor.from_pretrained(model_id)
        return model_id, processor, CLIPModel.from_pretrained(model_id).eval()
    except (ImportError, OSError, ValueError, RuntimeError):
        raise Blocked("visual_matcher_unavailable") from None


VISUAL_LOCK = Lock()


def similarity(image, captions):
    try:
        with VISUAL_LOCK:
            import torch
            model_id, processor, model = clip_model()
            batch = processor(text=captions, images=image, return_tensors="pt", padding=True,
                              truncation=True, max_length=77)
            with torch.inference_mode():
                result = model(**batch)
                scores = (result.logits_per_image / model.logit_scale.exp())[0].tolist()
    except (ImportError, OSError, ValueError, RuntimeError, TypeError):
        raise Blocked("visual_matcher_unavailable") from None
    return model_id, [round(x, 4) for x in scores]


def image_hashes(path):
    from PIL import Image, ImageOps
    import imagehash
    with Image.open(path) as image:
        return digest(path.read_bytes()), str(imagehash.phash(ImageOps.exif_transpose(image)))


class ImageIndex:
    def __init__(self, memory, archive):
        self.memory = memory
        self.archive = []
        for path in archive:
            try:
                self.archive.append(image_hashes(path))
            except (OSError, ValueError):
                continue

    def save(self, raw, metadata, scene, subject, path):
        from PIL import Image, ImageOps, ImageStat
        import imagehash
        with Image.open(io.BytesIO(raw)) as file:
            if file.width * file.height > 22_000_000 or min(file.size) < 480:
                raise ValueError("image_dimensions")
            if file.format not in ("JPEG", "PNG", "WEBP") or getattr(file, "is_animated", False):
                raise ValueError("image_format")
            image = ImageOps.exif_transpose(file).convert("RGB")
        model, scores = similarity(image, [scene["subject"], subject,
                                            "an unrelated image with no connection with this topic"])
        if scores[0] < .19 or scores[0] <= scores[2] + .015:
            raise ValueError("image_scene_match_below_threshold")
        contrast = ImageStat.Stat(image.convert("L")).stddev[0]
        if contrast < 15:
            raise ValueError("image_low_contrast")
        quality = (min(1, image.width / 1200) * .3
                   + min(1, contrast / 60) * .25
                   + max(0, 1 - abs(image.width / image.height - 1.5)) * .45)
        image.thumbnail((1440, 1200))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=89, optimize=True)
        rendered = buffer.getvalue()
        raw_sha, sha, phash = digest(raw), digest(rendered), str(imagehash.phash(image))
        with self.memory.lock:
            hashes = list(self.archive)
            for existing in self.memory.root.glob("*/*/images/*.json"):
                if existing != path.with_suffix(".json"):
                    row = read_json(existing)
                    hashes.extend([(row["sha256"], row["perceptual_hash"]),
                                   (row["original_sha256"], row["perceptual_hash"])])
            if any(previous in (sha, raw_sha) or (int(phash, 16) ^ int(near, 16)).bit_count() <= 6
                   for previous, near in hashes):
                raise ValueError("image_duplicate")
            row = {**metadata, "file": path.name, "sha256": sha, "original_sha256": raw_sha,
                   "perceptual_hash": phash, "size": list(image.size), "match_model": model,
                   "scene_similarity": scores[0], "topic_similarity": scores[1],
                   "cover_score": round(scores[1] * 2 + scores[0] * .3 + quality * .3, 4),
                   "section": scene["section"], "scene": scene["subject"]}
            write(path, rendered)
            write_json(path.with_suffix(".json"), row)
            self.memory.save()
        return row


def photo(scene, subject, path, index):
    had_candidate = False
    errors = []
    for find in (commons_photos, nasa_photos):
        if index.memory.time_left() < 70:
            raise Blocked("production_deadline")
        try:
            for n, row in enumerate(find(scene["query"])):
                if n >= 5 or index.memory.time_left() < 40:
                    break
                had_candidate = True
                try:
                    raw = request(row["asset_url"], row.pop("domains"), timeout=10)
                    row["availability"] = "downloaded"
                    return index.save(raw, row, scene, subject, path)
                except (ValueError, OSError, ProviderError) as exc:
                    errors.append(str(exc) if isinstance(exc, ProviderError) else type(exc).__name__)
        except (ProviderError, ValueError, OSError) as exc:
            errors.append(str(exc) if isinstance(exc, ProviderError) else type(exc).__name__)
    detail = "photo_candidates_rejected" if had_candidate else "no_traceable_photo_found"
    raise Blocked(detail + (": " + ", ".join(dict.fromkeys(errors)) if errors else ""))


def generated(scene, subject, path, index, task, article, channel):
    style = ("Cinematic realistic urban mystery, restrained color grading and atmospheric lighting."
             if channel == "legends" else
             "Modern moderately stylized science editorial illustration with a coherent muted palette. "
             "Explanatory concept art with accurate geometry where applicable. Do not mimic documentary "
             "evidence, scientific measurements or labeled diagrams.")
    for attempt in range(2):
        if index.memory.time_left() < 150:
            raise Blocked("production_deadline")
        prompt = f"{scene['brief']} Depict {scene['subject']}. {style} Landscape. No text or watermark."
        if attempt:
            prompt += " Isolate the main subject in a clear composition with distinct foreground and background."
        raw, params = index.memory.call(task, article, "klein", lambda: klein(prompt))
        try:
            return index.save(raw, {"kind": "illustration", "source": "Cloudflare Workers AI",
                                    "model": KLEIN, "prompt": prompt, "parameters": params,
                                    "rights_status": "rights_review_pending",
                                    "rights_basis": "Generated via Cloudflare Workers AI; review terms and depicted material.",
                                    "label": "ИИ-иллюстрация"},
                              scene, subject, path)
        except (OSError, ValueError):
            if attempt:
                raise Blocked("generated_image_quality_or_match_failed") from None
    raise AssertionError("unreachable image attempt")


def select_cover(rows):
    populated = Counter(row["section"] for row in rows)
    eligible = [row for row in rows if row["section"] == 1 or populated[row["section"]] > 1]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row["cover_score"], row["sha256"]))["file"]
