"""Fail-closed, versioned scene contracts for controlled FLUX.2 Klein edits.

A contract states what is immutable, what one defect to fix, and what to forbid.
No LLM verdict is assumed true merely because a prompt was generated.
"""
from hashlib import sha256
import json

SCHEMA = "zen-scene-contract-v1"
FIELDS = ("subject", "location", "action", "must_keep", "fix", "forbid", "style")


def make_contract(scene, *, subject=None, location=None, action=None, must_keep=(),
                  fix=None, forbid=(), style="zen-legends-visual-profile-v1"):
    if not isinstance(scene, str) or not 1 <= len(scene.strip()) <= 1100:
        raise ValueError("Scene must be specific and no longer than 1100 characters")
    contract = {
        "schema": SCHEMA, "scene": scene.strip(),
        "subject": subject or "", "location": location or "", "action": action or "",
        "must_keep": list(must_keep),
        "fix": fix or "",
        "forbid": list(forbid),
        "style": style,
    }
    validate_contract(contract)
    return contract


def validate_contract(c):
    if not isinstance(c, dict) or c.get("schema") != SCHEMA:
        raise ValueError("Unknown scene contract")
    if not isinstance(c.get("scene"), str) or not 1 <= len(c["scene"]) <= 1100:
        raise ValueError("Missing scene")
    for k in ("subject", "location", "action", "fix", "style"):
        if not isinstance(c.get(k), str) or len(c[k]) > 700:
            raise ValueError("Invalid scene field: " + k)
    if c["style"] != "zen-legends-visual-profile-v1":
        raise ValueError("Visual style cannot be changed by the editor")
    for k in ("must_keep", "forbid"):
        if (not isinstance(c.get(k), list) or len(c[k]) > 12
                or any(not isinstance(x, str) or not x.strip() or len(x) > 250 for x in c[k])):
            raise ValueError("Invalid list: " + k)
    return c


def fingerprint(contract):
    validate_contract(contract)
    return sha256(json.dumps(contract,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def controlled_edit_prompt(contract):
    from editorial_visual_style import visual_profile
    validate_contract(contract)
    p = visual_profile()
    keep = [contract["subject"], contract["location"], contract["action"], *contract["must_keep"]]
    keep = [x for x in keep if x]
    fix = contract["fix"].strip()
    # An empty fix is a format-preserving edit only; never invite re-imagination.
    fix_request = fix or "Extend only the image borders to the requested landscape framing, without redesigning the interior."
    prompt = (
        "TARGETED EDIT OF INPUT IMAGE 0. This is NOT a new scene generation. "
        "PRESERVE EXACTLY the identity and arrangement of the foreground subject, "
        "background location, story action, camera position, viewpoint, clothing, "
        "time of day, existing light sources and artistic style. "
        "IMMUTABLE STORY SCENE: " + contract["scene"] + ". "
        "MUST KEEP: " + "; ".join(keep or ["Everything in the reference scene"]) + ". "
        "CHANGE ONLY: " + fix_request + ". "
        "STRICTLY FORBIDDEN: " + "; ".join(contract["forbid"] + [
            "changing the story, replacing the location, changing clothing or faces",
            "adding new people, random props, furniture or doorways",
            "new colour grade, new art style, glossy painterly surfaces, text, watermark"
        ]) + ". "
        "STYLE: retain the SAME photographic appearance as input image 0, "
        "with natural colour, material texture and lighting; do NOT beautify or reimagine. "
        "OUTPUT: wide 16:9 framing with the essential subjects kept visible. "
    )
    if len(prompt) > 3500:
        raise ValueError("Scene contract would exceed Klein input budget")
    return prompt


def structural_verdict(original_data, refined_data, *, expected_size=(1280,720)):
    """Checks only technical integrity, not whether scene meaning survived."""
    from io import BytesIO
    from PIL import Image, ImageStat
    with Image.open(BytesIO(original_data)) as original, Image.open(BytesIO(refined_data)) as final:
        original.load()
        final.load()
        if final.size != expected_size:
            return {"status":"reject", "reason":"wrong_dimensions", "semantic_verified":False}
        if final.mode not in ("RGB","RGBA"):
            return {"status":"reject", "reason":"invalid_mode", "semantic_verified":False}
        if max(ImageStat.Stat(final.convert("L")).stddev) < 4:
            return {"status":"reject", "reason":"flat_or_blank_image", "semantic_verified":False}
        return {"status":"needs_visual_review", "reason":"structural_checks_passed",
                "semantic_verified":False,
                "warning":"Visual content, character identity and scene semantics NOT verified"}


def select_reviewed_variant(original, edited, evidence):
    """Use an edit ONLY when a human/independent approved review matches exact bytes.

    A rejection safely returns the intact original. No synthetic QA evidence.
    """
    if not isinstance(evidence, dict):
        raise ValueError("Review evidence must be provided")
    if evidence.get("edited_sha256") != sha256(edited).hexdigest():
        raise ValueError("Review evidence is stale or for a different candidate")
    if evidence.get("original_sha256") != sha256(original).hexdigest():
        raise ValueError("Review source mismatch")
    if evidence.get("reviewer_kind") not in ("human", "independent_validated_vision"):
        raise ValueError("A real qualified reviewer is required")
    if evidence.get("decision") == "reject":
        return original, {"selection": "original", "reviewed": True}
    if evidence.get("decision") != "approve":
        raise ValueError("Review is not final")
    gates = ("same_story", "same_character", "same_location",
             "same_visual_style", "targeted_fix_successful", "no_new_defects")
    if any(evidence.get(field) is not True for field in gates):
        return original, {"selection": "original", "reviewed": True,
                          "reason": "failed_visual_gate"}
    return edited, {"selection": "refined", "reviewed": True}
