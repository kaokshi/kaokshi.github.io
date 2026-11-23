"""One-shot Cloudflare image-generator benchmark, isolated from the Zen editorial release.

No Codex branch writes, no publication, no Groq/Gemini/OpenRouter use.
Hard-coded one prompt, one request per model, no retries, bounded estimate.
Images/results are saved as Actions artifacts and in owner-private review folder.
"""
import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
OWNER_TOKEN = os.environ.get("EDITORIAL_PRIVATE_TOKEN", "")
PRIVATE_REPO = "kaokshi/zen-editorial-review"
DIR = Path(os.environ.get("BENCHMARK_DIR", "benchmark-out"))
WIDTH, HEIGHT = 1024, 768
MAX_ESTIMATED_NEURONS = 5200
TEST_ID = "tram-depot-20261123"
SCENE = (
    "Cinematic realistic wide view at dawn of a forgotten old Russian TRAM DEPOT. "
    "Visible tram rails enter a large brick industrial depot building with broad tram-bay "
    "doors. In the foreground a heavy weathered IRON BAR GATE blocks a narrow underground "
    "passage below the depot; rough wooden PLANKS are nailed across its entrance; loose "
    "earth partly fills the tunnel beyond. One small old-fashioned LANTERN is visible "
    "behind the bars. Cold wet stone, mist, believable architecture, natural atmospheric "
    "light. All five features must be clearly visible: tram rails, brick depot building, "
    "iron gate, wooden planks, one lantern. No glass greenhouse, no conservatory, no "
    "fantasy portal, no giant glowing human figure, no text, no watermark. Landscape 4:3."
)
MODELS = [
    ("klein4b", "@cf/black-forest-labs/flux-2-klein-4b", "multipart", {"width":"1024","height":"768"}, 105),
    ("klein9b", "@cf/black-forest-labs/flux-2-klein-9b", "multipart", {"width":"1024","height":"768"}, 1370),
    ("flux2dev", "@cf/black-forest-labs/flux-2-dev", "multipart", {"width":"1024","height":"768","steps":"8"}, 1250),
    ("phoenix", "@cf/leonardo/phoenix-1.0", "json",
     {"width":WIDTH,"height":HEIGHT,"num_steps":20,"guidance":4}, 2350),
]
# Maximum charged units are estimates from 2026-11-23 Cloudflare public pricing.
# 105+1370+1250+2350=5075; ensure reserve is above this but below free 10k/day.
assert sum(x[4] for x in MODELS) <= MAX_ESTIMATED_NEURONS


def payload(mode, values):
    if mode == "json":
        body = {"prompt": SCENE, **values}
        return json.dumps(body).encode("utf-8"), "application/json"
    boundary = uuid4().hex
    fields = {"prompt": SCENE, **values}
    chunks = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n"
            f"\r\n{value}\r\n".encode("utf-8")
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def decode_image(raw):
    if raw.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
        return raw
    response = json.loads(raw.decode("utf-8"))
    if response.get("success") is False:
        raise ValueError("provider_success_false")
    result = response.get("result", response)
    value = None
    if isinstance(result, dict):
        value = result.get("image")
        if value is None and isinstance(result.get("images"), list) and result["images"]:
            value = result["images"][0]
        if value is None and isinstance(result.get("output"), dict):
            value = result["output"].get("image")
    if isinstance(value, dict):
        value = value.get("image") or value.get("b64_json")
    if not isinstance(value, str):
        raise ValueError("image_base64_missing")
    return base64.b64decode(value, validate=False)


def request_model(model, mode, options):
    body, content_type = payload(mode, options)
    req = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/{model}",
        data=body, method="POST",
        headers={
            "Authorization": "Bearer " + CF_TOKEN,
            "Content-Type": content_type,
            "Accept": "*/*",
            "User-Agent": "ZenEditorialPrivateModelBenchmark/1.0",
        }
    )
    start = time.monotonic()
    try:
        # One HTTP request. No automatic retry on 429 / 503 / timeouts.
        with urlopen(req, timeout=130) as resp:
            raw = resp.read(15_000_001)
            if len(raw) > 15_000_000:
                raise ValueError("response_too_large")
            content = decode_image(raw)
            with Image.open(BytesIO(content)) as image:
                image.load()
                if min(image.size) < 512:
                    raise ValueError("image_too_small")
                return image.convert("RGB"), {
                    "http_status": resp.status,
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "returned_size": list(image.size),
                }
    except HTTPError as exc:
        code = exc.code
        try:
            data = json.loads(exc.read(4096))
            cf_code = (data.get("errors") or [{}])[0].get("code")
        except (ValueError, KeyError, IndexError, TypeError):
            cf_code = None
        return None, {"http_status": code, "provider_error_code": cf_code,
                      "elapsed_seconds": round(time.monotonic() - start, 2)}
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return None, {"status": "request_or_decode_error",
                      "error_type": type(exc).__name__,
                      "elapsed_seconds": round(time.monotonic() - start, 2)}


