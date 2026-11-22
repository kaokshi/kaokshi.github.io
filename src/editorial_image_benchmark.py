"""Reproducible offline model comparison over a frozen, human-labelled image pool."""
import argparse
from collections import Counter
from io import BytesIO
from pathlib import Path
import resource
import statistics
from time import perf_counter, sleep

from PIL import Image, ImageDraw, ImageOps

from .editorial_memory import digest, read_json, write, write_json
from .editorial_images import COMMONS
from .editorial_providers import ProviderError, request
from .editorial_visual_matching import (Scene, baseline_order, compare_pixels, describe_pixels, load_model,
                                        rank_key, shortlist, verified_order)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "editorial_image_matching.json"
TOPIC = "an unrelated image with no connection with this topic"
MAX_PIXELS = 22_000_000


def read_manifest(path):
    data = read_json(path)
    ids = {row["id"]: row for row in data["images"]}
    if len(ids) != 30 or Counter(row["channel"] for row in ids.values()) != {
            "legends": 10, "science": 10, "cinema": 10}:
        raise ValueError("benchmark_requires_10_unique_images_per_channel")
    if len({row["sha256"] for row in ids.values()}) != len(ids):
        raise ValueError("benchmark_image_bytes_must_be_unique")
    for case in data["cases"]:
        Scene.read(case)
        candidates, labels = set(case["candidates"]), case["labels"]
        if (len(candidates) != len(case["candidates"]) or candidates != labels.keys()
                or any(ids[key]["channel"] != case["channel"] for key in candidates)
                or set(labels.values()) != {"match", "near", "irrelevant"}):
            raise ValueError("benchmark_requires_frozen_candidate_order_and_three_label_types")
    return data


def collect_dataset(data, workspace):
    for row in data["images"]:
        dest = workspace / (row["id"] + ".img")
        if row.get("path"):
            path = (ROOT / row["path"]).resolve()
            if not path.is_relative_to(ROOT / "content" / "assets"):
                raise ValueError("invalid_versioned_reference_path")
            raw = path.read_bytes()
        else:
            for attempt in range(3):
                try:
                    raw = request(row["url"], COMMONS, timeout=18, limit=15_000_000)
                    break
                except ProviderError as exc:
                    if attempt == 2 or str(exc) != "http_429_quota_or_rate_limit":
                        raise ValueError(f"fixture_unavailable: {row['id']}: {exc}") from exc
                    sleep((2, 8)[attempt])
        if digest(raw) != row["sha256"]:
            raise ValueError("benchmark_fixture_hash_changed: " + row["id"])
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("benchmark_fixture_dimensions")
            image.verify()
        write(dest, raw)


def pixels(input_dir, key):
    with Image.open(input_dir / (key + ".img")) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def run_model(kind, data, workspace):
    import torch

    torch.set_num_threads(2)
    start = perf_counter()
    load_model(kind)
    initialization = perf_counter() - start
    start = perf_counter()
    durations, output = [], {}
    if kind == "florence":
        siglip = read_json(workspace / "siglip.json")["cases"]
        selected = sorted({row["id"] for case in siglip.values() for row in shortlist(case)})
        for key in selected:
            before = perf_counter()
            output[key] = describe_pixels(pixels(workspace, key))
            durations.append(perf_counter() - before)
    else:
        for case in data["cases"]:
            scene = Scene.read(case)
            prompts = [scene.subject, case["topic"], TOPIC]
            if kind == "siglip":
                prompts += list(scene.forbidden)
            rows = []
            for key in case["candidates"]:
                before = perf_counter()
                scores, _ = compare_pixels(kind, pixels(workspace, key), prompts)
                elapsed = perf_counter() - before
                durations.append(elapsed)
                rows.append({"id": key, "scores": scores, "seconds": elapsed})
            output[case["id"]] = rows
    return {"model": kind, "initialization_seconds": initialization, "inference_seconds": perf_counter() - start,
            "seconds_per_candidate": statistics.mean(durations), "candidate_evaluations": len(durations),
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            ("captions" if kind == "florence" else "cases"): output}


def metrics(cases, order, evaluated=None):
    counts = Counter()
    for case in cases:
        key, labels = case["id"], case["labels"]
        ranked = order[key]
        accepted = set(ranked if evaluated is None else evaluated[key])
        counts["correct_top"] += bool(ranked and labels[ranked[0]] == "match")
        counts["recall_at_5"] += any(labels[k] == "match" for k in ranked[:5])
        counts["wrong_top"] += bool(ranked and labels[ranked[0]] != "match")
        counts["abstained"] += not ranked
        counts["false_acceptance"] += sum(labels[k] != "match" for k in accepted)
        counts["false_rejection"] += sum(label == "match" and k not in accepted for k, label in labels.items())
    return {"cases": len(cases), "precision_at_1": counts["correct_top"] / len(cases),
            "recall_at_5_case_hit_rate": counts["recall_at_5"] / len(cases),
            "wrong_first": counts["wrong_top"], "abstentions": counts["abstained"],
            "semantic_error_rate_all_cases": counts["wrong_top"] / len(cases),
            "false_acceptance": counts["false_acceptance"], "false_rejection": counts["false_rejection"]}


