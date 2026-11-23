"""Independent owner-private encrypted checkpoint lane for the V1 validation."""

import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .editorial_chat_plaintext import approval, checked_day, connect, push_private_main, run
from .editorial_delivery import bundle_state, restore_blob, seal_state
from .editorial_memory import StorageError, write
from .editorial_providers import Blocked


class ReleaseCheckpoint:
    def __init__(self, checkout, token, run_id):
        approval()
        if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id or ""):
            raise Blocked("invalid_release_checkpoint_run")
        self.git, self.env = connect(token, checkout)
        self.checkout = checkout
        self.args = [*self.git, "-C", str(checkout)]
        self.run_id = run_id

    def path(self, day):
        return self.checkout / "editorial-v1-checkpoints" / self.run_id / checked_day(day) / "checkpoint.enc"

    def restore(self, state, day):
        self.state, self.day = state, day
        blob = self.path(day)
        if not blob.is_file():
            return False
        if (state / day / "state.json").is_file():
            raise Blocked("release_checkpoint_already_restored_or_conflicting")
        restore_blob(blob.read_bytes(), state, day)
        return True

    def checkpoint(self):
        blob = self.path(self.day)
        rel = blob.relative_to(self.checkout).as_posix()
        try:
            write(blob, seal_state(bundle_state(self.state, self.day)))
            run([*self.args, "add", "--", rel], self.env)
            run([*self.args, "-c", "user.name=Zen Editorial Bot",
                 "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-m", "Persist isolated editorial checkpoint"], self.env)
            push_private_main(self.args, self.env)
        except (Blocked, OSError) as exc:
            # Memory has already persisted the reservation or response locally.
            # The current runner may retry storage, never the provider call.
            raise StorageError("release_checkpoint_durable_write_pending") from exc


def restore_private_checkpoint(root, day, run_id):
    token = os.environ.get("EDITORIAL_V1_CHECKPOINT_TOKEN")
    if not token:
        return False
    with TemporaryDirectory(prefix="zen-v1-checkpoint-restore-") as work:
        return ReleaseCheckpoint(Path(work) / "private", token, run_id).restore(root, day)