def private_put(path, content):
    if not OWNER_TOKEN:
        return {"status": "not_uploaded_missing_owner_token"}
    url = "https://api.github.com/repos/" + PRIVATE_REPO + "/contents/" + quote(path, safe="/")
    headers = {
        "Authorization": "Bearer " + OWNER_TOKEN,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "ZenEditorialPrivateModelBenchmark/1.0",
    }
    # A conflict must never re-run image generation.
    for attempt in range(3):
        sha = None
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as resp:
                sha = json.loads(resp.read())["sha"]
        except HTTPError as exc:
            if exc.code != 404:
                return {"status": "github_lookup_failed", "http_status": exc.code}
        data = json.dumps({
            "message": "Private one-shot model benchmark: " + Path(path).name,
            "content": base64.b64encode(content).decode(),
            "branch": "main",
            **({"sha": sha} if sha else {}),
        }).encode()
        try:
            with urlopen(Request(url, headers=headers, data=data, method="PUT"), timeout=75) as resp:
                if resp.status in (200, 201):
                    return {"status": "uploaded_private", "size_bytes": len(content)}
        except HTTPError as exc:
            if exc.code == 409 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            return {"status": "github_write_failed", "http_status": exc.code}
    return {"status": "github_conflict"}


def contact_sheet(images):
    canvas = Image.new("RGB", (900, 1240), "#f5f6f8")
    drawer = ImageDraw.Draw(canvas)
    for idx, (label, im) in enumerate(images):
        y, x = (idx // 2) * 620, (idx % 2) * 450
        drawer.text((x + 12, y + 14), label, fill="#10121a")
        if im is not None:
            thumb = im.copy()
            thumb.thumbnail((428, 550))
            canvas.paste(thumb, (x + 10, y + 48))
        else:
            drawer.text((x + 12, y + 60), "API unavailable / image rejected", fill="#a30020")
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


def main():
    DIR.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[A-Za-z0-9]{32}", ACCOUNT) or not CF_TOKEN:
        raise SystemExit("missing_cloudflare_model_test_credentials")
    if os.environ.get("EDITORIAL_CF_FREE_ONLY") != "true":
        raise SystemExit("free_plan_guard_not_enabled")
    if not OWNER_TOKEN:
        raise SystemExit("private_result_repo_token_missing")
    if os.environ.get("ALLOW_PAID_MODEL_BENCHMARK") == "true":
        raise SystemExit("paid_testing_forbidden")
    rows, images = [], []
    spent_ceiling = 0
    for name, model, mode, opts, ceiling in MODELS:
        if spent_ceiling + ceiling > MAX_ESTIMATED_NEURONS:
            rows.append({"model": name, "status": "skipped_local_neuron_cap"})
            continue
        spent_ceiling += ceiling  # reserve even if request later fails
        image, status = request_model(model, mode, opts)
        row = {"name": name, "model": model, "scene": TEST_ID,
               "requested_width": WIDTH, "requested_height": HEIGHT,
               "estimated_max_neurons_reserved": ceiling, **status}
        if image is not None:
            image_path = DIR / (name + ".jpg")
            image.save(image_path, "JPEG", quality=90, optimize=True)
            data = image_path.read_bytes()
            row["image_sha256"] = sha256(data).hexdigest()
            row["image_bytes"] = len(data)
            row["private_image"] = (
                f"image-generator-benchmark/2026-11-23/{TEST_ID}/{name}.jpg"
            )
            row["private_upload"] = private_put(row["private_image"], data)
        else:
            row["status"] = "generation_failed"
        rows.append(row)
        images.append((name, image))
        # Important: status output deliberately contains no account ID or token.
        print(json.dumps({"model": name, "http_status": row.get("http_status"),
                          "elapsed_seconds": row.get("elapsed_seconds"),
                          "private_upload": row.get("private_upload", {}).get("status"),
                          "image_saved": image is not None}, ensure_ascii=False), flush=True)
    sheet = contact_sheet(images)
    (DIR / "comparison.jpg").write_bytes(sheet)
    private_dir = f"image-generator-benchmark/2026-11-23/{TEST_ID}/"
    sheet_upload = private_put(private_dir + "comparison.jpg", sheet)
    report = {
        "benchmark": TEST_ID,
        "ran_utc": datetime.now(timezone.utc).isoformat(),
        "isolation": "separate_branch_not_codex_or_production",
        "text_model_calls": 0,
        "request_count": len(rows),
        "actual_neurons": "not_reported",
        "estimated_max_neurons_reserved": spent_ceiling,
        "cloudflare_free_neurons_per_day": 10000,
        "cloudflare_other_usage": "unknown",
        "prompts_identical": True,
        "prompt": SCENE,
        "width": WIDTH, "height": HEIGHT,
        "source": "Cloudflare Workers AI",
        "private_comparison": private_dir + "comparison.jpg",
        "private_sheet_upload": sheet_upload,
        "models": rows,
        "publication_authorized": False,
        "interpretation": "One sample per model; no winner claimed without visual inspection.",
    }
    body = json.dumps(report, ensure_ascii=False, indent=2).encode()
    (DIR / "report.json").write_bytes(body)
    result = private_put(private_dir + "report.json", body)
    print(json.dumps({"summary": {
        "successful_images": sum("image_sha256" in r for r in rows),
        "models_attempted": len(rows),
        "max_neurons_reserved": spent_ceiling,
        "private_report_upload": result.get("status"),
        "private_comparison_upload": sheet_upload.get("status"),
        "publication_authorized": False,
    }}, ensure_ascii=False), flush=True)
    return 0 if any("image_sha256" in r for r in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
