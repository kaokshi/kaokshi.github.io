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
from . import editorial_scene_qa as qa
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
    **{f"CC-{code.upper()}-{version}": f"https://creativecommons.org/licenses/{code}/{version}/"
       for code in ("by", "by-sa") for version in ("2.0", "2.5", "3.0", "4.0")},
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}


def validate_plan(plan, section_count, channel, settings, *, sourced_illustrations=False):
    blocks = plan.get("images")
    # Each story has one independent cover immediately after the contents, then
    # exactly one distinct illustration immediately below every section heading.
    if not 6 <= section_count <= 7:
        raise Blocked("image_plan_layout_requires_six_or_seven_sections")
    if (not isinstance(blocks, list) or len(blocks) != section_count + 1
            or not settings["minimum_images"] <= len(blocks) <= settings["maximum_images"]):
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
        allowed = {"photo", "illustration"}
        if channel == "legends" and not sourced_illustrations:
            allowed = {"illustration"}
        elif channel == "cinema":
            allowed = {"photo"}
        if kind not in allowed:
            raise Blocked("image_plan_kind_invalid")
        if any(not isinstance(row.get(k), str) or not 5 <= len(row[k].strip()) <= limit
               for k, limit in (("subject", 180), ("brief", 950))):
            raise Blocked("image_plan_brief_invalid")
        if (kind == "photo" or sourced_illustrations) and (not isinstance(row.get("query"), str)
                                or not 2 <= len(row["query"].strip()) <= 150):
            raise Blocked("image_plan_query_invalid")
        brief = row["brief"].casefold().strip()
        if brief in descriptions:
            raise Blocked("image_plan_duplicate_prompt")
        sections.add(section)
        descriptions.add(brief)
        kinds.add(kind)
    distribution = Counter(row["section"] for row in blocks)
    if distribution[1] != 2 or any(distribution[i] != 1 for i in range(2, section_count + 1)):
        raise Blocked("image_plan_layout_distribution")
    if len(sections) != section_count or (channel == "science" and len(kinds) != 2):
        raise Blocked("image_plan_missing_section_or_medium")


def commons_photos(query, *, kind="photo", verified_only=False):
    for row in commons.search(query + " filetype:bitmap" if len(query) < 133 else query, limit=12,
                              opener=lambda req, timeout: io.BytesIO(
                                  request(req.full_url, COMMONS, timeout=timeout, limit=3_000_000)))["candidates"]:
        meta = " ".join(row[k] for k in ("title", "description", "categories"))
        terms = row["license_url"].replace("http://", "https://").removesuffix("/deed.en")
        verified = (row["license_claimed"] in LICENSES
                    and terms.rstrip("/") + "/" == LICENSES[row["license_claimed"]]
                    and row["artist"] and not row["restrictions"])
        is_illustration = bool(NONPHOTO.search(meta))
        if (is_illustration != (kind == "illustration") or RESTRICTED.search(row["credit"])
                or (verified_only and not verified)):
            continue
        yield {"kind": kind, "source": "Wikimedia Commons", "source_page": row["source_page"],
               "asset_url": row["asset_url"], "creator": row["artist"], "credit": row["credit"],
               "rights_status": "license_metadata_verified" if verified else "rights_review_pending",
               "license": row["license_claimed"], "license_url": terms,
               "attribution": row["attribution"] or row["artist"],
               "description": row["title"] + ": " + row["description"],
               "original_asset_url": row["original_asset_url"], "domains": COMMONS}


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
               "description": description[:1300],
               "asset_manifest": "https://images-api.nasa.gov/asset/" + quote(meta["nasa_id"], safe=""),
               "domains": NASA}


@lru_cache(maxsize=1)
def clip_model():
    try:
        from transformers import CLIPModel, CLIPProcessor
        from .editorial_visual_matching import CLIP

        model_id, revision = CLIP
        processor = CLIPProcessor.from_pretrained(model_id, revision=revision, use_fast=False)
        return model_id + "@" + revision, processor, CLIPModel.from_pretrained(model_id, revision=revision).eval()
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise Blocked("visual_matcher_unavailable_" + type(exc).__name__) from None


VISUAL_LOCK = Lock()


def warm_visual_matcher():
    with VISUAL_LOCK:
        clip_model()


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
                embedding = result.image_embeds[0].tolist()
    except (ImportError, OSError, ValueError, RuntimeError, TypeError) as exc:
        raise Blocked("visual_matcher_unavailable_" + type(exc).__name__) from None
    return model_id, [round(x, 4) for x in scores], [round(x, 4) for x in embedding]


def image_hashes(path):
    from PIL import Image, ImageOps
    import imagehash
    with Image.open(path) as image:
        return digest(path.read_bytes()), str(imagehash.phash(ImageOps.exif_transpose(image)))


