#!/usr/bin/env python3
"""Private, reproducible owner-review and ChatGPT-escalation packets.

Never sends, publishes, or uploads content. Produces an offline ZIP and a
separate manifest with SHA-256 of each payload. Both stay outside git.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from editorial_writer import private_path

ALLOWED = ("case.json", "draft.md", "draft.json", "reports.json", "sources.json",
           "writer-status.json")
REQUIRED = ("case.json", "draft.md", "reports.json")
SLUG = re.compile(r"^(legends|science)-draft-[a-f0-9]{16}$")


def validate_case(folder):
    if not SLUG.fullmatch(folder.name) or folder.is_symlink():
        raise ValueError("Only canonical private legends/science draft directories")
    files = {}
    for name in ALLOWED:
        path = folder / name
        if path.is_symlink():
            raise ValueError("Symlinks are forbidden in owner packets")
        if path.is_file():
            files[name] = path.read_bytes()
    if not all(name in files for name in REQUIRED):
        raise ValueError("Case, draft and reports are required")
    case = json.loads(files["case.json"])
    reports = json.loads(files["reports.json"])
    versions = case["versions"]
    if not isinstance(versions, list) or not versions:
        raise ValueError("Empty editorial history")
    if case.get("publication_authorized") is True or case.get("rss_delivery_authorized") is True:
        raise ValueError("Release authorization cannot appear in review packets")
    hashes = {v["text_sha256"] for v in versions}
    digest = reports.get("draft_sha256")
    if digest not in hashes:
        raise ValueError("Report draft digest does not match an editorial version")
    if reports.get("decision") != case.get("decision"):
        raise ValueError("Stale handoff report")
    article = next(v["article"] for v in versions if v["text_sha256"] == digest)
    if article.get("status") != "draft" or article.get("publication_authorized") is True:
        raise ValueError("Article is not a held draft")
    if article.get("slug") != folder.name or article.get("channel") != folder.name.split("-")[0]:
        raise ValueError("Case slug or channel mismatch")
    from editorial_agent_v1 import article_digest
    if article_digest(article) != digest:
        raise ValueError("Digest mismatch in selected article")
    if not files["draft.md"].strip():
        raise ValueError("Empty readable draft")
    return files, case, reports


def make_packet(folder, output):
    folder = private_path(folder).resolve()
    output = private_path(output).resolve()
    if not folder.is_dir() or not folder.parent.exists():
        raise ValueError("Missing private case directory")
    if output.parent == folder or folder in output.parents:
        raise ValueError("Put the owner packet outside source case")
    files, case, reports = validate_case(folder)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    summary = {
        "schema": "zen-owner-review-packet-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "slug": folder.name,
        "decision": case.get("decision"),
        "selected_text_sha256": reports["draft_sha256"],
        "revision_count": len(case["versions"]) - 1,
        "manual_checks_pending": (case["versions"][-1].get("report", {})
                                  .get("manual_checks_pending", [])),
        "assistant_handoff": case.get("handoff"),
        "publication_authorized": False,
        "rss_delivery_authorized": False,
        "transport_status": "not_connected",
        "files": {name: {"sha256": sha256(blob).hexdigest(), "bytes": len(blob)}
                  for name, blob in files.items()}
    }
    guide = (
        "# На согласование владельцу — не опубликовано\n\n"
        f"Статья: {folder.name}\n"
        f"Статус: {case.get('decision')}\n"
        f"SHA-256 текста: {reports['draft_sha256']}\n\n"
        "Прочитайте draft.md, ознакомьтесь с reports.json, sources.json "
        "и историей версий в case.json. Отсутствующие результаты проверок "
        "не считать успешными. Право на изображения, достоверность источников "
        "и финальное одобрение требуют отдельного подтверждения.\n\n"
        "Этот ZIP не был автоматически отправлен ChatGPT, владельцу, в Дзен или RSS.\n"
    )
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("README_REVIEW.md", guide)
        archive.writestr("manifest.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        for name, blob in files.items():
            archive.writestr(name, blob)
    os.chmod(output, 0o600)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    try:
        result = make_packet(args.case_dir, args.output)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        ap.error(str(exc))
    print(json.dumps({"slug": result["slug"], "decision": result["decision"],
                      "archive": str(args.output), "transport_status": "not_connected"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
