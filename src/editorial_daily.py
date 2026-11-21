"""Prepare one private draft per active channel for the current Moscow date."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from editorial_agent_v1 import ACTIVE
from editorial_writer import private_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", type=Path, default=Path(__file__).with_name("editorial_local_llm.py"))
    parser.add_argument("--generator-rights", default=os.getenv("ZEN_GENERATOR_RIGHTS"))
    parser.add_argument("--image-generator", default=os.getenv("ZEN_IMAGE_GENERATOR","http://127.0.0.1:7860"))
    args = parser.parse_args()
    root = private_path(args.output)
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
        statuses[ch] = subprocess.run(cmd, check=False).returncode
    print(json.dumps(statuses))
    return int(any(statuses.values()))


if __name__ == "__main__":
    sys.exit(main())