def decode_candidate(raw):
    from PIL import Image, ImageOps, ImageStat

    with Image.open(io.BytesIO(raw)) as file:
        if file.width * file.height > 22_000_000 or min(file.size) < 480:
            raise ValueError("image_dimensions")
        if file.format not in ("JPEG", "PNG", "WEBP") or getattr(file, "is_animated", False):
            raise ValueError("image_format")
        image = ImageOps.exif_transpose(file).convert("RGB")
    contrast = ImageStat.Stat(image.convert("L")).stddev[0]
    if contrast < 15:
        raise ValueError("image_low_contrast")
    return image, contrast


class ImageIndex:
    def __init__(self, memory, archive):
        self.memory = memory
        self.archive = []
        for path in archive:
            try:
                self.archive.append(image_hashes(path))
            except (OSError, ValueError):
                continue

    def save(self, raw, metadata, scene, subject, path, *, preserve=False):
        import imagehash
        image, contrast = decode_candidate(raw)
        card = scene.get("scene_card")
        if not card:
            raise Blocked("image_scene_card_required")
        scene_caption = ", ".join([card["location"], *card["must_have"]])
        model, scores, embedding = similarity(image, [scene_caption, subject,
                                            "an unrelated image with no connection with this topic"])
        clip = {"model": model, "scene": scores[0], "topic": scores[1], "unrelated": scores[2],
                "passed": scores[0] >= .19 and scores[0] > scores[2] + .015}
        quality = (min(1, image.width / 1200) * .3
                   + min(1, contrast / 60) * .25
                   + max(0, 1 - abs(image.width / image.height - 1.5)) * .45)
        image.thumbnail((1440, 1200))
        buffer = io.BytesIO()
        if preserve:
            rendered = raw
        else:
            image.save(buffer, format="JPEG", quality=89, optimize=True)
            rendered = buffer.getvalue()
        raw_sha, sha, phash = digest(raw), digest(rendered), str(imagehash.phash(image))
        previous = path.parent / "image-qa" / path.stem / (sha + ".json")
        decision = qa.current_report(previous, card, sha)
        # Source and duplicate checks may change independently of image pixels.
        if not decision or decision["status"] != "accepted":
            decision = qa.judge(card, image, sha, clip, quality={
                "passed": True, "size": list(image.size), "contrast": round(contrast, 3)})
        qa.apply_channel_policy(decision, scene["channel"], metadata,
                                film=scene.get("film"), spoilers=scene.get("spoilers", False))
        with self.memory.lock:
            saved = read_json(path.with_suffix(".json"))
            if (path.is_file() and saved and qa.accepted_for(saved, card, is_cover=scene["is_cover"])
                    and digest(path.read_bytes()) == saved["sha256"] and saved["sha256"] != sha):
                raise Blocked("verified_review_image_replacement_conflict")
            hashes = list(self.archive)
            vectors = []
            for existing in self.memory.root.glob("*/*/images/*.json"):
                if existing != path.with_suffix(".json"):
                    row = read_json(existing)
                    hashes.extend([(row["sha256"], row["perceptual_hash"]),
                                   (row["original_sha256"], row["perceptual_hash"])])
                    if row.get("match_model") == model and row.get("image_embedding"):
                        vectors.append(row["image_embedding"])
            identical = any(previous in (sha, raw_sha) or (int(phash, 16) ^ int(near, 16)).bit_count() <= 6
                            for previous, near in hashes)
            near_duplicate = any(sum(a * b for a, b in zip(embedding, previous)) > .965 for previous in vectors)
            decision.update(generations=metadata.get("generation", 0),
                            requests=metadata.get("generation", 0),
                            duplicate=identical or near_duplicate)
            if decision["duplicate"]:
                decision["status"] = "rejected"
                if "image_duplicate" not in decision["reasons"]:
                    decision["reasons"].append("image_duplicate")
            qa.report_candidate(path, decision)
            if decision["status"] != "accepted":
                raise qa.ImageQAError(decision)
            row = {**metadata, "file": path.name, "sha256": sha, "original_sha256": raw_sha,
                   "perceptual_hash": phash, "size": list(image.size), "match_model": model, "image_embedding": embedding,
                   "scene_similarity": scores[0], "topic_similarity": scores[1],
                   "cover_score": round(scores[1] * 2 + scores[0] * .3 + quality * .3, 4),
                   "section": scene["section"], "scene": scene["subject"]}
            row["image_qa"] = decision
            row["is_cover"] = scene["is_cover"]
            write(path, rendered)
            write_json(path.with_suffix(".json"), row)
            self.memory.save()
        return row


