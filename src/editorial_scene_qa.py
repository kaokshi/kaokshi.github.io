"""Grounded scene cards and a fail-closed pixel-level acceptance policy."""

from functools import lru_cache
import re
from threading import Lock
from time import monotonic

from .editorial_memory import digest, json_bytes, read_json, write_json
from .editorial_providers import Blocked

SCHEMA = "editorial-scene-qa-v2"
SECTION_FIELDS = ("location", "time_of_day", "visual_description")
LIST_FIELDS = ("characters", "must_have", "must_not_have", "optional_details")
TYPES = {"environment", "event", "object", "character"}
VERDICTS = {"present", "absent", "uncertain"}
MODELS = {
    "smol": ("HuggingFaceTB/SmolVLM-256M-Instruct", "d039ab0d4b9311b617e8ffec1d06c8464364aeb3"),
    "blip": ("Salesforce/blip-vqa-base", "787b3d35d57e49572baabd22884b3d5a05acf072"),
}
VISION_LOCK = Lock()
CARD_RULES = """
У каждого изображения обязателен scene_card. Верни scene_type (environment, event, object, character),
location и time_of_day (английский; пустое время если неизвестно), characters, must_have, must_not_have,
optional_details (списки конкретных видимых элементов по-английски), visual_description (точная английская
композиция, включая видимые действия и освещение только если они обоснованы).
Добавь evidence_spans в виде объекта: ключи дословно совпадают с location и всеми элементами must_have,
characters и optional_details, и с time_of_day, если заполнено; значения — буквальные русские цитаты
из ЭТОГО раздела. Для двух изображений первого раздела каждый паспорт опирается на первый раздел.
Достаточно 1–3 ключевых элементов. Указывай место до уровня видимого объекта или обстановки.
Выбирай ОДИН видимый момент: не совмещай события разных абзацев, эпох или мест. Проверяемы только
предметы, место, персонажи, действия и отношения в кадре. Не выдумывай обязательных деталей.
Если смысл эпизода зависит от того, где персонаж или предмет находится относительно другого,
запиши это пространственное отношение в must_have как одно видимое условие. Наличие обоих
объектов по отдельности недостаточно. Указывай действующее лицо, если от него зависит событие.
must_not_have перечисляет только явные видимые противоречия выбранной сцене и может быть пустым.
Для трудно различимого места можно добавить location_cues (2–4 разных видимых признака места),
обоснованных цитатой location без дополнительных сюжетных фактов. Для кино о свете и операторской
работе выбирай соответствующую наблюдаемую среду без обязательного героя.
Кадр фильма подтверждай видимым отличием эпизода. В полях для визуальных вопросов опиши внешность и
действия без имени; идентичность актёра, персонажа и фильма отдельно проверяется по источнику.
Для науки учитывай видимые пространственные связи и направление механизма. Никаких вымышленных этикеток.
"""


def make_card(article_id, section, source_sha, section_text, proposal):
    if (not isinstance(proposal, dict) or not isinstance(proposal.get("scene_type"), str)
            or proposal["scene_type"] not in TYPES):
        raise Blocked("scene_card_type_invalid")
    card = {k: proposal.get(k) for k in (*SECTION_FIELDS, *LIST_FIELDS, "scene_type", "evidence_spans")}
    for name in SECTION_FIELDS:
        value = card[name]
        if (not isinstance(value, str) or len(value) > (1100 if name == "visual_description" else 140)
                or (name != "time_of_day" and not value.strip())):
            raise Blocked("scene_card_text_invalid")
        card[name] = value.strip()
    for name in LIST_FIELDS:
        values = card[name]
        if (not isinstance(values, list) or len(values) > 6 or (name == "must_have" and not values)
                or any(not isinstance(item, str) or not 3 <= len(item.strip()) <= 140 for item in values)
                or len({item.strip().casefold() for item in values}) != len(values)):
            raise Blocked("scene_card_items_invalid")
        card[name] = [item.strip() for item in values]
    grounded = set([card["location"], *card["must_have"], *card["characters"], *card["optional_details"]])
    if card["time_of_day"]:
        grounded.add(card["time_of_day"])
    spans = card["evidence_spans"]
    if (not isinstance(spans, dict) or set(spans) != grounded
            or any(not isinstance(quote, str) or not 5 <= len(quote) <= 420 or quote not in section_text
                   for quote in spans.values())):
        raise Blocked("scene_card_evidence_not_in_section")
    if {item.casefold() for item in grounded} & {item.casefold() for item in card["must_not_have"]}:
        raise Blocked("scene_card_self_contradictory")
    cues = proposal.get("location_cues", [])
    if (not isinstance(cues, list) or (cues and not 2 <= len(cues) <= 4)
            or any(not isinstance(x, str) or not 3 <= len(x.strip()) <= 140 for x in cues)
            or len({x.strip().casefold() for x in cues}) != len(cues)
            or {x.strip().casefold() for x in cues} & {x.casefold() for x in (*grounded, *card["must_not_have"])}):
        raise Blocked("scene_card_location_cues_invalid")
    if cues:
        card["location_cues"] = [x.strip() for x in cues]
    return {**card, "article_id": article_id, "section_id": section,
            "section_text_sha256": digest(section_text), "source_text_sha256": source_sha}


