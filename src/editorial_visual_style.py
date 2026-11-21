"""Shared visual brief for legends; no style filters or local post-processing.

The model-specific image geometry is handled by the final Klein stage, not by
unsupported width/height fields on Cloudflare FLUX.1 Schnell.
"""
import json
from pathlib import Path

PROFILE_FILE = Path(__file__).resolve().parents[1] / "config/editorial_visual_style.json"


def visual_profile():
    profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    if profile.get("channel") != "legends" or profile.get("schema") != "zen-legends-visual-profile-v1":
        raise ValueError("Unexpected visual style profile")
    return profile


def scene_prompt(scene):
    """Budget-aware: retain scene semantics; never silently truncate it."""
    if not isinstance(scene, str) or not scene.strip():
        raise ValueError("Missing image scene")
    p = visual_profile()
    style = "; ".join(p["visual_identity"][:4])
    restrictions = "; ".join(p["restrictions"][:4])
    suffix = "\nVISUAL IDENTITY: " + style + ".\nCOMPOSITION: " + p["composition"] + "\nAVOID: " + restrictions
    if len(scene.strip()) + len(suffix) > 2048:
        raise ValueError("Scene plus the channel visual identity exceeds the 2048-character model limit")
    return scene.strip() + suffix


def final_edit_prompt(scene):
    p = visual_profile()
    # Deliberately keep one concise subject reference to avoid ignoring the actual image.
    statement = scene.strip()
    if len(statement) > 900:
        raise ValueError("Final edit scene exceeds 900 characters; simplify before generation")
    return ("Edit input image 0 as a faithful, natural live-action film still. "
            + p["editing"] + " VISUAL IDENTITY: " + "; ".join(p["visual_identity"])
            + ". AVOID: " + "; ".join(p["restrictions"])
            + ". REQUIRED STORY ACTION: " + statement)


def expected_size(kind="inline"):
    p = visual_profile()
    if kind not in {"cover", "inline"}:
        raise ValueError("Unknown media purpose")
    width, height = p[kind]["width"], p[kind]["height"]
    if (width, height) != (1280, 720):
        raise ValueError("Review visual profile dimensions before changing")
    return width, height
