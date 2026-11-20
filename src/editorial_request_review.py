#!/usr/bin/env python3
"""Fetch the newest genuinely saved, encrypted Zen owner packet on demand.

Requires user's own GitHub CLI auth (gh) and separately held X25519 private key.
No article text or credentials are printed to GitHub issue/PR/run logs.
Does not schedule generation, send messages, or publish articles.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import x25519
from editorial_encrypted_review import decrypt

REPO="kaokshi/kaokshi.github.io"
ARTIFACT="nine-real-qwen-owner-review-encrypted"
WORKFLOW="real-nine-qwen-e2e.yml"


def gh(args, *, runner=subprocess.run):
    proc=runner(["gh",*args],capture_output=True,text=True,encoding="utf-8",
                timeout=120,check=False)
    if proc.returncode:
        raise RuntimeError("GitHub CLI command failed (check gh auth and artifact access)")
    return proc.stdout


def candidate_runs(repo, *, command=gh):
    payload=json.loads(command(["run","list","--repo",repo,
                               "--workflow",WORKFLOW,
                               "--limit","100",
                               "--json","databaseId,status,conclusion,createdAt"]))
    return [str(row["databaseId"]) for row in payload
            if row.get("status")=="completed" and row.get("databaseId")]


def safely_extract_plaintext(plaintext, output):
    output=Path(output)
    if output.exists():
        raise ValueError("Private output already exists; never overwrite")
    with ZipFile(io.BytesIO(plaintext)) as archive:
        files=archive.namelist()
        if not files or "MANIFEST.json" not in files:
            raise ValueError("Missing verified article manifest")
        if any(name.startswith("/") or
               ".." in Path(name).parts or
               not name.startswith(("articles/","MANIFEST.json","README.txt"))
               for name in files):
            raise ValueError("Unsafe archive entry")
        manifest=json.loads(archive.read("MANIFEST.json"))
        if manifest.get("publication_authorized") or manifest.get("rss_authorized"):
            raise ValueError("Unexpected publishing flags in review")
        if not isinstance(manifest.get("articles"),list):
            raise ValueError("Invalid review manifest")
        output.mkdir(parents=True,mode=0o700)
        for name in files:
            dest=output/name
            if name.endswith("/"):
                dest.mkdir(parents=True,exist_ok=True)
                continue
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(archive.read(name))
            dest.chmod(0o600)
        return manifest


def fetch_latest_review(private_key, output, repo=REPO, *, command=gh):
    pem=Path(private_key).read_bytes()
    key=load_pem_private_key(pem,password=None)
    if not isinstance(key,x25519.X25519PrivateKey):
        raise ValueError("Expected owner's X25519 private PEM")
    for run_id in candidate_runs(repo,command=command):
        with TemporaryDirectory(prefix="zen-encrypted-review-") as tmp:
            try:
                command(["run","download",run_id,"--repo",repo,
                         "--name",ARTIFACT,"--dir",tmp])
            except RuntimeError:
                continue
            candidates=list(Path(tmp).rglob("*.enc"))
            if len(candidates)!=1:
                continue
            try:
                plain=decrypt(candidates[0].read_bytes(),key)
                manifest=safely_extract_plaintext(plain,output)
            except (ValueError,KeyError):
                raise
            except Exception as exc:
                raise ValueError("Decryption failed: check owner key") from exc
            return {"run_id":run_id,"articles":len(manifest["articles"]),
                    "output":str(Path(output).resolve()),"transport":"local_private_files"}
    raise FileNotFoundError("No completed GitHub Action with a downloadable encrypted review archive")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner-key",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    try:
        result=fetch_latest_review(args.owner_key,args.output)
    except (OSError,RuntimeError,ValueError) as exc:
        p.error(f"Cannot retrieve article review: {type(exc).__name__}: {exc}")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