def normalize_plan(plan):
    """Reuse the grounded card as the single source of duplicated prompt fields."""
    images = plan.get("images")
    if not isinstance(images, list):
        return plan
    normalized = []
    for row in images:
        if not isinstance(row, dict) or not isinstance(row.get("scene_card"), dict):
            normalized.append(row)
            continue
        copy = dict(row)
        card = row["scene_card"]
        for field, alternate, limit in (("subject", "location", 180), ("brief", "visual_description", 950)):
            original, derived = copy.get(field), card.get(alternate)
            if ((not isinstance(original, str) or not 5 <= len(original.strip()) <= limit)
                    and isinstance(derived, str) and 5 <= len(derived.strip()) <= limit):
                copy[field] = derived
        normalized.append(copy)
    return {**plan, "images": normalized}


def section_text(section):
    return "\n".join([section["heading"], *section["paragraphs"]])


def attach_cards(plan, article_id, article, source_sha, *, article_title=None):
    rows = []
    for scene in plan["images"]:
        section = article["sections"][scene["section"] - 1]
        card = make_card(article_id, scene["section"], source_sha, section_text(section), scene.get("scene_card"))
        if article_title:
            card.update(article_title=article_title, section_title=section["heading"])
        rows.append({**scene, "scene_card": card})
    return {**plan, "images": rows}


def visual_prompt(card):
    return (
        f"{card['visual_description']} Setting: {card['location']}. "
        + (f"Time: {card['time_of_day']}. " if card["time_of_day"] else "")
        + (f"Characters in the same moment: {', '.join(card['characters'])}. " if card["characters"] else "")
        + f"Clearly show: {', '.join(card['must_have'])}. "
        + (f"Do not show: {', '.join(card['must_not_have'])}. " if card["must_not_have"] else "")
    )


@lru_cache(maxsize=len(MODELS))
def load_vision(kind):
    import torch
    from transformers import AutoProcessor, BlipForQuestionAnswering, SmolVLMForConditionalGeneration

    torch.set_num_threads(min(torch.get_num_threads(), 2))
    model_id, rev = MODELS[kind]
    common = {"revision": rev, "trust_remote_code": False}
    processor = AutoProcessor.from_pretrained(model_id, **common)
    architecture = SmolVLMForConditionalGeneration if kind == "smol" else BlipForQuestionAnswering
    model = architecture.from_pretrained(model_id, use_safetensors=True, **common).eval()
    return processor, model


def ask_pixels(kind, image, questions):
    import torch

    with VISION_LOCK, torch.inference_mode():
        processor, model = load_vision(kind)
        if kind == "blip":
            batch = processor(images=[image] * len(questions), text=questions,
                              padding=True, return_tensors="pt")
            generated = model.generate(**batch, max_new_tokens=16, do_sample=False)
        else:
            template = [[{"role": "user", "content": [
                {"type": "image"}, {"type": "text", "text": question}]}] for question in questions]
            prompts = [processor.apply_chat_template(row, add_generation_prompt=True) for row in template]
            processor.tokenizer.padding_side = "left"
            processor.image_processor.do_image_splitting = False
            batch = processor(text=prompts, images=[[image] for _ in questions],
                              padding=True, return_tensors="pt")
            generated = model.generate(**batch, max_new_tokens=12, do_sample=False,
                                       use_cache=True)[:, batch["input_ids"].shape[1]:]
        return [answer.strip() for answer in processor.batch_decode(generated, skip_special_tokens=True)]