def aggregate(data, workspace):
    clip, siglip, florence = [read_json(workspace / (kind + ".json")) for kind in ("clip", "siglip", "florence")]
    legacy, clip_rank, sig_rank, verify, details = {}, {}, {}, {}, {}
    for case in data["cases"]:
        key = case["id"]
        a_rows, b_rows = clip["cases"][key], siglip["cases"][key]
        a = baseline_order(a_rows)
        legacy[key] = [row["id"] for row in a]
        clip_rank[key] = [row["id"] for row in sorted(a_rows, key=lambda row: row["scores"][0], reverse=True)]
        sig_rank[key] = [row["id"] for row in sorted(b_rows, key=lambda row: rank_key(row["scores"]), reverse=True)]
        accepted, judged = verified_order(Scene.read(case), b_rows, florence["captions"])
        verify[key] = [row["id"] for row in accepted]
        details[key] = {"channel": case["channel"], "description": case["subject"],
                        "clip": legacy[key][0] if a else None, "siglip": sig_rank[key][0],
                        "siglip_florence": verify[key][0] if accepted else None,
                        "labels": case["labels"], "shortlist": judged,
                        "clip_candidate_decisions": [
                            {**row, "reason": ("passed_clip_threshold" if row in a else "below_clip_threshold")}
                            for row in a_rows],
                        "siglip_candidates": [
                            {**row, "reason": ("caption_checked" if row["id"] in {x["id"] for x in judged}
                                              else "below_top_five")}
                            for row in b_rows]}
    variants = {"legacy_clip": legacy, "ranked_clip": clip_rank, "ranked_siglip": sig_rank,
                "siglip_florence": verify}
    return {"labels": data["label_method"], "metrics": {
                name: metrics(data["cases"], order,
                              {k: v[:5] for k, v in order.items()} if name.startswith("ranked_") else None)
                for name, order in variants.items()},
            "runtime": {d["model"]: {k: v for k, v in d.items() if k not in ("cases", "captions")}
                        for d in (clip, siglip, florence)},
            "cases": details}


def contact_sheet(data, report, workspace):
    images = {row["id"]: row for row in data["images"]}
    output = Image.new("RGB", (840, 192 * len(data["cases"]) + 24), "white")
    draw = ImageDraw.Draw(output)
    credits = {}
    for i, (key, case) in enumerate(report["cases"].items()):
        for column, (method, title) in enumerate((("clip", "A / CLIP"), ("siglip", "SigLIP 2"),
                                                  ("siglip_florence", "B / SigLIP 2 + Florence-2"))):
            x, y = column * 280, i * 192 + 6
            candidate = case[method]
            title = f"{key}: {title}"
            draw.text((x + 4, y), title, fill="black")
            if candidate is None:
                draw.text((x + 4, y + 50), "No image selected", fill="black")
                continue
            fixture = images[candidate]
            if fixture["reusable"]:
                image = ImageOps.contain(pixels(workspace, candidate), (266, 136))
                output.paste(image, (x + 4, y + 21))
                credits[candidate] = {k: fixture[k] for k in ("source", "creator", "license", "license_url")}
                credits[candidate]["changes"] = "Reduced for the contact sheet"
            else:
                draw.text((x + 4, y + 50), "See source record", fill="black")
            draw.text((x + 4, y + 160), f"{candidate}: {case['labels'][candidate]}", fill="black")
    output.save(workspace / "contact-sheet.jpg", quality=88)
    write_json(workspace / "contact-sheet-credits.json", credits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("download", "clip", "siglip", "florence", "report"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    data = read_manifest(args.manifest)
    if args.command == "download":
        collect_dataset(data, args.workspace)
    elif args.command == "report":
        report = aggregate(data, args.workspace)
        write_json(args.workspace / "benchmark.json", report)
        contact_sheet(data, report, args.workspace)
        print({"metrics": report["metrics"], "runtime": report["runtime"]})
    else:
        outcome = run_model(args.command, data, args.workspace)
        write_json(args.workspace / (args.command + ".json"), outcome)
        print({k: v for k, v in outcome.items() if k not in ("cases", "captions")})


if __name__ == "__main__":
    main()
