"""Audit the already delivered images entirely inside the owner-private workflow."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic

from . import editorial_scene_qa as qa
from .editorial_brain import json_reply
from .editorial_chat_plaintext import approval, checked_day, connect, push_private_main, run
from .editorial_images import decode_candidate, image_hashes, similarity
from .editorial_memory import digest, read_json, write_json
from .editorial_offline_review_pack import AUDIT_DIR, CHANNELS, FILE_NAME, IMAGE_LINK
from .editorial_providers import Blocked, text_call
from .editorial_review import HEADING

LEAD = "cover"


def input_sections(raw):
    sections, slots, section = {}, {}, LEAD
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        match = IMAGE_LINK.fullmatch(line)
        heading = HEADING.fullmatch(line)
        if match:
            if section in slots or match[2] in slots.values():
                raise Blocked("invalid_or_reused_audit_slot")
            slots[section] = match[2]
        elif heading and heading[1].strip().casefold() != "оглавление":
            sections[section] = "\n".join(lines)
            section = str(len(sections))
            lines = [heading[1]]
        elif line and not line.startswith(("# ", "**Оглавление", "- ", "* ")):
            lines.append(line)
    sections[section] = "\n".join(lines)
    if set(slots) != set(sections) or len(sections) not in (7, 8):
        raise Blocked("audit_slot_layout_incomplete")
    return sections, slots


def cards_for(channel, source, sections):
    prompt = ("Подготовь паспорта сцен для аудита иллюстраций русской статьи. Выбирай сцены из "
              "текста независимо от текущих изображений. Верни JSON-объект с единственным ключом cards; cards — словарь, где ключи ID разделов, "
              "значение каждого ключа — только объект паспорта. Обложка cover опирается на вступление. "
              "Цитаты evidence_spans копируй дословно и целиком с пунктуацией, на русском. "
              + qa.CARD_RULES + "\nРАЗДЕЛЫ:\n" + json.dumps(sections, ensure_ascii=False))
    generation = text_call("groq", prompt, max_tokens=6100)
    proposals = json_reply(generation)
    if isinstance(proposals.get("cards"), dict):
        proposals = proposals["cards"]
    good, errors = {}, {}
    for key, section in sections.items():
        try:
            good[key] = qa.make_card(channel, key, source, section, proposals.get(key))
        except Blocked as exc:
            errors[key] = str(exc)
    return good, errors, {"model": generation["model"], "usage": generation["usage"]}


def manifest_by_file(folder):
    values = json.loads((folder / "illustrations" / "source-manifest.json").read_text(encoding="utf-8"))
    return {row.get("filename", row.get("file")): row for row in values}


def audit_image(channel, key, slot, card, error, meta, folder, all_hashes):
    path = folder / "illustrations" / f"{slot}.jpg"
    raw = path.read_bytes()
    sha = digest(raw)
    row = {"article_id": channel, "section_id": key, "slot": slot, "image_sha256": sha,
           "generations": 0, "requests": 0}
    if error or card is None or meta.get("sha256") != sha:
        row.update(status="needs_review", reasons=[error or "source_manifest_sha_mismatch"])
        return row
    image, contrast = decode_candidate(raw)
    model, scores, _ = similarity(image, [f"{card['location']}, {', '.join(card['must_have'])}",
                                           card["visual_description"], "an unrelated scene"])
    clip = {"model": model, "scene": scores[0], "topic": scores[1],
            "unrelated": scores[2], "passed": scores[0] >= .19 and scores[0] > scores[2] + .015}
    _, phash = image_hashes(path)
    dupes = [other for other, exact, near in all_hashes if other != (channel, slot)
             and (exact == sha or (int(near, 16) ^ int(phash, 16)).bit_count() <= 6)]
    decision = qa.judge(card, image, sha, clip, duplicate=bool(dupes), clip_gate=False,
                        quality={"passed": True, "size": list(image.size), "contrast": round(contrast, 2)})
    decision["duplicate_slots"] = dupes
    qa.apply_channel_policy(decision, channel.split("-")[0], meta,
                            film={"original_title": "Prisoners", "year": 2013, "imdb": "tt1392214"}
                            if channel == "cinema-01" else None)
    kind = meta.get("kind") or ("illustration" if channel == "legends-01" else "photo")
    if decision["status"] == "rejected":
        if kind == "illustration":
            decision["next_action"] = {"action": "revise_klein_prompt_from_scene_card",
                                       "corrections": qa.corrections(card, decision)}
        else:
            decision["next_action"] = {"action": "find_verified_replacement",
                                       "subject": card["location"], "required": card["must_have"],
                                       "requires_film_identity": channel == "cinema-01"}
    elif decision["status"] != "accepted":
        decision["next_action"] = {"action": "manual_or_independent_visual_review"}
    return {**row, **decision}


def audit(root, day):
    base = root / "editorial-chat-review" / checked_day(day)
    report_dir = base / AUDIT_DIR
    output = report_dir / "image_qa.json"
    all_hashes = []
    for channel in CHANNELS:
        for picture in sorted((base / channel / "illustrations").glob("[0-9][0-9].jpg")):
            all_hashes.append(((channel, picture.stem), *image_hashes(picture)))
    if len(all_hashes) != 23:
        raise Blocked("audit_requires_original_23_images")
    if len({x[0] for x in all_hashes}) != 23:
        raise Blocked("audit_reused_slot")
    article_reports = {}
    reference = read_json(base / "image-qa-reference-v3.json") or {}
    elapsed = monotonic()
    # Audit the owner's priority channel first.
    for channel in ("legends-01", "science-01", "cinema-01"):
        folder = base / channel
        raw = (folder / FILE_NAME).read_text(encoding="utf-8")
        sections, slots = input_sections(raw)
        # Card hashes follow actual local article text independent of Markdown image/TOC decoration.
        source = digest("\n\n".join(sections.values()))
        card_path = report_dir / f"{channel}-cards.json"
        cached = read_json(card_path)
        frozen = reference.get(channel)
        if frozen and frozen.get("source_text_sha256") == source:
            cards, errors = {}, dict(frozen.get("errors", {}))
            for key, section in sections.items():
                try:
                    cards[key] = qa.make_card(channel, key, source, section, frozen["cards"].get(key))
                except Blocked as exc:
                    errors[key] = str(exc)
            provider = {"kind": "independently_labeled_reference_with_literal_evidence"}
        elif cached and cached.get("source_text_sha256") == source:
            cards, errors, provider = (cached["cards"], cached["errors"], cached["provider"])
        else:
            try:
                cards, errors, provider = cards_for(channel, source, sections)
            except (Blocked, ValueError) as exc:
                cards, errors, provider = {}, dict.fromkeys(sections, "scene_card_provider_unavailable"), {}
                print(json.dumps({"channel": channel, "card_error": type(exc).__name__}), flush=True)
            if cards:
                write_json(card_path, {"source_text_sha256": source, "cards": cards,
                                       "errors": errors, "provider": provider})
        manifests = manifest_by_file(folder)
        rows = []
        for key, slot in slots.items():
            try:
                row = audit_image(channel, key, slot, cards.get(key), errors.get(key),
                                  manifests.get(slot + ".jpg", {}), folder, all_hashes)
            except (Blocked, ValueError, OSError) as exc:
                row = {"article_id": channel, "section_id": key, "slot": slot,
                       "image_sha256": next(sha for (ch, image_slot), sha, _ in all_hashes
                                            if (ch, image_slot) == (channel, slot)),
                       "status": "provider_unavailable", "reasons": ["audit_" + type(exc).__name__]}
            if channel == "cinema-01" and key == "7":
                row["reasons"].append("section_contains_no_concrete_film_episode")
                if row["status"] == "accepted":
                    row["status"] = "needs_review"
            rows.append(row)
        article_reports[channel] = {"source_text_sha256": source, "card_provider": provider, "images": rows}
        print(json.dumps({"channel": channel, "images": len(rows),
                          "statuses": dict(Counter(row["status"] for row in rows)),
                          "reason_counts": dict(Counter(reason for row in rows for reason in row["reasons"])),
                          "seconds": round(monotonic() - elapsed, 2)}), flush=True)
    results = {"schema": qa.SCHEMA, "date": day, "articles": article_reports,
               "elapsed_seconds": round(monotonic() - elapsed, 2), "klein_calls": 0,
               "gemini_calls": 0, "publication_authorized": False}
    write_json(output, results)
    return output


def main(day, *, legacy=False):
    approval()
    if json.loads(Path("config/site.json").read_text())["publication_enabled"] is not False:
        raise Blocked("image_audit_requires_publication_freeze")
    with TemporaryDirectory(prefix="editorial-image-audit-") as work:
        repo = Path(work) / "private"
        git, env = connect(os.environ.get("EDITORIAL_PRIVATE_TOKEN"), repo)
        if legacy:
            from .editorial_scene_legacy_test import benchmark
            base = repo / "editorial-chat-review" / checked_day(day)
            results = benchmark(base)
            output = base / "image-qa-legacy-test" / "legacy-checks.json"
            write_json(output, results)
            print(json.dumps({key: results[key] for key in ("count", "passed", "seconds")}), flush=True)
        else:
            output = audit(repo, day)
        args = [*git, "-C", str(repo)]
        run([*args, "add", "--", output.parent.relative_to(repo).as_posix()], env)
        run([*args, "-c", "user.name=Zen Editorial Bot",
             "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit", "-m", "Audit 23 existing images with local pixel checks"], env)
        push_private_main(args, env)
        if not legacy:
            results = read_json(output)
            assert sum(len(article["images"]) for article in results["articles"].values()) == 23
            print(json.dumps({"private_report_saved": True,
                              "elapsed_seconds": results["elapsed_seconds"], "klein_calls": 0}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    main(args.date, legacy=args.legacy)