def binary_answer(answer):
    # Unrecognized or mixed answers are not evidence.
    match = re.fullmatch(r"(yes|no)[.!]?", answer.strip(), re.I)
    return {"yes": "present", "no": "absent"}[match[1].lower()] if match else "uncertain"


def questions_for(card):
    rows = [("setting", card["location"]), *(("required", item) for item in
            dict.fromkeys([*card["must_have"], *card["characters"], card["time_of_day"]])
            if item and item != card["location"]),
            *(("location_cue", item) for item in card.get("location_cues", [])),
            *(("forbidden", item) for item in card["must_not_have"])]
    statements = [question for role, item in rows for question in (
        f"Is this {item}?" if role == "setting" else f"Is there {item} in the image?",
        f"Can you see {item} in this picture?",
    )]
    observations = [
        "Where does the scene take place? Describe only what is visible.",
        "What main objects and people are visible?",
        "What are the people visibly doing?",
    ]
    return rows, statements + observations


def inspect_pixels(card, image, kinds=("blip",), *, ask=ask_pixels):
    rows, questions = questions_for(card)
    perceptions = {}
    n = len(rows) * 2
    for kind in kinds:
        replies = ask(kind, image, questions)
        if len(replies) != len(questions):
            raise ValueError("vision_reply_count")
        perceptions[kind] = {"model": list(MODELS[kind]), "answers": replies[:n],
                             "observations": dict(zip(("place", "objects_and_people", "action"), replies[n:]))}
    findings = []
    for ix, (role, item) in enumerate(rows):
        votes = {kind: [binary_answer(a) for a in result["answers"][ix * 2:ix * 2 + 2]]
                 for kind, result in perceptions.items()}
        answers = {status for pair in votes.values() for status in pair}
        findings.append({"role": role, "element": item,
                         "status": next(iter(answers)) if len(answers) == 1 else "uncertain", "votes": votes})
    return {"models": perceptions, "findings": findings}


class ImageQAError(ValueError):
    pass


def judge(card, image, image_sha, clip, *, duplicate=False, quality=None, clip_gate=True, vision=inspect_pixels):
    started = monotonic()
    report = {"schema": SCHEMA, "scene_card": card, "scene_card_sha256": digest(json_bytes(card)),
              "article_id": card["article_id"], "section_id": card["section_id"],
              "source_text_sha256": card["source_text_sha256"], "image_sha256": image_sha,
              "pixel_input_sha256": image_sha, "clip": clip,
              "scene_match": "unverified", "atmosphere_match": {"clip_topic_score": clip.get("topic")},
              "visual_quality": quality or {}, "duplicate": duplicate, "vision": None,
              "confirmed": [], "absent": [], "uncertain": [], "contradictions": [], "reasons": [],
              "must_have_coverage": None}
    try:
        evidence = vision(card, image)
        if not evidence.get("findings"):
            raise ValueError("vision_evidence_missing")
    except Exception as exc:
        # A model/configuration/network failure must leave a trace and cannot approve an image.
        report.update(status="provider_unavailable", reasons=["vision_unavailable_" + type(exc).__name__])
    else:
        report["vision"] = evidence
        rows = evidence["findings"]
        cues = [r for r in rows if r["role"] == "location_cue"]
        required = [dict(r) for r in rows if r["role"] in {"setting", "required"}]
        setting = next((r for r in required if r["role"] == "setting"), None)
        if setting and len(cues) >= 2 and setting["status"] != "present":
            if all(r["status"] == "present" for r in cues):
                setting["status"] = "present"
                report["setting_evidence"] = "independent_observable_cues"
            elif any(r["status"] != "absent" for r in cues):
                setting["status"] = "uncertain"
        report["confirmed"] = [r["element"] for r in required if r["status"] == "present"]
        report["absent"] = [r["element"] for r in required if r["status"] == "absent"]
        report["uncertain"] = [r["element"] for r in [*required, *(p for p in rows if p["role"] == "forbidden")]
                               if r["status"] == "uncertain"]
        report["contradictions"] = [r["element"] for r in rows
                                    if r["role"] == "forbidden" and r["status"] == "present"]
        report["must_have_coverage"] = {"confirmed": len(report["confirmed"]), "required": len(required)}
        report["scene_match"] = ("mismatch" if report["absent"] or report["contradictions"] else
                                 "uncertain" if report["uncertain"] else "matched")
        reasons = report["reasons"]
        if report["absent"]:
            reasons.append("required_place_or_element_absent")
        if report["contradictions"]:
            reasons.append("contradiction_present")
        if report["uncertain"]:
            reasons.append("visual_evidence_uncertain")
        if duplicate:
            reasons.append("image_duplicate")
        if clip_gate and not clip["passed"]:
            report["clip"]["decision_weight"] = "advisory_low_similarity"
        if quality and quality.get("passed") is False:
            reasons.append("image_quality_failed")
        if any(r != "visual_evidence_uncertain" for r in reasons):
            report["status"] = "rejected"
        elif reasons:
            report["status"] = "needs_review"
        else:
            report.update(status="accepted", reasons=["scene_elements_verified"])
    report["elapsed_seconds"] = round(monotonic() - started, 2)
    return report


