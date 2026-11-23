"""Synthetic visual evidence used only to test storage and document boundaries."""
from src import editorial_scene_qa as qa


def proposal(evidence):
    return {"scene_type": "object", "location": "a synthetic setting", "time_of_day": "",
            "characters": [], "must_have": ["a synthetic object"], "must_not_have": [],
            "optional_details": [], "visual_description": "Synthetic visual test fixture.",
            "evidence_spans": {"a synthetic setting": evidence, "a synthetic object": evidence}}


def certify(row, article_id, is_cover, text_sha, section_text="Synthetic section"):
    card = row.pop("fixture_card", None) or qa.make_card(
        article_id, row["section"], text_sha, section_text, proposal(section_text[:25]))
    result = qa.judge(card, object(), row["sha256"], {"passed": True, "topic": .25},
                      quality={"passed": True}, vision=lambda *_: {
                          "findings": [{"role": "required", "element": "fixture", "status": "present"}]})
    result["policy"] = {"fixture": True}
    row.update(image_qa=result, is_cover=is_cover)
    return row
