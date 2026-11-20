"""Compare a complete rights-cleared image set and assemble a private article."""
import argparse
import base64
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

from editorial_layout import SECTION_SCHEMA, parse_sections
from editorial_local_llm import INTEGER, STRING, local_endpoint, matches_schema, obj, read_json_response, request_timeout

CRITERIA = ("topic", "composition", "lighting", "sharpness", "detail", "appeal", "thumbnail", "crop", "genre")
GENRES = {"legends": "Атмосфера основной загадки без спойлера.",
          "science": "Достоверная фотография явления или научная визуализация.",
          "cinema": "Выразительный кадр именно из указанного фильма."}
ASSESSMENT = obj(**dict.fromkeys(CRITERIA, INTEGER), defect=STRING, reason=STRING)


def identity(article):
    body = parse_sections(article["body_html"])
    remove_figures(body)
    return sha256(json.dumps([article["channel"], article["title"], ET.tostring(body, encoding="unicode")],
                             ensure_ascii=False).encode()).hexdigest()


def remove_figures(root):
    for section in root:
        section.tail = None
        for figure in section.findall("figure"):
            if figure.get("data-editorial-image") is None:
                raise ValueError("Untracked illustration in body")
            section.remove(figure)
        if list(section.iter("img")):
            raise ValueError("Untracked inline image")


def asset(root, name):
    path = (root / name).resolve()
    if (not name.startswith("content/assets/") or ".." in Path(name).parts
            or not path.is_relative_to((root / "content/assets").resolve())):
        raise ValueError("Image must be under content/assets")
    return path


def vision(article, image):
    from PIL import ImageOps
    model = os.environ.get("ZEN_VISION_MODEL")
    if not model:
        raise ValueError("Configure a local Ollama vision model for cover_selection")
    endpoint = local_endpoint(os.environ.get("ZEN_VISION_URL", "http://127.0.0.1:11434"))
    images = []
    for view in (image.copy(), ImageOps.fit(image, (192, 108))):
        view.thumbnail((1024, 1024))
        buf = BytesIO()
        view.save(buf, "JPEG")
        images.append(base64.b64encode(buf.getvalue()).decode())
    prompt = ("Оцени одну иллюстрацию и её уменьшенный кадр 16:9 по каждому критерию от 0 до 5. "
              "В defect укажи водяной знак, случайный текст, дефект, вводящую в заблуждение деталь "
              "или спойлер. Если препятствий нет, оставь пустую строку. В reason обоснуй выбор "
              "по видимому содержанию. " + GENRES[article["channel"]] + " Материал: " +
              article["title"] + "\n" + " ".join(parse_sections(article["body_html"]).itertext()))
    reply = read_json_response(endpoint, "/api/chat", {"model": model, "format": ASSESSMENT,
        "stream": False, "messages": [{"role": "user", "content": prompt, "images": images}]}, request_timeout())
    return json.loads(reply["message"]["content"])


