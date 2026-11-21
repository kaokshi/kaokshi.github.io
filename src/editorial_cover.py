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
    if os.environ.get("ZEN_VISION_BACKEND") == "llama.cpp":
        properties = {k: {"type": "integer", "enum": list(range(6))} for k in CRITERIA}
        schema = obj(**properties, defect={"type": "string", "enum": [
            "none", "watermark", "text", "misleading", "blur", "spoiler", "artifact"]}, reason={**STRING, "maxLength": 210})
        prompt = ("Rate the photograph or scene illustration for the lead image of an online article. The second view is a center crop of the same picture. No printed title or lettering is expected. Judge crop by subject visibility. Legitimate scale bars on microscopy are allowed. Give a score from 0 (poor) to 5 (excellent) "
                  "for each criterion. Identify any disqualifying defect; choose none if absent. "
                  "In one short sentence identify the visible subject, its relevance, and the crop. A mismatch between pixels and caption disqualifies the image. Article: " + article["title"] + ". " +
                  " ".join(s[0][0].text for s in parse_sections(article["body_html"])) +
                  " Caption to verify against the pixels: " + article.get("candidate_caption", ""))
        content = [{"type": "text", "text": prompt}, *[
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + item}} for item in images]]
        response = read_json_response(endpoint, "/v1/chat/completions",
            {"model": model, "temperature": 0, "max_tokens": 360,
             "messages": [{"role": "user", "content": content}],
             "response_format": {"type": "json_schema", "schema": schema}}, request_timeout())
        choice = response["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Incomplete visual assessment at token limit")
        rating = json.loads(choice["message"]["content"])
        if rating["defect"] == "none":
            rating["defect"] = ""
        return rating
    reply = read_json_response(endpoint, "/api/chat", {"model": model, "format": ASSESSMENT,
        "stream": False, "messages": [{"role": "user", "content": prompt, "images": images}]}, request_timeout())
    return json.loads(reply["message"]["content"])


def eligible_cover(rating, size):
    if (not matches_schema(rating, ASSESSMENT) or rating["reason"].strip().casefold() in {"", "none", "null", "n/a"}
            or any(not 0 <= rating[k] <= 5 for k in CRITERIA)):
        raise ValueError("Invalid visual assessment")
    return (min(size) >= 600 and not rating["defect"].strip()
            and min(rating[k] for k in ("topic", "genre", "thumbnail", "crop", "sharpness")) >= 4)


class UnsuitableIllustrations(ValueError):
    def __init__(self, identifiers):
        self.identifiers = identifiers
        super().__init__("Illustrations require replacement: " + ", ".join(identifiers))


def unsuitable(row):
    rating = row["assessment"]
    return bool(rating["defect"].strip()) or min(rating["topic"], rating["genre"]) == 0


def cover_selection(article, manifest, root, assess=vision, *, checkpoint=None, review=None):
    from PIL import Image, ImageOps
    body = parse_sections(article["body_html"])
    remove_figures(body)
    if not isinstance(manifest, list) or len(manifest) not in (7, 8):
        raise ValueError("Compare a complete set of seven or eight illustrations")
    if review is not None and (review.get("article_sha256") != identity(article)
            or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
            or set(review.get("scores", {})) != {row["sha256"] for row in manifest}):
        raise ValueError("Cover review must identify the reviewer and the exact article and images")
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
        rating = assess(dict(article, candidate_caption=row.get("caption", "")), image) if review is None else review["scores"][digest]
        eligible = eligible_cover(rating, image.size)
        metadata = {k: row[k] for k in ("artist", "license", "license_url", "adaptation", "caption", "original_sha256", "section")
                    if row.get(k)}
        records.append({**metadata, "id": ident, "file": name, "source": source, "rights_verified": True,
                        "original_position": i, "sha256": digest, "assessment": rating,
                        "size": list(image.size), "eligible": eligible, "score": sum(rating[k] for k in CRITERIA)})
    candidates = [r for r in records if r["eligible"]]
    if checkpoint:
        checkpoint({"status": "assessed", "article_sha256": identity(article), "images": records})
    rejected = [r["id"] for r in records if unsuitable(r)]
    if rejected:
        raise UnsuitableIllustrations(rejected)
    if not candidates:
        raise ValueError("No eligible cover in this set")
    cover = max(candidates, key=lambda r: (r["score"], r["assessment"]["topic"], r["id"]))
    ordered = [cover, *(r for r in records if r is not cover)]
    if any(r.get("section") is not None for r in ordered):
        if any(type(r.get("section")) is not int or not 1 <= r["section"] <= len(body) for r in ordered):
            raise ValueError("Illustrations need a consistent section plan")
        ordered = [cover, *sorted(ordered[1:], key=lambda r:r["section"])]
    for i, row in enumerate(ordered):
        row.update(position=i, is_cover=(i == 0))
        if i == 0:
            row["cover_selection_reason"] = row["assessment"]["reason"]
        figure = ET.Element("figure", {"data-editorial-image": row["id"]})
        ET.SubElement(figure, "img", {"src": "/" + row["file"].removeprefix("content/"),
                                      "alt": row["assessment"]["reason"]})
        if row.get("caption"):
            credit = " · ".join(row[k] for k in ("artist", "license", "adaptation") if row.get(k))
            caption = ET.SubElement(figure, "figcaption")
            caption.text = row["caption"] + (" — " + credit if credit else "")
            for key, label in (("source", "Источник"), ("license_url", "Лицензия")):
                if row.get(key, "").startswith("https://"):
                    link = ET.SubElement(caption, "a", {"href": row[key]})
                    link.text = " " + label
        index = row.get("section", 1 + i * len(body) // len(ordered)) - 1
        section = body[0 if i == 0 else index]
        section.append(figure)
    result = {**article, "layout_schema": SECTION_SCHEMA, "body_html": "\n".join(ET.tostring(s, encoding="unicode") for s in body),
              "image_files": [r["file"] for r in ordered], "cover_selection": {
                  "article_sha256": identity(article), "images": ordered}}
    result["cover_selection"]["origin"] = "editor_review_of_images" if review is not None else "vision"
    if review is not None:
        result["cover_selection"]["reviewer"] = review["reviewer"]
    if all(r.get("artist") and r.get("license") for r in ordered):
        result["rights_basis"] = "Источники, лицензии и редакционные оригиналы указаны для каждого файла."
        result["rights_reviewed"] = True
        result["image_sources"] = [{"file": r["file"], "source_page": r["source"],
                                    "license": r["license"].upper().replace(" ", "-"),
                                    "artist": r["artist"], "license_url": r.get("license_url"),
                                    "license_verified": r["rights_verified"], "sha256": r["sha256"]}
                                   for r in ordered]
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
        if (unsuitable(row) or row["position"] != i or row["is_cover"] is not (i == 0)
                or row["eligible"] is not eligible_cover(row["assessment"], row["size"])
                or row["score"] != sum(row["assessment"][k] for k in CRITERIA)
                or figure.get("data-editorial-image") != row["id"]
                or row.get("rights_verified") is not True or not row.get("source")
                or [node.tag for node in figure] not in (["img"], ["img", "figcaption"])
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
    dest = private_path(args.output)
    if dest in (args.article.resolve(), args.manifest.resolve()):
        parser.error("Choose a separate output for the assembled draft")
    assembled = cover_selection(json.loads(args.article.read_text()), json.loads(args.manifest.read_text()), args.asset_root)
    save_case(dest, assembled)
