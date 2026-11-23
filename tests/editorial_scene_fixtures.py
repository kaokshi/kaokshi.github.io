"""Synthetic visual evidence used only to test storage and document boundaries."""
from random import Random

from PIL import Image

from src import editorial_scene_qa as qa
from src.editorial_visual_matching import CLIP


def distinct_picture(seed):
    rng = Random(seed)
    image = Image.frombytes("RGB", (16, 16), rng.randbytes(16 * 16 * 3))
    return image.resize((640, 480), Image.Resampling.NEAREST)


def proposal(evidence):
    return {"scene_type": "object", "location": "a synthetic setting", "time_of_day": "",
            "characters": [], "must_have": ["a synthetic object"], "must_not_have": [],
            "optional_details": [], "visual_description": "Synthetic visual test fixture.",
            "evidence_spans": {"a synthetic setting": evidence, "a synthetic object": evidence}}


def certify(row, article_id, is_cover, text_sha, section_text="Synthetic section"):
    card = row.pop("fixture_card", None) or qa.make_card(
        article_id, row["section"], text_sha, section_text, proposal(section_text[:25]))
    def fixture_answers(_, image, questions):
        roles, _ = qa.questions_for(card)
        votes = ["no" if role == "forbidden" else "yes" for role, _ in roles]
        return [answer for vote in votes for answer in (vote, vote)] + [
            "Synthetic setting", "Synthetic object", "Synthetic action"]

    result = qa.judge(card, object(), row["sha256"], {"model": "@".join(CLIP), "passed": True, "topic": .25},
                      quality={"passed": True}, vision=lambda c, im: qa.inspect_pixels(c, im, ask=fixture_answers))
    result["policy"] = {"fixture": True}
    row.update(image_qa=result, is_cover=is_cover)
    return row