def cover_selection(article, manifest, root, assess=vision, *, checkpoint=None):
    from PIL import Image, ImageOps
    body = parse_sections(article["body_html"])
    remove_figures(body)
    if not isinstance(manifest, list) or len(manifest) not in (7, 8):
        raise ValueError("Compare a complete set of seven or eight illustrations")
    records = []
    for i, row in enumerate(manifest):
        name, ident, source = (row[key] for key in ("file", "id", "source"))
        if (not all(isinstance(v, str) and v.strip() for v in (ident, source, name))
                or row.get("rights_verified") is not True):
            raise ValueError("Missing illustration identity, provenance or rights clearance")
        path = asset(root, name)
        with path.open("rb") as stream:
            digest = sha256(stream.read()).hexdigest()
            stream.seek(0)
            with Image.open(stream) as original:
                if original.format != "JPEG":
                    raise ValueError("The current RSS enclosure requires a JPEG asset")
                image = ImageOps.exif_transpose(original).convert("RGB")
        if digest != row["sha256"]:
            raise ValueError("Illustration changed since rights verification")
        if any(r["id"] == ident or r["sha256"] == digest for r in records):
            raise ValueError("Duplicate illustration")
        rating = assess(article, image)
        if (not matches_schema(rating, ASSESSMENT) or not rating["reason"].strip()
                or any(not 0 <= rating[k] <= 5 for k in CRITERIA)):
            raise ValueError("Invalid visual assessment")
        eligible = (min(image.size) >= 600 and not rating["defect"].strip()
                    and min(rating[k] for k in ("topic", "genre", "thumbnail", "crop", "sharpness")) >= 4)
        records.append({"id": ident, "file": name, "source": source, "rights_verified": True,
                        "original_position": i, "sha256": digest, "assessment": rating,
                        "eligible": eligible, "score": sum(rating[k] for k in CRITERIA)})
    candidates = [r for r in records if r["eligible"]]
    if checkpoint:
        checkpoint({"status": "assessed", "article_sha256": identity(article), "images": records})
    if not candidates:
        raise ValueError("No eligible cover in this set")
    cover = max(candidates, key=lambda r: (r["score"], r["assessment"]["topic"], r["id"]))
    ordered = [cover, *(r for r in records if r is not cover)]
    for i, row in enumerate(ordered):
        row.update(position=i, is_cover=(i == 0))
        if i == 0:
            row["cover_selection_reason"] = row["assessment"]["reason"]
        figure = ET.Element("figure", {"data-editorial-image": row["id"]})
        ET.SubElement(figure, "img", {"src": "/" + row["file"].removeprefix("content/"),
                                      "alt": row["assessment"]["reason"]})
        section = body[i * len(body) // len(ordered)]
        section.append(figure)
    result = {**article, "layout_schema": SECTION_SCHEMA, "body_html": "\n".join(ET.tostring(s, encoding="unicode") for s in body),
              "image_files": [r["file"] for r in ordered], "cover_selection": {
                  "article_sha256": identity(article), "images": ordered}}
    verify_cover(result, root)
    if checkpoint:
        checkpoint({"status": "selected", **result["cover_selection"]})
    return result


def verify_cover(article, root):
    stage = article["cover_selection"]
    rows = stage["images"]
    if (len(rows) not in (7, 8) or stage["article_sha256"] != identity(article)
            or [r["file"] for r in rows] != article["image_files"]
            or len({r["sha256"] for r in rows}) != len(rows)):
        raise ValueError("Stale or incomplete cover selection")
    body = parse_sections(article["body_html"])
    figures = list(body.iter("figure"))
    if len(figures) != len(rows):
        raise ValueError("Cover and article illustrations differ")
    candidates = [r for r in rows if r["eligible"]]
    if not candidates or rows[0] != max(candidates, key=lambda r: (r["score"], r["assessment"]["topic"], r["id"])):
        raise ValueError("First image must be the highest ranked eligible candidate")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate illustration identifier")
    for i, (row, figure) in enumerate(zip(rows, figures)):
        if (row["position"] != i or row["is_cover"] is not (i == 0)
                or figure.get("data-editorial-image") != row["id"]
                or row.get("rights_verified") is not True or not row.get("source")
                or len(figure) != 1 or figure[0].tag != "img"
                or figure[0].get("src") != "/" + row["file"].removeprefix("content/")
                or sha256(asset(root, row["file"]).read_bytes()).hexdigest() != row["sha256"]):
            raise ValueError("Image changed; rerun cover_selection")
    if not rows[0]["eligible"] or not rows[0].get("cover_selection_reason"):
        raise ValueError("Missing cover justification")


if __name__ == "__main__":
    from editorial_revision import save_case
    from editorial_writer import private_path
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("article", "manifest", "asset-root", "output"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    assembled = cover_selection(json.loads(args.article.read_text()), json.loads(args.manifest.read_text()), args.asset_root)
    save_case(private_path(args.output), assembled)