def current_report(path, card, sha):
    row = read_json(path)
    if (row and row.get("schema") == SCHEMA and row.get("scene_card_sha256") == digest(json_bytes(card))
            and row.get("image_sha256") == row.get("pixel_input_sha256") == sha):
        return row
    return None


def candidate_report_path(path, sha, card_sha):
    return path.parent / "image-qa" / path.stem / f"{sha}-{card_sha}.json"


def report_candidate(path, row):
    card_sha = row.get("scene_card_sha256") or digest(json_bytes(row["scene_card"]))
    write_json(candidate_report_path(path, row["image_sha256"], card_sha), row)


def allocation(memory, article):
    with memory.lock:
        calls = [row for row in memory.data["calls"] if row["provider"] == "klein"]
        daily = memory.config["daily_calls"]["klein"] - len(calls)
        per_article = memory.config["per_article_calls"]["klein"] - sum(
            row["article"] == article for row in calls)
    return max(0, min(daily, per_article))


def apply_channel_policy(report, channel, metadata, *, film=None, spoilers=False):
    from urllib.parse import urlsplit
    from .editorial_cinema import spoiler_risk

    card = report["scene_card"]
    kind = metadata.get("kind")
    url = metadata.get("source_page") or ""
    source_valid = urlsplit(url).scheme == "https" and bool(urlsplit(url).hostname)
    checks = {"channel": channel, "source": url or None, "rights_status": metadata.get("rights_status")}
    issue = None
    if channel == "science":
        if kind == "photo":
            license_url = urlsplit(metadata.get("license_url") or "")
            checks["photo_provenance"] = (
                source_valid and metadata.get("rights_status") == "license_metadata_verified"
                and license_url.hostname == "creativecommons.org"
                and license_url.scheme in ("http", "https")
                and bool(metadata.get("attribution") or metadata.get("artist") or metadata.get("creator"))
            )
            if not checks["photo_provenance"]:
                issue = "scientific_photo_source_or_license_unverified"
        elif kind == "illustration":
            checks["illustration"] = "generated"
            checks["scientific_visual_check"] = report["must_have_coverage"]
        else:
            issue = "science_image_medium_unknown"
    elif channel == "cinema":
        story = (metadata.get("scene") or "") + " " + card["visual_description"]
        identity = (
            film and source_valid and (
                (metadata.get("original_title") == film.get("original_title")
                 and metadata.get("year") == film.get("year") and metadata.get("film_id") == film.get("qid") and
                 bool(film.get("qid")))
                or (bool(film.get("imdb_id") or film.get("imdb"))
                    and urlsplit(url).hostname in ("www.imdb.com", "imdb.com")
                    and f"/title/{film.get('imdb_id') or film.get('imdb')}/mediaviewer/" in urlsplit(url).path)
            )
        )
        checks["film_provenance"] = bool(identity)
        checks["spoiler_description_flag"] = not spoilers and spoiler_risk({"brief": story})
        checks["spoiler_review"] = ("allowed_by_article" if spoilers else
                                    "verified" if metadata.get("spoiler_safe_verified") is True else "unconfirmed")
        if checks["spoiler_description_flag"]:
            if report["status"] != "provider_unavailable":
                report["status"] = "rejected"
            report["reasons"].append("cinema_spoiler_detected")
        elif not identity:
            issue = "cinema_film_identity_unverified"
        elif checks["spoiler_review"] == "unconfirmed":
            issue = "cinema_spoiler_safety_unconfirmed"
    elif channel != "legends":
        issue = "image_channel_policy_unknown"
    if issue:
        report["reasons"].append(issue)
        if report["status"] == "accepted":
            report["status"] = "needs_review"
    report["policy"] = checks



