"""Owner-approved private image pack: sourced film frames and Klein mystical art.

Never writes to the public Pages repository or enables publication. All frames
without permissions stay marked 'rights_review_pending'.
"""
import argparse
from contextlib import contextmanager
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .editorial_chat_plaintext import approval, checked_day, connect, run
from .editorial_providers import Blocked, klein, request

FILM_ID = "Q3404003"
SCENES = (
    "A forgotten late-19th-century underground winter garden entrance beneath an old tram depot, metal gate, wet stone stairs descending into darkness, single shaft of pale daylight, Russian urban gothic, cinematic wide shot, no people, no lettering",
    "Deep underground abandoned winter garden, broken glass canopy covered by layers of city stone, black trees, dead leaves and tiny lantern lit path, realistic mysterious Russian city atmosphere, no writing",
    "The translucent ghost of an 1824 city clerk in a long dark wool coat walking through a derelict underground park, holding paper, softly luminous outline, no horror gore, no text",
    "Close view of an old city registry book opened beside a flickering oil lamp, 19th century ink, paper edges worn, faint ghostly fingerprints on glass, atmospheric macro cinema photography, no legible lettering",
    "Three different shimmering silhouettes of vanished nineteenth century citizens standing on an empty cobblestone lane under the buried garden, lantern haze, ominous, no text",
    "An underground archway showing a vision of a modern residential street dissolving into the old Tikhiy Lane with houses fading through one another, eerie, grounded architecture, no words",
    "Ivan, middle-aged Russian archaeologist with a weathered coat, gripping a rusted lever in a tunnel as glowing ancestral memories swirl and the surrounding walls crack, dramatic cinematic horror, no lettering",
    "A single lonely human-shaped silhouette glowing inside the empty winter garden beneath a modern city, dawn light through broken glass, unsettling but quiet ending, cinematic cover quality, no text",
)


def _decode(raw):
    from PIL import Image
    image = Image.open(BytesIO(raw))
    image.load()
    if image.width < 480 or image.height < 320:
        raise Blocked("image_resolution_insufficient")
    if image.width * image.height > 14_000_000:
        raise Blocked("image_too_large")
    image = image.convert("RGB")
    image.thumbnail((1600, 1200))
    output = BytesIO()
    image.save(output, "JPEG", quality=88, optimize=True)
    return output.getvalue(), [image.width, image.height]


def _hash(raw):
    from PIL import Image, ImageOps
    image = ImageOps.grayscale(Image.open(BytesIO(raw))).resize((9, 8))
    pixels = list(image.getdata())
    bits = [pixels[y*9+x] > pixels[y*9+x+1] for y in range(8) for x in range(8)]
    return sum(int(bit) << i for i, bit in enumerate(bits))


def _image_records(kind):
    if kind == "cinema":
        path = Path(__file__).resolve().parents[1] / "config/editorial_cinema.json"
        rows = json.loads(path.read_text(encoding="utf-8"))[FILM_ID]["stills"]
        if len(rows) != 8:
            raise Blocked("cinema_eight_verified_candidates_missing")
        # The 2400px car view has best potential as the opening cover.
        return [rows[1], rows[0], *rows[2:]]
    if kind == "legends":
        return [{"scene": prompt} for prompt in SCENES]
    raise Blocked("unknown_private_visual_channel")


def produce(kind, day, *, token):
    approval()
    checked_day(day)
    if not token:
        raise Blocked("private_review_token_missing")
    if kind == "legends" and os.environ.get("EDITORIAL_CF_FREE_ONLY") != "true":
        raise Blocked("cloudflare_free_account_unconfirmed")
    with TemporaryDirectory(prefix="zen-private-visuals-") as work:
        repo_path = Path(work) / "repo"
        git, env = connect(token, repo_path)
        destination = repo_path / "editorial-chat-review" / day / (
            "cinema-01" if kind == "cinema" else "legends-01") / "illustrations"
        destination.mkdir(parents=True, exist_ok=True)
        rows = _image_records(kind)
        existing = {p.name for p in destination.glob("[0-9][0-9].jpg")}
        created = []
        hashes = []
        errors = []
        for i, row in enumerate(rows, 1):
            filename = f"{i:02d}.jpg"
            image_path = destination / filename
            if filename in existing and image_path.stat().st_size > 10000:
                # Content is owner-reviewed, not part of public publication.
                payload = image_path.read_bytes()
                hashes.append(_hash(payload))
                continue
            try:
                if kind == "cinema":
                    host = urlsplit(row["asset_url"]).hostname
                    if not host or not row["source_page"].startswith("https://"):
                        raise Blocked("unsafe_film_image_locator")
                    raw = request(row["asset_url"], (host,), timeout=15)
                else:
                    raw, _ = klein(
                        row["scene"] +
                        ". Photorealistic moody Russian supernatural mystery, 16:9, "
                        "no words, no captions, no watermarks, consistent palette."
                    )
                payload, resolution = _decode(raw)
                fingerprint = _hash(payload)
                if any((fingerprint ^ prior).bit_count() <= 5 for prior in hashes):
                    raise Blocked("duplicate_visual_needs_human_review")
                image_path.write_bytes(payload)
                hashes.append(fingerprint)
                created.append({"filename": filename, "size": resolution,
                                "sha256": sha256(payload).hexdigest(),
                                "source_page": row.get("source_page"),
                                "rights_status": ("rights_review_pending" if kind == "cinema"
                                                  else "generated_for_private_review"),
                                "scene": row["scene"]})
                # Commit each successful scene; any later provider error must not
                # require calling Klein again for already stored and paid images.
                manifest_path = destination / "source-manifest.json"
                manifests = json.loads(manifest_path.read_text()) if manifest_path.is_file() else []
                manifests = [m for m in manifests if m["filename"] != filename] + [created[-1]]
                manifest_path.write_text(json.dumps(sorted(manifests, key=lambda r:r["filename"]),
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
                args = [*git, "-C", str(repo_path)]
                run([*args, "add", "--", image_path.relative_to(repo_path).as_posix(),
                     manifest_path.relative_to(repo_path).as_posix()], env)
                run([*args, "-c", "user.name=Zen Editorial Bot",
                     "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "commit", "-m", f"Private {kind} image {i} of 8"], env)
                run([*args, "push", "origin", "HEAD:refs/heads/main"], env, timeout=90)
                print(json.dumps({"image": i, "saved_to_private": True,
                                  "kind": kind, "resolution": resolution}, ensure_ascii=False), flush=True)
            except (Blocked, OSError, ValueError) as exc:
                errors.append({"image": i, "reason": str(exc) if isinstance(exc, Blocked)
                               else type(exc).__name__})
                print(json.dumps({"image": i, "saved_to_private": False,
                                  "error_type": errors[-1]["reason"]}), flush=True)
        return {"channel": kind, "total": len(rows), "existing": len(existing),
                "new_saved": len(created), "errors": errors,
                "private_dir": "editorial-chat-review/" + day + ("/cinema-01" if kind == "cinema" else "/legends-01") + "/illustrations",
                "publication_authorized": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("channel", choices=("cinema", "legends"))
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    result = produce(args.channel, args.date, token=os.environ.get("EDITORIAL_PRIVATE_TOKEN"))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
