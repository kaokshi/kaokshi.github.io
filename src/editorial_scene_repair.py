"""Single owner-private end-to-end repair of an audited, rejected scene."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import editorial_scene_qa as qa
from .editorial_brain import json_reply, settings
from .editorial_chat_plaintext import approval, connect, run
from .editorial_images import ImageIndex, generated
from .editorial_memory import Memory, digest, read_json, write_json
from .editorial_offline_review_pack import FILE_NAME
from .editorial_providers import Blocked, text_call
from .editorial_scene_audit import input_sections

DAY = "2026-11-23"
ARTICLE = "legends-01"
SECTION = "7"


def main():
    approval()
    config = settings()
    with TemporaryDirectory(prefix="zen-audited-repair-") as work:
        root = Path(work) / "private"
        git, env = connect(os.environ.get("EDITORIAL_PRIVATE_TOKEN"), root)
        base = root / "editorial-chat-review" / DAY
        report = read_json(base / "image-qa-audit-v2" / "image_qa.json")
        if not report or report.get("schema") != qa.SCHEMA or sum(
                len(row["images"]) for row in report["articles"].values()) != 23:
            raise Blocked("repair_requires_completed_original_23_image_audit")
        articles = report["articles"]
        if any(not image.get("vision") for value in articles.values() for image in value["images"]):
            raise Blocked("repair_requires_completed_visual_audit")
        folder = base / ARTICLE
        sections, slots = input_sections((folder / FILE_NAME).read_text(encoding="utf-8"))
        source = digest("\n\n".join(sections.values()))
        original = next(row for row in articles[ARTICLE]["images"] if row["section_id"] == SECTION)
        section_text = sections[SECTION]
        slot = slots[SECTION]
        if (original.get("status") != "rejected" or original["source_text_sha256"] != source
                or original.get("slot") != slot or original["image_sha256"] != digest(
                    (folder / "illustrations" / f"{slot}.jpg").read_bytes())):
            raise Blocked("repair_target_has_no_current_rejection")
        output = base / "image-qa-targeted-repair"
        args = [*git, "-C", str(root)]

        def commit(message):
            rel = output.relative_to(root).as_posix()
            run([*args, "add", "--", rel], env)
            run([*args, "-c", "user.name=Zen Editorial Bot",
                 "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-m", message], env)
            run([*args, "push", "origin", "HEAD:refs/heads/main"], env, timeout=120)

        receipt = output / "result.json"
        if receipt.is_file():
            existing = read_json(receipt)
            print(json.dumps({"status": existing["status"], "klein_calls_this_run": 0}), flush=True)
            return
        request_record = output / "request.json"
        if request_record.is_file():
            raise Blocked("previous_targeted_repair_request_outcome_unknown")
        card = original["scene_card"]
        prompt = (
            "Уточни композицию одного нового кадра. Верни JSON с единственным полем visual_description. "
            "Опиши на английском видимое место, предметы и действия, не совмещая разные моменты. "
            "Строго соблюдай обязательные и запрещённые элементы проверки. "
            "Не добавляй деталей без подтверждения в тексте.\nПРОВЕРКА:\n"
            + json.dumps({key: card[key] for key in ("location", "must_have", "must_not_have")})
            + "\nРАЗДЕЛ:\n" + section_text
        )
        plan = text_call("groq", prompt, max_tokens=650)
        description = json_reply(plan).get("visual_description")
        if not isinstance(description, str) or not 20 <= len(description.strip()) <= 1100:
            raise Blocked("repair_composition_missing")
        write_json(request_record, {"article_id": ARTICLE, "slot": slot, "source_text_sha256": source,
                                    "prior_image_sha256": original["image_sha256"], "max_klein_calls": 1,
                                    "model": plan["model"], "usage": plan["usage"], "description": description})
        commit("Reserve one audited illustration correction")
        # This private test reserves one request before touching Klein, and
        # disallows a new allocation if a runner lost the request outcome.
        config["daily_calls"]["klein"] = 1
        config["per_article_calls"]["klein"] = 1
        memory = Memory(output / "checkpoint", DAY, datetime.now(timezone.utc) + timedelta(minutes=12), config)
        prior = sorted(base.glob("*/illustrations/[0-9][0-9].jpg"))
        index = ImageIndex(memory, prior)
        target = output / "images" / f"{slot}.jpg"
        scene = {"scene_card": {**card, "visual_description": description}, "section": SECTION,
                 "is_cover": False, "channel": "legends", "subject": card["location"], "kind": "illustration"}
        result = {"article_id": ARTICLE, "section_id": SECTION, "slot": slot,
                  "publication_authorized": False, "original_unchanged": True}
        try:
            row = generated(scene, card["location"], target, index, "historical-vision-repair",
                            ARTICLE, "legends")
        except Blocked as exc:
            result.update(status="image_review_required", reason=str(exc))
        else:
            result.update(status="accepted", image_sha256=row["sha256"], image_qa=row["image_qa"])
        result["klein_calls"] = sum(row["provider"] == "klein" for row in memory.data["calls"])
        write_json(receipt, result)
        commit("Save result of one audited Klein correction")
        print(json.dumps({"status": result["status"], "klein_calls": result["klein_calls"],
                          "reason": result.get("reason")}), flush=True)


if __name__ == "__main__":
    main()