def accepted_for(row, card, *, is_cover):
    report = row.get("image_qa") or {}
    return bool(card and report.get("schema") == SCHEMA and report.get("status") == "accepted"
                and report.get("scene_card_sha256") == digest(json_bytes(card))
                and report.get("image_sha256") == report.get("pixel_input_sha256") == row.get("sha256")
                and row.get("is_cover") is is_cover
                and type(is_cover) is bool)


def verified_model_evidence(report):
    """Check that the saved model output supports the accepted scene."""
    from .editorial_visual_matching import CLIP

    card = report.get("scene_card")
    clip = report.get("clip") or {}
    vision = report.get("vision") or {}
    blip = (vision.get("models") or {}).get("blip") or {}
    if (not card or not report.get("image_sha256") or report.get("pixel_input_sha256") != report["image_sha256"]
            or clip.get("model") != "@".join(CLIP) or type(clip.get("passed")) is not bool
            or blip.get("model") != list(MODELS["blip"])):
        return False
    answers = blip.get("answers")
    observations = blip.get("observations") or {}
    if (not isinstance(answers, list) or not all(isinstance(answer, str) for answer in answers)
            or not all(isinstance(observations.get(key), str)
                       for key in ("place", "objects_and_people", "action"))):
        return False
    checked = judge(
        card, None, report["image_sha256"], dict(clip), quality=report.get("visual_quality"),
        vision=lambda card, image: inspect_pixels(
            card, image, ask=lambda *_: [*answers, observations["place"],
                                         observations["objects_and_people"], observations["action"]]),
    )
    return (checked["status"] == "accepted" and checked["vision"]["findings"] == vision.get("findings")
            and report.get("scene_match") == "matched" and not report.get("duplicate"))


def require_packet_rows(rows, source_sha, *, article_id=None, article=None):
    """Defend the packaging and delivery boundaries independently of generation."""
    for row in rows:
        evidence = row.get("image_qa") or {}
        card = evidence.get("scene_card")
        if card and (card.get("source_text_sha256") != source_sha or
                     evidence.get("source_text_sha256") != source_sha):
            raise Blocked("stale_image_needs_recheck")
        if article is not None and card:
            number = row.get("section")
            sections = article["sections"]
            if (type(number) is not int or not 1 <= number <= len(sections)
                    or card.get("section_text_sha256") != digest(section_text(sections[number - 1]))):
                raise Blocked("stale_image_needs_recheck")
        if (not accepted_for(row, card, is_cover=row.get("is_cover"))
                or evidence.get("source_text_sha256") != source_sha
                or card["source_text_sha256"] != source_sha
                or card["section_id"] != row.get("section")
                or (article_id is not None and card.get("article_id") != article_id)
                or not verified_model_evidence(evidence)
                or not evidence.get("policy") or evidence.get("visual_quality", {}).get("passed") is not True):
            raise Blocked("image_review_required_before_packaging")



def require_ready_rows(rows):
    sections = sorted(row["section"] for row in rows)
    if (not 7 <= len(rows) <= 8 or sections != [1, *range(1, len(rows))]
            or len({row["sha256"] for row in rows}) != len(rows)
            or sum(row["is_cover"] and row["section"] == 1 for row in rows) != 1
            or sum(row["is_cover"] for row in rows) != 1):
        raise Blocked("review_visual_layout_incomplete")



def corrections(card, row):
    details = []
    if row["absent"]:
        details.append("Clearly show these missing items: " + ", ".join(row["absent"]))
    if card["location"] in row["absent"]:
        details.append("Use a wide establishing view that includes the full setting and the required objects "
                       "in the same frame. Avoid a close-up that hides the surrounding location.")
    if row["contradictions"]:
        details.append("Exclude these conflicting items: " + ", ".join(row["contradictions"]))
    if row["duplicate"]:
        details.append("Use a distinct viewpoint and composition from other images.")
    if not row["clip"]["passed"]:
        details.append("Frame the setting and main subject prominently.")
    return details
