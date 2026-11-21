"""Encrypted restic snapshots of one private production workspace."""
import argparse
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_SH, flock
import os
from pathlib import Path
import subprocess

from editorial_writer import private_path
from editorial_ai_check import ROOT


@contextmanager
def workspace_lock(root, *, exclusive):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".snapshot-lock").open("a") as lock:
        flock(lock, LOCK_EX if exclusive else LOCK_SH)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--snapshot", default="latest")
    args = parser.parse_args()
    root = private_path(args.directory)
    repo = os.environ.get("RESTIC_REPOSITORY", "")
    if not repo or not os.environ.get("RESTIC_PASSWORD_FILE"):
        parser.error("Set RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE")
    paths = [Path(os.environ["RESTIC_PASSWORD_FILE"]).resolve()]
    if ":" not in repo or repo.startswith("local:"):
        paths.append(Path(repo.removeprefix("local:")).resolve())
    if any(p.is_relative_to(ROOT.resolve()) for p in paths):
        parser.error("Keep the password and local vault outside the public checkout")
    if any(p.is_relative_to(root) or root.is_relative_to(p) for p in paths):
        parser.error("Keep the workspace, vault and password file separate")
    if args.operation == "restore":
        if root.exists() and any(root.iterdir()):
            parser.error("Restore into an empty private directory")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        subprocess.run(["restic", "restore", args.snapshot, "--tag", "newsroom",
                        "--target", str(root), "--verify"], check=True)
        return
    if not (root / "editorial-memory.sqlite3").is_file():
        parser.error("Expected an initialized editorial workspace")
    with workspace_lock(root, exclusive=True):
        subprocess.run(["restic", "backup", ".", "--tag", "newsroom", "--quiet"], cwd=root, check=True)


if __name__ == "__main__":
    main()
