"""Opt-in scene ranking, with pinned CPU models and independent caption evidence."""
from dataclasses import dataclass
from functools import lru_cache
import re

from .editorial_http import Blocked

SIGLIP = ("google/siglip2-base-patch16-224", "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2")
FLORENCE = ("florence-community/Florence-2-base", "00921df66db728a9ceb750f5eca43e5c203a2051")
CLIP = ("openai/clip-vit-base-patch32", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268")
FLORENCE_TASK = "<MORE_DETAILED_CAPTION>"
SHORTLIST = 5
MAX_CANDIDATES = 20


@dataclass(frozen=True)
class Scene:
    subject: str
    required: dict
    forbidden: tuple

    @classmethod
    def read(cls, row):
        subject, criteria, wrong = (row.get(key) for key in ("subject", "required_features", "forbidden"))
        if (not isinstance(subject, str) or not 5 <= len(subject) <= 250
                or not isinstance(criteria, dict) or not 1 <= len(criteria) <= 8
                or not isinstance(wrong, list) or not 1 <= len(wrong) <= 10):
            raise ValueError("invalid_visual_criteria")
        if any(not isinstance(group, list) or not 1 <= len(group) <= 12 for group in criteria.values()):
            raise ValueError("invalid_visual_alias_group")
        phrases = [*wrong, *(word for group in criteria.values() for word in group)]
        if any(not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 90 for phrase in phrases):
            raise ValueError("invalid_visual_phrase")
        return cls(subject, criteria, tuple(wrong))


def mention(caption, phrase):
    words = re.findall(r"[a-z0-9]+", phrase.casefold())
    if not words:
        return False
    pattern = re.compile(r"\b" + r"[\s-]+".join(map(re.escape, words)) + r"\b")
    for match in pattern.finditer(caption.casefold()):
        prefix = caption[:match.start()].casefold()
        if not re.search(r"\b(?:no|not|without|rather than)\s+(?:\w+\s+){0,3}$", prefix):
            return True
    return False


def verify_caption(scene, caption):
    contradictions = [phrase for phrase in scene.forbidden if mention(caption, phrase)]
    missing = [feature for feature, aliases in scene.required.items()
               if not any(mention(caption, phrase) for phrase in aliases)]
    verdict = "contradiction" if contradictions else "uncertain" if missing else "supported"
    return {"verdict": verdict, "missing": missing, "contradictions": contradictions, "caption": caption}


@lru_cache(maxsize=1)
def load_model(kind):
    try:
        from transformers import AutoProcessor, CLIPModel, Florence2ForConditionalGeneration, SiglipModel

        path, revision = {"clip": CLIP, "siglip": SIGLIP, "florence": FLORENCE}[kind]
        processor = AutoProcessor.from_pretrained(path, revision=revision, trust_remote_code=False,
                                                  use_fast=False)
        implementation = {"clip": CLIPModel, "siglip": SiglipModel,
                          "florence": Florence2ForConditionalGeneration}[kind]
        options = {"use_safetensors": True} if kind != "clip" else {}
        return processor, implementation.from_pretrained(path, revision=revision, **options).eval()
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        raise Blocked(f"{kind}_unavailable_{type(exc).__name__}") from exc


def compare_pixels(kind, image, captions):
    import torch

    processor, model = load_model(kind)
    if kind == "clip":
        batch = processor(images=image, text=captions, padding=True, truncation=True,
                          max_length=77, return_tensors="pt")
    else:
        batch = processor(images=image, text=captions, padding="max_length", truncation=True,
                          max_length=64, return_tensors="pt")
    with torch.inference_mode():
        result = model(**batch)
        scores = result.logits_per_image[0]
        if kind == "clip":
            scores = scores / model.logit_scale.exp()
        embedding = torch.nn.functional.normalize(result.image_embeds[0], dim=0).tolist()
        return ([float(value) for value in scores.tolist()], embedding)


def describe_pixels(image):
    import torch

    processor, model = load_model("florence")
    inputs = processor(text=FLORENCE_TASK, images=image, return_tensors="pt")
    with torch.inference_mode():
        sequence = model.generate(**inputs, max_new_tokens=48, num_beams=1, do_sample=False)
    raw = processor.batch_decode(sequence, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(raw, task=FLORENCE_TASK,
                                               image_size=(image.width, image.height))
    caption = parsed.get(FLORENCE_TASK)
    if not isinstance(caption, str) or not caption.strip():
        raise Blocked("florence_caption_missing")
    return caption


def rank_key(scores):
    # SigLIP logits are model scores, never probabilities or certainty.
    scene, _, unrelated, *alternatives = scores
    return scene, scene - max([unrelated, *alternatives])


def baseline_order(rows):
    accepted = [row for row in rows if row["scores"][0] >= .19 and
                row["scores"][0] > row["scores"][2] + .015]
    return accepted


def shortlist(rows):
    return sorted(rows, key=lambda row: rank_key(row["scores"]), reverse=True)[:SHORTLIST]


def verified_order(scene, rows, captions):
    judged = []
    for row in shortlist(rows):
        key = row["id"]
        judged.append({**row, "caption_check": verify_caption(scene, captions[key])})
    accepted = [row for row in judged if row["caption_check"]["verdict"] == "supported"
                and rank_key(row["scores"])[1] > 0]
    return accepted, judged
