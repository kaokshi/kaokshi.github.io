"""Build a private catalogue from films linked to complete Wikimedia Commons videos."""
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlsplit

from .editorial_http import Blocked, request
from .editorial_images import decode_candidate
from .editorial_memory import digest

COMMONS = "commons.wikimedia.org"
UPLOAD = "upload.wikimedia.org"
QUERY = "https://query.wikidata.org/sparql"
MAX_VIDEO_BYTES = 160_000_000
MAX_FILMS = 4
QUERY_TEXT = """
SELECT ?film ?video ?release ?title ?ru WHERE {
  ?film wdt:P31 wd:Q11424; wdt:P10 ?video; wdt:P577 ?release; rdfs:label ?title.
  FILTER(LANG(?title) = "en" && YEAR(?release) >= 1900 && YEAR(?release) <= 1928)
  OPTIONAL { ?film rdfs:label ?ru. FILTER(LANG(?ru) = "ru") }
} ORDER BY ?film ?release LIMIT 80
"""


def film_links():
    from urllib.parse import unquote

    raw = request(QUERY + "?" + urlencode({"format": "json", "query": QUERY_TEXT}),
                  ("query.wikidata.org",), limit=1_000_000)
    for row in json.loads(raw)["results"]["bindings"]:
        qid = row["film"]["value"].rsplit("/", 1)[-1]
        name = unquote(row["video"]["value"].rsplit("/", 1)[-1]).replace("_", " ")
        if not re.fullmatch(r"Q[1-9][0-9]*", qid) or not re.search(r"\.(webm|ogv|mp4)$", name, re.I):
            continue
        yield qid, name, {"title": (row.get("ru") or row["title"])["value"],
                          "original_title": row["title"]["value"], "year": int(row["release"]["value"][:4])}


def commons_video(filename):
    query = urlencode({"action": "query", "titles": "File:" + filename, "prop": "imageinfo",
                       "iiprop": "url|size|mime|extmetadata", "formatversion": 2, "format": "json"})
    pages = json.loads(request(f"https://{COMMONS}/w/api.php?{query}", (COMMONS,), limit=1_000_000))["query"]["pages"]
    if re.search(r"(?:excerpt|trailer|fragment|\bpart [1-9]\b)", filename, re.I):
        return None
    item = (pages[0].get("imageinfo") or [{}])[0]
    metadata = item.get("extmetadata", {})
    label = metadata.get("LicenseShortName", {}).get("value", "")
    license_url = metadata.get("LicenseUrl", {}).get("value", "").replace("http://", "https://")
    if label in {"Public domain", "Public Domain"}:
        license_url = f"https://{COMMONS}/wiki/Commons:Licensing#Public_domain"
    elif (label.upper(), license_url) != ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"):
        return None
    if (not item.get("mime", "").startswith(("video/", "application/ogg"))
            or not 0 < item.get("size", 0) <= MAX_VIDEO_BYTES
            or urlsplit(item.get("url", "")).hostname != UPLOAD
            or urlsplit(item.get("descriptionurl", "")).hostname != COMMONS):
        return None
    return {"asset_url": item["url"], "source_page": item["descriptionurl"],
            "license": label, "license_url": license_url}


def video_frames(video, run=subprocess.run):
    from imagehash import phash

    with TemporaryDirectory() as folder:
        source = Path(folder) / "source.video"
        source.write_bytes(video)
        args = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                "-show_entries", "format=duration:stream=width,height", "-of", "json", str(source)]
        probe = run(args, check=True, capture_output=True, timeout=15)
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        duration = float(info["format"]["duration"])
        # Exclude clips and keep decode memory bounded for archive footage.
        if (len(streams) != 1 or not 300 <= duration <= 14400
                or not 300 <= streams[0]["width"] <= 2400 or not 220 <= streams[0]["height"] <= 1600):
            return []
        frames, seen = [], []
        for fraction in range(6, 95, 4):
            timestamp = round(duration * fraction / 100, 2)
            output = run(["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
                          "-threads", "1", "-ss", str(timestamp), "-i", str(source), "-map", "0:v:0",
                          "-frames:v", "1", "-vf", "scale=960:-2:force_original_aspect_ratio=decrease",
                          "-threads", "1", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"],
                         check=True, capture_output=True, timeout=10).stdout
            try:
                image, _ = decode_candidate(output)
            except (ValueError, OSError):
                continue
            signature = phash(image)
            if any(signature - previous <= 6 for previous in seen):
                continue
            seen.append(signature)
            frames.append((timestamp, str(signature), output))
        return frames


def refresh_catalogue(store, *, discover=film_links, lookup=commons_video, extract=video_frames):
    name = "sources/cinema.json"
    raw, rev = store.read(name)
    rows = json.loads(raw) if raw else {}
    added = 0
    # Only fill gaps. Keep earlier sources and published hashes stable between runs.
    if len(rows) >= MAX_FILMS:
        return {"added": 0, "available": len(rows)}
    known = {entry.get("source_page") for entry in rows.values()}
    consumed = set()
    for qid, filename, identity in discover():
        if qid in rows or qid in consumed:
            continue
        consumed.add(qid)
        try:
            origin = lookup(filename)
            if not origin or origin["source_page"] in known:
                continue
            video = request(origin["asset_url"], (UPLOAD,), timeout=55, limit=MAX_VIDEO_BYTES)
            frames = extract(video)
        except (Blocked, ValueError, KeyError, OSError, subprocess.SubprocessError):
            continue
        if len(frames) < 12:
            continue
        stills = []
        for timestamp, signature, data in frames:
            sha = digest(data)
            path = f"sources/cinema/{qid}/{sha}.jpg"
            store.immutable(path, data)
            stills.append({**identity, **origin, "source_type": "commons_film",
                           "scene": f"Frame at {timestamp:.2f} s", "timestamp_seconds": timestamp,
                           "perceptual_hash": signature, "sha256": sha, "private_path": path})
        rows[qid] = {**identity, "source_page": origin["source_page"], "stills": stills}
        known.add(origin["source_page"])
        added += 1
        if len(rows) >= MAX_FILMS:
            break
    if added:
        store.write(name, json.dumps(rows, ensure_ascii=False).encode(), rev)
    return {"added": added, "available": len(rows)}


if __name__ == "__main__":
    from os import environ
    from .editorial_zen_cloud import GitHubStore

    print(json.dumps(refresh_catalogue(GitHubStore(environ["EDITORIAL_PRIVATE_TOKEN"]))))
