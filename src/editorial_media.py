"""Acquire real Commons photographs or request original scenes from a local SD WebUI."""
import argparse
import base64
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps
from editorial_local_llm import local_endpoint
from editorial_revision import save_case
from editorial_writer import private_path


def fetch(url, limit):
    req = Request(url, headers={"User-Agent": "ZenEditorial/1.0 (github.com/kaokshi/kaokshi.github.io)"})
    with urlopen(req, timeout=60) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Media response exceeds size limit")
    return data


def commons_photo(pageid):
    params = {"action": "query", "pageids": int(pageid), "prop": "imageinfo",
              "iiprop": "url|extmetadata|size", "format": "json"}
    query = "https://commons.wikimedia.org/w/api.php?" + urlencode(params)
    page = json.loads(fetch(query, 1_000_000))["query"]["pages"][str(pageid)]
    info = page["imageinfo"][0]
    meta = {key: unescape(re.sub("<[^>]+>", "", str(obj["value"]))).strip()
            for key, obj in info["extmetadata"].items()}
    license_url = meta.get("LicenseUrl", "").replace("http://", "https://").rstrip("/")
    if not re.fullmatch(r"https://creativecommons\.org/(licenses/by(-sa)?/[234]\.0|publicdomain/zero/1\.0)", license_url):
        raise ValueError("Commons photo lacks an accepted reusable license")
    if not meta.get("Artist") or urlsplit(info["url"]).hostname != "upload.wikimedia.org":
        raise ValueError("Missing photographer or unsupported file host")
    data = fetch(info["url"], 45_000_000)
    return data, {"source": info["descriptionurl"], "artist": meta["Artist"],
                  "license": meta["LicenseShortName"], "license_url": license_url,
                  "original_sha256": sha256(data).hexdigest()}


def save_image(root, channel, identifier, data, credit, caption):
    if channel not in {"science", "legends"} or not re.fullmatch(r"[a-z0-9-]{1,80}", identifier):
        raise ValueError("Unsupported channel or illustration identifier")
    with Image.open(BytesIO(data)) as source:
        img = ImageOps.exif_transpose(source).convert("RGB")
    img.thumbnail((1800, 1800))
    stream = BytesIO()
    img.save(stream, "JPEG", quality=91)
    encoded = stream.getvalue()
    digest = sha256(encoded).hexdigest()
    name = f"content/assets/{channel}/{identifier}-{digest[:16]}.jpg"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return {**credit, "id": identifier, "file": name, "caption": caption,
            "sha256": digest, "rights_verified": True, "adaptation": "JPEG; масштабирование"}


def plan_images(article, provider, *, query=None, rights_basis=None):
    from editorial_length import extract_text
    inputs = {"title": article["title"], "text": extract_text(article["body_html"])}
    if article["channel"] == "legends":
        if not rights_basis or not rights_basis.strip():
            raise ValueError("Supply the rights basis for this local generator")
        task = "scene_plan"
        inputs["instruction"] = ("Выбери семь разных конкретных сцен из рассказа. Для каждой дай "
                                 "caption на русском и самостоятельный prompt для генерации оригинальной "
                                 "иллюстрации. Сохрани героев, место и время; избегай надписей и спойлеров.")
    else:
        if not query or not query.strip():
            raise ValueError("Supply a Commons photographic search query")
        params = {"action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
                  "gsrlimit": 50, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "format": "json"}
        data = json.loads(fetch("https://commons.wikimedia.org/w/api.php?" + urlencode(params), 4_000_000))
        candidates = {}
        for page in data.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo", [{}])[0]
            meta = info.get("extmetadata", {})
            if (min(info.get("width", 0), info.get("height", 0)) < 600
                    or not re.search(r"\.(jpe?g|png|webp)$", page["title"], re.I)
                    or not meta.get("LicenseUrl")):
                continue
            candidates[page["pageid"]] = {"name": page["title"], "description":
                unescape(re.sub("<[^>]+>", "", str(meta.get("ImageDescription", {}).get("value", ""))))[:400]}
        if len(candidates) < 7:
            raise ValueError("Insufficient photographic candidates from Commons")
        task = "photo_plan"
        inputs.update(candidates=candidates, instruction="Выбери семь различных документальных фотографий, "
                      "соответствующих теме, по описаниям; приведи pageid и точную подпись на русском. "
                      "Исключи схемы, рисунки и изображения с недостоверным контекстом.")
    rows = provider(task, inputs)["images"]
    if len(rows) not in (7, 8):
        raise ValueError("Model did not select seven or eight images")
    if task == "photo_plan" and (len({r["pageid"] for r in rows}) != len(rows)
                                or any(r["pageid"] not in candidates for r in rows)):
        raise ValueError("Unknown or repeated Commons photograph")
    return [{**r, "id": f"image-{n}", "rights_basis": rights_basis} for n, r in enumerate(rows, 1)]


def produce(channel, plan, root, endpoint):
    if len(plan) not in (7, 8) or len({row["id"] for row in plan}) != len(plan):
        raise ValueError("Plan requires seven or eight unique scenes")
    rows = []
    for row in plan:
        if channel == "science":
            data, credit = commons_photo(row["pageid"])
        else:
            # SD WebUI returns base64; send only to an explicitly configured loopback service.
            payload = {"prompt": row["prompt"], "width": 1024, "height": 768, "steps": 25,
                       "negative_prompt": "watermark, caption, text, collage", "batch_size": 1}
            req = Request(local_endpoint(endpoint).geturl().rstrip("/") + "/sdapi/v1/txt2img",
                          data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=900) as response:
                raw = response.read(32_000_001)
            if len(raw) > 32_000_000:
                raise ValueError("Generator response too large")
            result = json.loads(raw)
            data = base64.b64decode(result["images"][0], validate=True)
            credit = {"source": "Local Stable Diffusion WebUI", "artist": "ИИ-иллюстрация",
                      "generation": json.loads(result["info"]), "prompt": row["prompt"],
                      "license": row["rights_basis"]}
            if not credit["license"].strip():
                raise ValueError("Supply rights basis for the deployed model and its output")
        rows.append(save_image(root, channel, row["id"], data, credit, row["caption"]))
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("science", "legends"), required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--generator", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    root = private_path(args.root)
    manifest = produce(args.channel, json.loads(args.plan.read_text()), root, args.generator)
    save_case(root / (args.channel + "-manifest.json"), manifest)
