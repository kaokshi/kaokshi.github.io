"""Build a private catalogue from films linked to complete Wikimedia Commons videos."""
import json
from datetime import date
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import quote, unquote, urlencode, urlsplit

from .editorial_http import Blocked, request
from .editorial_cinema import (MIN_RELEASE_YEAR, archive_film_identity, archive_source_record, eligible_year,
                               verify_original_year)
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
  FILTER(LANG(?title) = "en" && YEAR(?release) >= MIN_YEAR && YEAR(?release) <= MAX_YEAR)
  OPTIONAL { ?film rdfs:label ?ru. FILTER(LANG(?ru) = "ru") }
} ORDER BY DESC(?release) ?film LIMIT 80
"""


def linked_films(property_id):
    query = QUERY_TEXT.replace("wdt:P10", "wdt:" + property_id)
    query = query.replace("MIN_YEAR", str(MIN_RELEASE_YEAR)).replace("MAX_YEAR", str(date.today().year))
    raw = request(QUERY + "?" + urlencode({"format": "json", "query": query}),
                  ("query.wikidata.org",), limit=1_000_000)
    for row in json.loads(raw)["results"]["bindings"]:
        qid = row["film"]["value"].rsplit("/", 1)[-1]
        if re.fullmatch(r"Q[1-9][0-9]*", qid):
            yield qid, row["video"]["value"], {
                "title": (row.get("ru") or row["title"])["value"],
                "original_title": row["title"]["value"], "year": int(row["release"]["value"][:4]),
            }


def film_links():
    for qid, url, identity in linked_films("P10"):
        name = unquote(url.rsplit("/", 1)[-1]).replace("_", " ")
        if re.search(r"\.(webm|ogv|mp4)$", name, re.I):
            yield qid, name, identity


def internet_archive_links():
    for qid, identifier, identity in linked_films("P724"):
        if re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", identifier):
            yield qid, identifier, identity


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


def internet_archive_video(identifier):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", identifier):
        return None
    raw = request("https://archive.org/metadata/" + identifier, ("archive.org",), limit=2_000_000)
    item = json.loads(raw)
    meta = item.get("metadata", {})
    if meta.get("identifier") != identifier or meta.get("mediatype") != "movies":
        return None
    license_url = str(meta.get("licenseurl", "")).replace("http://", "https://").rstrip("/") + "/"
    labels = {"https://creativecommons.org/publicdomain/mark/1.0/": "Public Domain Mark",
              "https://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0"}
    if license_url not in labels:
        return None
    candidates = []
    for file in item.get("files", []):
        name = file.get("name", "")
        size = str(file.get("size", ""))
        if (re.fullmatch(r"[^/\\]{1,230}\.mp4", name, re.I) and name not in {"..", "."}
                and not re.search(r"(?:excerpt|trailer|preview|\bpart[ _][1-9]\b)", name, re.I)
                and size.isascii() and size.isdecimal() and 0 < int(size) <= MAX_VIDEO_BYTES):
            candidates.append((int(size), name))
    if not candidates:
        return None
    _, name = max(candidates)
    return {"asset_url": f"https://archive.org/download/{identifier}/{quote(name, safe='')}",
            "source_page": f"https://archive.org/details/{identifier}", "license": labels[license_url],
            "license_url": license_url}


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


def authorized_archives(qid, source, identity):
    checked = {**identity, "source_page": source["source_page"]}
    film = archive_film_identity(qid, checked)["film"]
    verify_original_year(identity["year"], film)
    archive_source_record(source["source_page"])


def refresh_catalogue(store, *, sources=None, extract=video_frames, verify=authorized_archives):
    if sources is None:
        sources = (
            ("commons_film", (UPLOAD,), film_links, commons_video),
            ("internet_archive_film", ("archive.org",), internet_archive_links, internet_archive_video),
        )
    name = "sources/cinema.json"
    raw, rev = store.read(name)
    rows = json.loads(raw) if raw else {}
    eligible = {qid: row for qid, row in rows.items() if eligible_year(row.get("year"))}
    added = 0
    known = {entry.get("source_page") for entry in eligible.values()}
    for source_type, domains, discover, lookup in sources:
        if len(eligible) >= MAX_FILMS:
            break
        try:
            candidates = list(discover())
        except (Blocked, KeyError, ValueError, OSError):
            continue
        for qid, identifier, identity in candidates:
            if not eligible_year(identity.get("year")) or qid in eligible:
                continue
            try:
                origin = lookup(identifier)
                if not origin or origin["source_page"] in known:
                    continue
                verify(qid, origin, identity)
                video = request(origin["asset_url"], domains, timeout=55, limit=MAX_VIDEO_BYTES)
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
                stills.append({**identity, **origin, "source_type": source_type,
                               "scene": f"Frame at {timestamp:.2f} s", "timestamp_seconds": timestamp,
                               "perceptual_hash": signature, "sha256": sha, "private_path": path})
            rows[qid] = {**identity, "source_page": origin["source_page"], "stills": stills}
            eligible[qid] = rows[qid]
            known.add(origin["source_page"])
            added += 1
            if len(eligible) >= MAX_FILMS:
                break
    if added:
        store.write(name, json.dumps(rows, ensure_ascii=False).encode(), rev)
    return {"added": added, "available": len(eligible)}


if __name__ == "__main__":
    from os import environ
    from .editorial_zen_cloud import GitHubStore

    print(json.dumps(refresh_catalogue(GitHubStore(environ["EDITORIAL_PRIVATE_TOKEN"]))))
