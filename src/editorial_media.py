"""Acquire real Commons photographs or request original scenes from a local SD WebUI."""
import argparse
import base64
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
import os
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
    return data, {"source": info["descriptionurl"], "original_url": info["url"], "artist": meta["Artist"],
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


def plan_images(article, provider, *, query=None, rights_basis=None, exclude=()):
    from editorial_length import extract_text
    from editorial_layout import parse_sections
    sections = parse_sections(article["body_html"])
    inputs = {"title": article["title"], "text": extract_text(article["body_html"]),
              "section_headings": [section[0][0].text for section in sections]}
    if article["channel"] == "legends":
        if not rights_basis or not rights_basis.strip():
            raise ValueError("Supply the rights basis for this local generator")
        task = "scene_plan"
        inputs["instruction"] = ("Выбери семь разных конкретных сцен из рассказа. Для каждой дай "
                                 "caption на русском и самостоятельный prompt для генерации оригинальной "
                                 "иллюстрации. Сохрани героев, место и время; избегай надписей и спойлеров.")
    else:
        if not query or not query.strip():
            query = provider("photo_query", {**inputs, "instruction": "Give a concise English Wikimedia Commons search query for documentary photographs of this subject."})["query"]
            if not query.strip():
                raise ValueError("Empty Commons query")
        params = {"action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
                  "gsrlimit": 50, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "format": "json"}
        data = json.loads(fetch("https://commons.wikimedia.org/w/api.php?" + urlencode(params), 4_000_000))
        candidates = {}
        for page in data.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo", [{}])[0]
            meta = info.get("extmetadata", {})
            if (page["pageid"] in exclude or min(info.get("width", 0), info.get("height", 0)) < 600
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
    inputs["instruction"] += " Укажи для каждого изображения номер подходящего смыслового раздела section, начиная с 1."
    rows = provider(task, inputs)["images"]
    if len(rows) not in (7, 8):
        raise ValueError("Model did not select seven or eight images")
    count = len(sections)
    if any(type(r.get("section")) is not int or not 1 <= r["section"] <= count for r in rows):
        raise ValueError("Each planned image needs a valid article section")
    if task == "photo_plan" and (len({r["pageid"] for r in rows}) != len(rows)
                                or any(r["pageid"] not in candidates for r in rows)):
        raise ValueError("Unknown or repeated Commons photograph")
    return [{**r, "id": f"image-{n}", "rights_basis": rights_basis} for n, r in enumerate(rows, 1)]


def generation_settings():
    """Fixed per run; a CPU deployment can reduce the workload for its validated model."""
    width = int(os.environ.get("ZEN_SD_WIDTH", 1024))
    height = int(os.environ.get("ZEN_SD_HEIGHT", 768))
    steps = int(os.environ.get("ZEN_SD_STEPS", 25))
    if (min(width, height) < 600 or max(width, height) > 1024 or width % 64 or height % 64
            or not 1 <= steps <= 25):
        raise ValueError("Choose 64-aligned dimensions between 600 and 1024 and 1 to 25 steps")
    return {"width": width, "height": height, "steps": steps}


def produce(channel, plan, root, endpoint, *, checkpoint=None):
    if len(plan) not in (7, 8) or len({row["id"] for row in plan}) != len(plan):
        raise ValueError("Plan requires seven or eight unique scenes")
    from editorial_cover import asset
    key = sha256(json.dumps([channel, plan, endpoint, generation_settings() if channel == "legends" else None], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    saved = json.loads(checkpoint.read_text()) if checkpoint and checkpoint.exists() else {}
    prior = saved.get("images", {}) if saved.get("plan_sha256") == key else {}
    images = {}
    for row in plan:
        cached = prior.get(row["id"])
        if cached:
            path = asset(root, cached["file"])
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != cached["sha256"]:
                cached = None
        images[row["id"]] = cached or produce_one(channel, row, root, endpoint)
        if checkpoint:
            save_case(checkpoint, {"plan_sha256": key, "images": images})
    return list(images.values())


def produce_one(channel, row, root, endpoint):
    if channel == "science":
        data, credit = commons_photo(row["pageid"])
    else:
        image_provider = os.environ.get("ZEN_IMAGE_PROVIDER", "sd-webui")
        if image_provider == "cloudflare":
            from editorial_cloudflare import generate_scene
            data, credit = generate_scene(row["prompt"])
            credit["license"] = row["rights_basis"]
        elif image_provider == "sd-webui":
            # SD WebUI returns base64; send only to an explicitly configured loopback service.
            payload = {"prompt": row["prompt"], **generation_settings(),
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
        else:
            raise ValueError("Unsupported image provider; Modal is disabled pending billing controls")
        if not credit["license"].strip():
            raise ValueError("Supply rights basis for the deployed model and its output")
    result = save_image(root, channel, row["id"], data, credit, row["caption"])
    if "pageid" in row:
        result["pageid"] = row["pageid"]
    if "section" in row:
        result["section"] = row["section"]
    return result



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


def replace_duplicates(article, plan, manifest, root, registry, endpoint, provider, *, query=None):
    from editorial_quality import image_hash_audit, CONTENT
    from editorial_memory import image_conflicts
    changed = list(manifest)
    excluded = {r["pageid"] for r in plan if "pageid" in r}
    for attempt in range(3):
        temporary = dict(article, image_files=[r["file"] for r in changed], cover_selection={"images":changed})
        audit = image_hash_audit(sorted(CONTENT.glob("*.json")), candidate=temporary, asset_root=root)
        if audit["near_duplicate_status"] != "completed" or audit.get("unreadable"):
            raise ValueError("Incomplete image corpus")
        bad = {r["candidate"] for r in image_conflicts(registry, temporary, root)}
        for pair in audit["duplicates"]:
            if article["slug"] in pair["articles"]:
                bad.add(pair["images"][pair["articles"].index(article["slug"])])
        if not bad:
            return changed
        if attempt == 2:
            raise ValueError("Image replacements still conflict with the shared corpus")
        alternatives = iter(plan_images(article, provider, query=query, exclude=excluded)) if article["channel"]=="science" else None
        for i, row in enumerate(changed):
            if row["file"] in bad:
                spec = next(alternatives) if alternatives is not None else plan[i]
                if "pageid" in spec:
                    excluded.add(spec["pageid"])
                changed[i] = produce_one(article["channel"], dict(spec,id=plan[i]["id"]), root, endpoint)

def replace_unsuitable(article, plan, images, rejected, root, registry, endpoint, provider, query):
    """Generate new scene bytes or acquire new Commons IDs, then repeat the corpus audit."""
    excluded = {row["pageid"] for row in [*plan, *images] if "pageid" in row}
    alternatives = iter(plan_images(article, provider, query=query, exclude=excluded)) if article["channel"] == "science" else None
    by_id = {row["id"]: row for row in plan}
    result = []
    for current in images:
        ident = current["id"]
        if ident in rejected:
            row = next(alternatives) if alternatives is not None else by_id[ident]
            current = produce_one(article["channel"], dict(row, id=ident), root, endpoint)
        result.append(current)
    return replace_duplicates(article, plan, result, root, registry, endpoint, provider, query=query)
