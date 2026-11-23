"""Check owner-private saved candidates against the unchanged historical drafts."""

import json
import os
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from . import editorial_scene_qa as qa
from .editorial_chat_plaintext import approval, connect, push_private_main, run
from .editorial_images import image_hashes
from .editorial_memory import digest, read_json, write, write_json
from .editorial_offline_review_pack import AUDIT_DIR, FILE_NAME
from .editorial_providers import Blocked
from .editorial_scene_audit import audit_image, input_sections, manifest_by_file

DAY = "2026-11-23"
OUTPUT = "image-qa-saved-triage"
FILM = {"qid": "Q3404003", "original_title": "Prisoners", "year": 2013, "imdb": "tt1392214"}


def verify_slot(folder, slot, sha):
    if slot not in {f"{i:02}" for i in range(1, 9)}:
        raise Blocked("triage_invalid_saved_slot")
    path = folder / "illustrations" / f"{slot}.jpg"
    if digest(path.read_bytes()) != sha:
        raise Blocked("triage_original_image_sha_mismatch")
    return path


def review(base):
    previous = base / AUDIT_DIR / "image_qa.json"
    plan = read_json(base / "image-qa-saved-triage-input.json")
    if not plan or plan["audit_sha256"] != digest(previous.read_bytes()):
        raise Blocked("triage_original_audit_changed")
    original = read_json(previous)
    registry = read_json(Path("config/editorial_cinema.json"))[FILM["qid"]]["stills"]
    output = base / OUTPUT
    results, occupied = {}, []
    # Reserve all already approved images before assessing reassignments.
    for channel, article in original["articles"].items():
        for row in article["images"]:
            path = verify_slot(base / channel, row["slot"], row["image_sha256"])
            if row["status"] == "accepted":
                occupied.append(((channel, row["slot"]), *image_hashes(path)))
    for channel, article in original["articles"].items():
        folder = base / channel
        sections, slots = input_sections((folder / FILE_NAME).read_text(encoding="utf-8"))
        text_sha = digest("\n\n".join(sections.values()))
        rows = {row["slot"]: row for row in article["images"]}
        if text_sha != article["source_text_sha256"] or {s for s, row in rows.items() if row["status"] != "accepted"} != set(plan["articles"][channel]):
            raise Blocked("triage_saved_draft_or_audit_changed")
        source_manifest = manifest_by_file(folder)
        details = {}
        for dest, decision in sorted(plan["articles"][channel].items()):
            original_row = rows[dest]
            section = original_row["section_id"]
            if slots[section] != dest:
                raise Blocked("triage_slot_position_changed")
            result = {"finding": decision["finding"], "reason": decision["reason"],
                      "previous_status": original_row["status"], "draft_status": original_row["status"],
                      "previous_sha256": original_row["image_sha256"], "section_id": section}
            candidate = decision.get("candidate")
            if not candidate:
                details[dest] = result
                continue
            source = candidate["source_slot"]
            if source != dest and decision["finding"] != "confirmed_wrong":
                raise Blocked("triage_replacement_without_confirmed_mismatch")
            path = verify_slot(folder, source, candidate["source_sha256"])
            metadata = dict(source_manifest[path.name])
            if channel == "cinema-01":
                matches = [row for row in registry if row["source_page"] == metadata.get("source_page")
                           and row["scene"] == metadata.get("scene")]
                if len(matches) == 1:
                    metadata.update(film_id=FILM["qid"], original_title=matches[0]["original_title"],
                                    year=matches[0]["year"], kind="photo",
                                    source_asset_url=matches[0]["asset_url"])
                    if candidate.get("spoiler_evidence"):
                        metadata["spoiler_safe_verified"] = True
                        result["spoiler_evidence"] = candidate["spoiler_evidence"]
            card = qa.make_card(channel, section, text_sha, sections[section], candidate["card"])
            audit = audit_image(channel, section, dest, card, None, metadata, folder, occupied,
                                candidate=path, film=FILM if channel == "cinema-01" else None)
            result.update(candidate_source_slot=source, recheck=audit)
            if audit["status"] == "accepted":
                staged = output / "selected" / channel / f"{dest}.jpg"
                already_selected = staged.exists()
                if already_selected and digest(staged.read_bytes()) != audit["image_sha256"]:
                    raise Blocked("triage_preexisting_selection_changed")
                write(staged, path.read_bytes())
                # Recheck the staged bytes at the actual destination and reapply
                # quality, provenance and duplicate checks before selecting them.
                final = audit_image(channel, section, dest, card, None, metadata, folder,
                                    occupied, candidate=staged, film=FILM if channel == "cinema-01" else None)
                if final["status"] != "accepted":
                    if not already_selected:
                        staged.unlink()
                    result["staged_recheck"] = final
                else:
                    occupied.append(((channel, dest), *image_hashes(staged)))
                    result.update(staged_recheck=final, draft_status="accepted",
                                  selected_file=staged.relative_to(output).as_posix(),
                                  action="replace_from_saved" if source != dest else "retain_existing")
            details[dest] = result
        results[channel] = details
    dispositions = [row for article in results.values() for row in article.values()]
    if len(dispositions) != 18:
        raise Blocked("triage_expected_eighteen_reviewed_slots")
    report = {"schema": "editorial-saved-image-triage-v1", "source_audit": plan["audit_sha256"],
              "results": results, "reviewed": len(dispositions), "preserved_previous_approvals": 5,
              "actions": dict(Counter(row.get("action", "unresolved") for row in dispositions)),
              "status_counts": dict(Counter(row["draft_status"] for row in dispositions)),
              "final_archive_ready": all(row["draft_status"] == "accepted" for row in dispositions),
              "klein_calls": 0, "gemini_calls": 0, "publication_authorized": False}
    write_json(output / "report.json", report)
    return report


def main():
    approval()
    if read_json(Path("config/site.json"))["publication_enabled"] is not False:
        raise Blocked("triage_requires_publication_freeze")
    with TemporaryDirectory(prefix="editorial-saved-image-triage-") as tmp:
        checkout = Path(tmp) / "private"
        git, env = connect(os.environ.get("EDITORIAL_PRIVATE_TOKEN"), checkout)
        base = checkout / "editorial-chat-review" / DAY
        report = review(base)
        args = [*git, "-C", str(checkout)]
        output = (base / OUTPUT).relative_to(checkout).as_posix()
        run([*args, "add", "--", output], env)
        if run([*args, "status", "--porcelain", "--", output], env):
            run([*args, "-c", "user.name=Zen Editorial Bot",
                 "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-m", "Reaudit saved images and stage only verified draft selections"], env)
            push_private_main(args, env)
        print(json.dumps({key: report[key] for key in ("reviewed", "preserved_previous_approvals", "actions",
                         "status_counts", "final_archive_ready", "klein_calls", "gemini_calls")}), flush=True)


if __name__ == "__main__":
    main()
