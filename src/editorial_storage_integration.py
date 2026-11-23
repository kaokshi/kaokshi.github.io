"""Exercise real concurrent GitHub writes with synthetic files on a disposable private ref."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Barrier

from .editorial_chat_plaintext import approval, connect, push_private_main, run
from .editorial_memory import digest
from .editorial_providers import Blocked


def main():
    approval()
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    branch = f"codex/v1-storage-probe-{run_id}-{attempt}"
    if not re.fullmatch(r"codex/v1-storage-probe-[0-9]+-[0-9]+", branch):
        raise Blocked("invalid_private_probe_branch")
    token = os.environ["EDITORIAL_PRIVATE_TOKEN"]
    payloads = {
        "image.bin": b"synthetic candidate bytes\n",
        "audit.json": b'{"fixture": "synthetic audit"}\n',
        "package.txt": b"synthetic package operation\n",
    }
    folder = ".editorial-v1-storage-probe"
    with TemporaryDirectory(prefix="editorial-storage-integration-") as tmp:
        root = Path(tmp)
        anchor = root / "anchor"
        git, env = connect(token, anchor)
        anchor_args = [*git, "-C", str(anchor)]
        run([*anchor_args, "push", "origin", f"HEAD:refs/heads/{branch}"], env)
        try:
            prepared = {}
            for name, body in payloads.items():
                checkout = root / name
                clone_args, clone_env = connect(token, checkout)
                args = [*clone_args, "-C", str(checkout)]
                run([*args, "fetch", "origin", f"refs/heads/{branch}"], clone_env)
                run([*args, "checkout", "--detach", "FETCH_HEAD"], clone_env)
                path = checkout / folder / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(body)
                run([*args, "add", "--", f"{folder}/{name}"], clone_env)
                commit(args, clone_env)
                prepared[name] = (args, clone_env)

            barrier = Barrier(3)

            def upload(item):
                args, clone_env = item
                barrier.wait(timeout=15)
                return push_private_main(args, clone_env, branch=branch)

            with ThreadPoolExecutor(max_workers=3) as pool:
                heads = list(pool.map(upload, prepared.values()))
            run([*anchor_args, "fetch", "origin", f"refs/heads/{branch}"], env)
            final = {}
            for name, body in payloads.items():
                blob = run([*anchor_args, "show", f"FETCH_HEAD:{folder}/{name}"], env)
                if blob != body.decode().strip():
                    raise Blocked("private_probe_lost_parallel_write")
                final[name] = digest(body)

            # A deliberately stale clone tries to create a different file at
            # the same path. The conflict must preserve the first writer.
            stale = root / "stale"
            args, stale_env = connect(token, stale)
            args = [*args, "-C", str(stale)]
            path = stale / folder / "image.bin"
            path.parent.mkdir()
            path.write_bytes(b"fixture stale overwrite\n")
            run([*args, "add", "--", f"{folder}/image.bin"], stale_env)
            commit(args, stale_env)
            try:
                push_private_main(args, stale_env, branch=branch)
            except Blocked as exc:
                if str(exc) != "private_review_write_conflict_pending":
                    raise
            else:
                raise Blocked("private_probe_stale_write_was_not_refused")
            run([*anchor_args, "fetch", "origin", f"refs/heads/{branch}"], env)
            if run([*anchor_args, "show", f"FETCH_HEAD:{folder}/image.bin"], env) != payloads["image.bin"].decode().strip():
                raise Blocked("private_probe_verified_image_changed")
            print(json.dumps({"parallel_operations_preserved": len(final), "stale_overwrite_blocked": True,
                              "distinct_commits": len(set(heads)), "provider_calls": 0}), flush=True)
        finally:
            run([*anchor_args, "push", "origin", f":refs/heads/{branch}"], env)


def commit(args, env):
    run([*args, "-c", "user.name=Editorial Integration",
         "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
         "commit", "-m", "Synthetic private storage integration fixture"], env)


if __name__ == "__main__":
    main()
