#!/usr/bin/env python3
"""Create an entirely private review ZIP from the nine-topic daily plan.

Do not claim approval or delivery. Drafts missing a checked case stay missing;
never push the ZIP to a public GitHub artifact or repository.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from editorial_nine_draft_queue import validate_plan, report_status
from editorial_owner_packet import make_packet
from editorial_writer import private_path


def pack_day(plan, workspace, output):
    tasks=validate_plan(plan)
    workspace=private_path(Path(workspace))
    output=private_path(Path(output))
    if output.suffix.lower()!=".zip" or output.is_symlink():
        raise ValueError("A private ZIP output is required")
    if not workspace.is_dir() or not output.parent.exists():
        raise ValueError("Private workspace and output directory must exist")
    if output.exists():
        raise ValueError("Never overwrite an existing owner packet")
    if output.parent==workspace or workspace in output.parents:
        raise ValueError("Place combined packet outside source workspace")
    summary={"schema":"zen-nine-owner-packet-v1",
             "date":plan["date"],"created_at":datetime.now(timezone.utc).isoformat(),
             "requested":9,"included":0,"not_included":0,
             "transport_status":"not_connected","owner_approved":False,
             "publication_authorized":False,"rss_authorized":False,"tasks":[]}
    with TemporaryDirectory(prefix="zen-owner-archive-") as td:
        root=Path(td)
        with ZipFile(output,"w",compression=ZIP_DEFLATED) as archive:
            for item in tasks:
                row=report_status(workspace,item)
                record={"id":item["task_id"],"channel":item["channel"],
                        "status":row["status"],"ready_for_owner":bool(row["article_ready_for_owner"])}
                if not row["article_ready_for_owner"]:
                    record["included"]=False
                else:
                    try:
                        packed=root/(item["task_id"]+".zip")
                        make_packet(Path(row["case_dir"]),packed)
                        blob=packed.read_bytes()
                        archive.writestr("cases/"+item["task_id"]+".zip",blob)
                        record["included"]=True
                        record["archive_sha256"]=sha256(blob).hexdigest()
                        record["draft_sha256"]=row["draft_sha256"]
                        summary["included"]+=1
                    except (OSError,ValueError,TypeError,KeyError) as exc:
                        record["included"]=False
                        record["reason"]="packet_build_error_"+type(exc).__name__
                if not record["included"]:
                    summary["not_included"]+=1
                summary["tasks"].append(record)
            archive.writestr("MANIFEST.json",json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
            archive.writestr("README.md",(
                "# Девять статей — только для согласования\n\n"
                "Вложенные ZIP-файлы содержат приватные тексты и историю. "
                "Отсутствующие досье перечислены в MANIFEST.json, их нельзя "
                "считать готовыми. Этот архив не отправлен ChatGPT или владельцу "
                "автоматически. Никакая публикация или RSS не разрешены.\n"
            ))
    os.chmod(output,0o600)
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--plan",type=Path,required=True)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    try:
        plan=json.loads(args.plan.read_text(encoding="utf-8"))
        result=pack_day(plan,args.workspace,args.output)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        p.error(str(exc))
    print(json.dumps({"requested":result["requested"],
                      "included":result["included"],
                      "not_included":result["not_included"],
                      "transport_status":result["transport_status"],
                      "publication_authorized":False},ensure_ascii=False))


if __name__=="__main__":
    main()
