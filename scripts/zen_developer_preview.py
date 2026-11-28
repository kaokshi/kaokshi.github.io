#!/usr/bin/env python3
"""Offline handoff validator. Never calls a model, pushes code, or runs owner text."""
import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = "kaokshi/kaokshi.github.io"
FLAGS = ("executionEnabled", "paidDispatchAuthorized", "writeAuthorized", "publicationAuthorized")

def validate_manifest(value):
    if not isinstance(value, dict) or value.get("schema") != "zen-developer/handoff-v1":
        raise ValueError("invalid_schema")
    ident = value.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9-]{36}", ident):
        raise ValueError("invalid_id")
    sha = value.get("baseSha")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("invalid_base")
    if value.get("repository") != REPO or value.get("branch") != "assistant/opus-task-" + ident:
        raise ValueError("invalid_target")
    if any(value.get(k) is not False for k in FLAGS):
        raise ValueError("authority_not_permitted")
    text = value.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 3000 or value.get("reasoning") not in ("low","medium","high"):
        raise ValueError("invalid_task")
    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("invalid_limits")
    caps = [limits.get(k) for k in ("taskKop","dailyKop","monthlyKop")]
    if any(type(v) is not int or not 100 <= v <= 150000 for v in caps) or caps != sorted(caps):
        raise ValueError("invalid_limits")
    if type(limits.get("policyVersion")) is not int or limits["policyVersion"] < 0:
        raise ValueError("invalid_policy_version")
    return {"schema":"zen-developer/preview-v1","id":ident,"baseSha":sha,
            "branch":value["branch"],"repository":REPO,"limits":limits,
            "status":"validated_preview_only","paidRequests":0,"gitWrites":0,
            "executed":False,"publicationAuthorized":False}

def preview(path, root):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = validate_manifest(data)
    sha = subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],check=True,capture_output=True,text=True).stdout.strip()
    if sha != result["baseSha"]:
        raise ValueError("checkout_pin_mismatch")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    print(json.dumps(preview(args.manifest,args.root), ensure_ascii=False))