def photo(scene, subject, path, index, *, experimental=False, verified_only=False):
    if not scene.get("scene_card"):
        raise Blocked("image_scene_card_required")
    if experimental:
        from .editorial_image_experiment import ranked_photo

        return ranked_photo(scene, subject, path, index)
    warm_visual_matcher()
    had_candidate = False
    errors = []
    def find_commons(query):
        return commons_photos(query, kind=scene.get("kind", "photo"), verified_only=verified_only)

    sources = (find_commons,) if verified_only else (find_commons, nasa_photos)
    for find in sources:
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
                except qa.ImageQAError as exc:
                    decision = exc.args[0]
                    if decision["status"] == "provider_unavailable":
                        raise Blocked("image_review_required_provider_unavailable") from None
                    errors.append(decision["status"])
                except (ValueError, OSError, ProviderError) as exc:
                    errors.append(str(exc) if isinstance(exc, ProviderError) else type(exc).__name__)
        except (ProviderError, ValueError, OSError) as exc:
            errors.append(str(exc) if isinstance(exc, ProviderError) else type(exc).__name__)
    detail = "photo_candidates_rejected" if had_candidate else "no_traceable_photo_found"
    raise Blocked(detail + (": " + ", ".join(dict.fromkeys(errors)) if errors else ""))


def generated(scene, subject, path, index, task, article, channel):
    if not scene.get("scene_card"):
        raise Blocked("image_scene_card_required")
    warm_visual_matcher()
    style = ("Cinematic realistic urban mystery, restrained color grading and atmospheric lighting."
             if channel == "legends" else
             "Modern moderately stylized science editorial illustration with a coherent muted palette. "
             "Explanatory concept art with accurate geometry where applicable. Do not mimic documentary "
             "evidence, scientific measurements or labeled diagrams.")
    return generated_scene(scene, subject, path, index, task, article, channel, style)


def generated_scene(scene, subject, path, index, task, article, channel, style):
    card = scene["scene_card"]
    base = f"{qa.visual_prompt(card)} {style} Landscape. No text or watermark."
    prompt = base
    for attempt in range(1, 4):
        stem = path.parent / "image-qa" / path.stem / f"klein-{attempt}"
        blob, checkpoint = stem.with_suffix(".candidate"), stem.with_suffix(".request.json")
        saved = read_json(checkpoint)
        if saved and saved.get("prompt") == prompt and blob.is_file() and digest(blob.read_bytes()) == saved["sha256"]:
            raw, params = blob.read_bytes(), saved["parameters"]
        else:
            if saved is not None or blob.exists():
                raise Blocked("klein_saved_candidate_needs_recovery")
            if index.memory.time_left() < 150:
                raise Blocked("production_deadline")
            if not qa.allocation(index.memory, article):
                break
            def create():
                candidate, parameters = klein(prompt)
                write(blob, candidate)
                write_json(checkpoint, {"prompt": prompt, "parameters": parameters, "sha256": digest(candidate)})
                return candidate, parameters

            raw, params = index.memory.call(f"{task}.candidate-{attempt}", article, "klein", create)
        try:
            return index.save(raw, {"kind": "illustration", "source": "Cloudflare Workers AI",
                                    "model": KLEIN, "prompt": prompt, "parameters": params, "generation": attempt,
                                    "rights_status": "rights_review_pending",
                                    "rights_basis": "Generated via Cloudflare Workers AI; review terms and depicted material.",
                                    "label": "ИИ-иллюстрация" if channel != "legends" else None},
                              scene, subject, path)
        except qa.ImageQAError as exc:
            row = exc.args[0]
            if row["status"] in {"needs_review", "provider_unavailable"}:
                raise Blocked("image_review_required_" + row["status"]) from None
            prompt = base + " Corrections: " + ". ".join(qa.corrections(card, row))
        except (ValueError, OSError) as exc:
            qa.report_candidate(path, {"image_sha256": digest(raw), "status": "rejected",
                                       "schema": qa.SCHEMA, "scene_card": card, "generations": attempt,
                                       "requests": attempt, "reasons": ["invalid_image_" + type(exc).__name__]})
            prompt = base + " Ensure clean, readable, high-contrast framing."
    if scene.get("query"):
        return photo({**scene, "kind": "photo"}, subject, path, index, verified_only=True)
    raise Blocked("image_review_required_no_verified_candidate")


def select_cover(rows):
    populated = Counter(row["section"] for row in rows)
    eligible = [row for row in rows if row["section"] == 1 or populated[row["section"]] > 1]
    if any("is_cover" in row for row in rows):
        covers = [row for row in rows if row.get("is_cover") is True]
        return covers[0]["file"] if len(covers) == 1 else None
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row["cover_score"], row["sha256"]))["file"]
