"""Finish the saved historical drafts without rewriting text or publishing."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from . import editorial_scene_qa as qa
from .editorial_brain import json_reply, settings
from .editorial_chat_plaintext import approval, connect, push_private_main, run
from .editorial_images import image_hashes
from .editorial_memory import Memory, digest, read_json, write, write_json
from .editorial_offline_review_pack import AUDIT_DIR, CHANNELS, FILE_NAME
from .editorial_providers import Blocked, KLEIN, klein, request, text_call
from .editorial_saved_image_triage import DAY, FILM, OUTPUT, verify_slot
from .editorial_scene_audit import audit_image, input_sections, manifest_by_file

WORK = "image-qa-final-repair"
TMDB = "https://www.themoviedb.org/movie/146233-prisoners/images/backdrops"
TMDB_ASSET = re.compile(r"https://image\.tmdb\.org/t/p/original/[A-Za-z0-9]+\.jpg")


def cards_and_selections(base):
    audit = read_json(base / AUDIT_DIR / "image_qa.json")
    triage_base = base / OUTPUT
    report_path = triage_base / "report.json"
    triage = read_json(report_path)
    plan = read_json(base / "image-qa-final-repair-input.json")
    if not plan or plan["source_triage_sha256"] != digest(report_path.read_bytes()):
        raise Blocked("final_repair_triage_sha_changed")
    if triage["source_audit"] != digest((base / AUDIT_DIR / "image_qa.json").read_bytes()):
        raise Blocked("final_repair_original_audit_sha_changed")
    selected, pending = {}, {}
    for channel in CHANNELS:
        folder = base / channel
        original = audit["articles"][channel]
        sections, slots = input_sections((folder / FILE_NAME).read_text(encoding="utf-8"))
        source = digest("\n\n".join(sections.values()))
        if source != original["source_text_sha256"]:
            raise Blocked("final_repair_text_changed")
        manifest = manifest_by_file(folder)
        for row in original["images"]:
            slot = row["slot"]
            section = row["section_id"]
            key = (channel, slot)
            if slots[section] != slot:
                raise Blocked("final_repair_slot_changed")
            if row["status"] == "accepted":
                path = verify_slot(folder, slot, row["image_sha256"])
                selected[key] = (path, row["scene_card"], manifest[path.name])
                continue
            decision = triage["results"][channel][slot]
            if decision["draft_status"] != "accepted":
                entry = plan.get(channel.split("-")[0], {}).get(slot)
                if not entry:
                    raise Blocked("final_repair_missing_target")
                card = qa.make_card(channel, section, source, sections[section],
                                    entry if channel == "legends-01" else entry["card"])
                pending[key] = (card, sections[section], entry)
                continue
            path = triage_base / "selected" / channel / f"{slot}.jpg"
            verified = decision["staged_recheck"]
            if (decision["selected_file"] != f"selected/{channel}/{slot}.jpg"
                    or verified["status"] != "accepted"
                    or digest(path.read_bytes()) != verified["image_sha256"]):
                raise Blocked("final_repair_saved_selection_changed")
            meta = dict(manifest[f"{decision['candidate_source_slot']}.jpg"])
            if channel == "cinema-01":
                policy = verified["policy"]
                if not policy.get("film_provenance") or policy.get("spoiler_review") != "verified":
                    raise Blocked("final_repair_movie_provenance_missing")
                meta.update(kind="photo", film_id=FILM["qid"], original_title=FILM["original_title"],
                            year=FILM["year"], spoiler_safe_verified=True)
            selected[key] = (path, verified["scene_card"], meta)
    if len(selected) != 16 or len(pending) != 7:
        raise Blocked("final_repair_expected_sixteen_approved_seven_open")
    return selected, pending


def receipt_path(path):
    return path.with_name(path.name + ".receipt.json") if path.suffix == ".json" else path.with_suffix(".json")


def recover_or_call(memory, operation, provider, callback, path):
    if path.is_file():
        receipt = read_json(receipt_path(path))
        raw = path.read_bytes()
        if not receipt or receipt.get("sha256") != digest(raw):
            raise Blocked("final_repair_saved_response_sha_mismatch")
        return raw
    if any(row["task"] == operation for row in memory.data["calls"]):
        raise Blocked("final_repair_previous_request_preserved_no_resend")

    def capture():
        raw, metadata = callback()
        write(path, raw)
        write_json(receipt_path(path), {"sha256": digest(raw), **metadata})
        return raw, metadata

    raw, _ = memory.call(operation, "legends-01", provider, capture)
    return raw


def run_repairs(base, sync, *, generate=False):
    selected, pending = cards_and_selections(base)
    work = base / WORK
    config = settings()
    # Four different targets at most, once per target. Prior unresolved requests
    # live in their original checkpoints and are never replayed by this workflow.
    prior_composition_collision = read_json(work / "groq-compositions.json") or {}
    prior_groq = int(set(prior_composition_collision) == {"model", "sha256", "usage"})
    config["daily_calls"].update(klein=min(config["daily_calls"]["klein"], 4), groq=1 + prior_groq)
    config["per_article_calls"].update(klein=min(config["per_article_calls"]["klein"], 4), groq=1 + prior_groq)
    memory = Memory(work / "ledger", DAY, datetime.now(timezone.utc) + timedelta(minutes=24), config,
                    private_git=SimpleNamespace(checkpoint=lambda: sync("Persist historical repair reservation or receipt")))
    descriptions = work / ("groq-compositions-recovered.json" if prior_groq else "groq-compositions.json")
    if generate and not descriptions.is_file():
        grouped = {slot: {"scene": card, "section": section} for (channel, slot), (card, section, _)
                   in pending.items() if channel == "legends-01"}
        message = (
            "Составь по одной композиции для каждого кадра по проверенному паспорту и цитатам. "
            "Сделай все обязательные предметы различимыми. Персонажи, положение, освещение и окружение "
            "должны соответствовать одному моменту из раздела. Для узнаваемости здания покажи отличительные "
            "признаки крупно и в одном кадре. Верни только JSON, ключи — номера, значения — английские описания "
            "не длиннее 65 слов. Данные:\n" + json.dumps(grouped, ensure_ascii=False)
        )

        def compose():
            reply = text_call("groq", message, max_tokens=950, response_json=True)
            composition = json_reply(reply)
            if set(composition) != set(grouped) or not all(isinstance(value, str) and 20 <= len(value) <= 900
                                                         for value in composition.values()):
                raise Blocked("final_repair_composition_contract_invalid")
            return json.dumps(composition).encode(), {"model": reply["model"], "usage": reply["usage"]}

        # The raw response is stored separately from the reservation ledger and
        # restored from the same private branch on a subsequent workflow.
        operation = "historical-composition-collision-recovery" if prior_groq else "historical-compositions-v1"
        recover_or_call(memory, operation, "groq", compose, descriptions)

    compositions = read_json(descriptions) or {}
    all_hashes = [(key, *image_hashes(path)) for key, (path, _, _) in selected.items()]
    failures = {}
    gallery = None
    for (channel, slot), (card, _, entry) in pending.items():
        key = channel + "/" + slot
        path = work / "candidates" / channel / f"{slot}.jpg"
        try:
            if channel == "legends-01":
                if generate and not path.is_file():
                    scene = {**card, "visual_description": compositions[slot]}
                    prompt = (qa.visual_prompt(scene) + " Cinematic urban legend illustration. "
                              "A single coherent, plausible scene in landscape format. No text or watermark.")
                    recover_or_call(memory, f"historical-standalone-v1-{slot}", "klein",
                                    lambda: klein(prompt), path)
                if not path.is_file():
                    raise Blocked("final_repair_saved_legend_candidate_missing")
                meta = {"kind": "illustration", "model": KLEIN, "rights_status": "rights_review_pending"}
            else:
                url = entry["asset_url"]
                if not TMDB_ASSET.fullmatch(url) or not entry["spoiler_review"]:
                    raise Blocked("final_repair_film_source_unverified")
                if not path.is_file():
                    if gallery is None:
                        gallery = request(TMDB, ("www.themoviedb.org",), timeout=15)
                    if url.encode() not in gallery:
                        raise Blocked("final_repair_still_not_in_film_gallery")
                    body = request(url, ("image.tmdb.org",), timeout=15)
                    write(path, body)
                    write_json(path.with_suffix(".json"), {"sha256": digest(body), "asset_url": url,
                                                          "gallery": TMDB, "spoiler_review": entry["spoiler_review"]})
                    sync("Preserve sourced film still")
                meta = {"kind": "photo", "film_id": FILM["qid"], "original_title": FILM["original_title"],
                        "year": FILM["year"], "source_page": TMDB, "asset_url": url,
                        "spoiler_safe_verified": True, "rights_status": "rights_review_pending"}
            receipt = read_json(path.with_suffix(".json"))
            if not receipt or receipt["sha256"] != digest(path.read_bytes()):
                raise Blocked("final_repair_candidate_sha_changed")
            if channel == "cinema-01" and (receipt.get("asset_url") != url or receipt.get("gallery") != TMDB
                                           or receipt.get("spoiler_review") != entry["spoiler_review"]):
                raise Blocked("final_repair_film_receipt_mismatch")
            meta["sha256"] = receipt["sha256"]
            result = audit_image(channel, card["section_id"], slot, card, None, meta, base / channel,
                                 all_hashes, candidate=path, film=FILM if channel == "cinema-01" else None)
            write_json(work / "checks" / channel / f"{slot}.json", result)
            if result["status"] == "accepted":
                selected[channel, slot] = (path, card, meta)
                all_hashes.append(((channel, slot), *image_hashes(path)))
            else:
                failures[key] = result["status"]
        except (Blocked, OSError, ValueError, KeyError) as exc:
            failures[key] = str(exc) if isinstance(exc, Blocked) else type(exc).__name__

    # The full repeat includes the seven science originals and every retained
    # image, evaluated against the exact section and all other selected bytes.
    articles = {}
    for (channel, slot), (path, card, meta) in sorted(selected.items()):
        section = card["section_id"]
        result = audit_image(channel, section, slot, card, None, meta, base / channel,
                             all_hashes, candidate=path, film=FILM if channel == "cinema-01" else None)
        if result["status"] != "accepted":
            failures[channel + "/" + slot] = result["status"]
        article = articles.setdefault(channel, {"source_text_sha256": card["source_text_sha256"], "images": []})
        result["selected_path"] = path.relative_to(base).as_posix()
        result["provenance"] = meta
        article["images"].append(result)
    accepted = sum(row["status"] == "accepted" for article in articles.values() for row in article["images"])
    result = {"schema": qa.SCHEMA, "date": DAY, "articles": articles, "accepted": accepted,
              "failures": failures, "ready": accepted == 23 and not failures,
              "model_calls": {provider: sum(row["provider"] == provider for row in memory.data["calls"])
                              for provider in ("groq", "klein", "gemini")},
              "publication_authorized": False}
    write_json(work / "image_qa.json", result)
    sync("Preserve full visual re-audit of the historical drafts")
    return result


def materialize_verified_selection(base, destination):
    path = base / WORK / "image_qa.json"
    report = read_json(path)
    if (not report or report.get("schema") != qa.SCHEMA or report.get("ready") is not True
            or report.get("accepted") != 23 or report.get("failures")
            or set(report.get("articles", {})) != set(CHANNELS)):
        raise Blocked("historical_release_full_audit_not_ready")
    for channel, article in report["articles"].items():
        source_folder = base / channel
        output = destination / channel
        source_text = (source_folder / FILE_NAME).read_bytes()
        sections, slots = input_sections(source_text.decode("utf-8"))
        if article["source_text_sha256"] != digest("\n\n".join(sections.values())):
            raise Blocked("historical_release_saved_text_changed")
        rows = article["images"]
        if len(rows) != len(slots):
            raise Blocked("historical_release_slot_count_changed")
        manifest = []
        for row in rows:
            slot = row["slot"]
            if (row.get("status") != "accepted" or slots.get(row["section_id"]) != slot
                    or not row.get("clip", {}).get("model")
                    or not (row.get("vision") or {}).get("models", {}).get("blip")):
                raise Blocked("historical_release_image_not_verified")
            source = (base / row["selected_path"]).resolve()
            if not source.is_relative_to(base.resolve()):
                raise Blocked("historical_release_selected_path_invalid")
            raw = source.read_bytes()
            if digest(raw) != row["image_sha256"]:
                raise Blocked("historical_release_selected_image_changed")
            name = f"{slot}.jpg"
            write(output / "illustrations" / name, raw)
            entry = {key: value for key, value in row["provenance"].items() if key not in {"file", "filename"}}
            manifest.append({**entry, "filename": name, "sha256": row["image_sha256"]})
        write(output / FILE_NAME, source_text)
        write_json(output / "illustrations" / "source-manifest.json", manifest)
    write(destination / AUDIT_DIR / "image_qa.json", path.read_bytes())


def main(*, generate=False):
    approval()
    if read_json(Path("config/site.json"))["publication_enabled"] is not False:
        raise Blocked("historical_repair_requires_publication_freeze")
    with TemporaryDirectory(prefix="editorial-historical-release-") as tmp:
        checkout = Path(tmp) / "private"
        git, env = connect(os.environ.get("EDITORIAL_PRIVATE_TOKEN"), checkout)
        base = checkout / "editorial-chat-review" / DAY
        args = [*git, "-C", str(checkout)]
        relative = (base / WORK).relative_to(checkout).as_posix()

        def sync(msg):
            run([*args, "add", "--", relative], env)
            if run([*args, "status", "--porcelain", "--", relative], env):
                run([*args, "-c", "user.name=Zen Editorial Bot",
                     "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "commit", "-m", msg], env)
                push_private_main(args, env)

        report = run_repairs(base, sync, generate=generate)
        print(json.dumps({k: report[k] for k in ("accepted", "failures", "ready", "model_calls")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    main(generate=parser.parse_args().generate)
