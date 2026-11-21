"""Prepare one private draft per active channel for the current Moscow date."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from editorial_agent_v1 import ACTIVE
from editorial_writer import private_path
from editorial_revision import save_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", type=Path, default=Path(__file__).with_name("editorial_local_llm.py"))
    parser.add_argument("--generator-rights", default=os.getenv("ZEN_GENERATOR_RIGHTS"))
    parser.add_argument("--image-generator", default=os.getenv("ZEN_IMAGE_GENERATOR","http://127.0.0.1:7860"))
    args = parser.parse_args()
    root = private_path(args.output)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    statuses = {}
    for ch in ACTIVE:
        cmd = [sys.executable, str(Path(__file__).with_name("editorial_pipeline.py")), "--channel", ch,
               "--auto-topic", "--output", str(root), "--asset-root", str(root), "--provider", str(args.provider),
               "--produce-media"]
        if ch == "science":
            cmd.append("--europe-pmc")
        else:
            cmd.extend(["--image-generator",args.image_generator])
            if args.generator_rights:
                cmd.extend(["--generator-rights",args.generator_rights])
        run = subprocess.run(cmd, capture_output=True, text=True, check=False)
        try:
            result = json.loads(run.stdout.strip().splitlines()[-1])
            status = result["status"]
        except (IndexError, KeyError, TypeError, ValueError):
            status = "pipeline_error"
        record = {"status": status, "exit_code": run.returncode, "stderr": run.stderr[-4000:]}
        if status == "pipeline_error" and run.stderr.strip():
            match = re.fullmatch(r"([a-z_]+_pending)(?:: .*)?", run.stderr.strip().splitlines()[-1])
            if match:
                record["status"] = match.group(1)
        save_case(root / f"daily-{day}-{ch}.json", record)
        statuses[ch] = {"status": record["status"], "exit_code": run.returncode}
    print(json.dumps(statuses))
    return int(any(row["exit_code"] or row["status"].endswith("_pending")
                   or row["status"] == "pipeline_error" for row in statuses.values()))


if __name__ == "__main__":
    sys.exit(main())
