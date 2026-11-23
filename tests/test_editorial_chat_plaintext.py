"""Synthetic-only tests for private owner-approved plaintext review bridge."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from src.editorial_memory import digest
from src.editorial_providers import Blocked
from src.editorial_chat_plaintext import (
    PRIVATE_REPO, approval, checked_day, cleanup, inputs, prune_directory, publish,
)


class PrivateChatReviewTests(unittest.TestCase):
    def fixture(self, root, day="2026-11-23"):
        folder = Path(root) / day / "review" / "science-01"
        folder.mkdir(parents=True)
        # Deliberately synthetic bytes; never a real draft, story or illustration.
        payloads = {
            "Статья.docx": b"TEST-FIXTURE-DOCX",
            "Статья.html": b"<p>TEST-FIXTURE-ONLY</p>",
            "Статья.md": b"TEST-FIXTURE-ONLY",
            "Текст для SHA-256.txt": b"TEST-FIXTURE-ONLY",
        }
        payloads["Статус и проверки.json"] = json.dumps({
            "text_sha256": digest(payloads["Текст для SHA-256.txt"]),
            "images": [{"file": "fixture.jpg", "sha256": digest(b"FAKE-IMAGE-TEST")}],
            "status": "partial_review_pending",
            "publication_authorized": False,
        }).encode()
        payloads["images/fixture.jpg"] = b"FAKE-IMAGE-TEST"
        # Historical versions are present in the original manifest, but must
        # NOT be copied to the chat-readable review.
        payloads["Версии/archived.txt"] = b"TEST-HISTORICAL-FIXTURE"
        for rel, data in payloads.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = "\n".join(
            f"{digest(blob)}  {name}" for name, blob in sorted(payloads.items())
        ) + "\n"
        (folder / "SHA256SUMS.txt").write_text(manifest, encoding="utf-8")
        return folder

    def test_explicit_owner_authorization_and_freeze(self):
        c = approval()
        self.assertTrue(c["owner_approved"])
        self.assertEqual(c["repository"], PRIVATE_REPO)
        self.assertEqual(c["retention_days"], 7)
        site = json.loads(Path("config/site.json").read_text(encoding="utf-8"))
        production = json.loads(Path("config/editorial_production.json").read_text(encoding="utf-8"))
        self.assertFalse(site["publication_enabled"])
        self.assertFalse(production["schedule"]["enabled"])

    def test_only_verified_agent_review_files_selected(self):
        with TemporaryDirectory() as root:
            folder = self.fixture(root)
            batch = inputs(root, "2026-11-23")
            self.assertEqual(len(batch), 1)
            self.assertEqual(batch[0][0], "science-01")
            selected = [p.relative_to(folder).as_posix() for p in batch[0][2]]
            self.assertIn("images/fixture.jpg", selected)
            self.assertNotIn("Версии/archived.txt", selected)
            self.assertNotIn("state.json", selected)

    def test_corrupt_manifest_blocks_publication_to_private_repo(self):
        with TemporaryDirectory() as root:
            folder = self.fixture(root)
            (folder / "Статья.html").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(Blocked, "manifest_mismatch"):
                inputs(root, "2026-11-23")

    def test_publish_copies_only_agent_fixture_and_checks_private_connection(self):
        with TemporaryDirectory() as root:
            self.fixture(root)
            capture = {}
            def fake_connect(token, destination, execute):
                self.assertEqual(token, "FAKE-NOT-REAL")
                destination.mkdir()
                return ["git"], {}
            def fake_run(argv, env, timeout=60):
                if "status" in argv:
                    return " M editorial-chat-review/2026-11-23/science-01/Статья.html"
                if "push" in argv:
                    repo = Path(argv[argv.index("-C")+1])
                    output = repo / "editorial-chat-review" / "2026-11-23" / "science-01"
                    capture["text"] = (output / "Статья.html").read_text()
                    capture["has_history"] = (output / "Версии").exists()
                    capture["image"] = (output / "images" / "fixture.jpg").read_bytes()
                return ""
            with patch("src.editorial_chat_plaintext.connect", fake_connect):
                result = publish(root, "2026-11-23", token="FAKE-NOT-REAL",
                                 execute=fake_run, now=date(2026, 11, 23))
            self.assertEqual(result["status"], "private_chat_review_available")
            self.assertEqual(result["channels"], ["science-01"])
            self.assertEqual(capture["text"], "<p>TEST-FIXTURE-ONLY</p>")
            self.assertFalse(capture["has_history"])
            self.assertEqual(capture["image"], b"FAKE-IMAGE-TEST")
            self.assertTrue(result["git_history_may_retain_deleted_content"])

    def test_retention_prunes_live_files_only_and_not_git_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day in ("2026-11-01", "2026-11-16", "2026-11-23"):
                (root / day).mkdir()
            removed = prune_directory(root, date(2026, 11, 23), 7)
            self.assertEqual(removed, ["2026-11-01"])
            self.assertTrue((root / "2026-11-16").exists())
            self.assertTrue((root / "2026-11-23").exists())

    def test_never_accept_invalid_dates(self):
        for value in ("2026-02-30", "../secret", "2026-11-23/../../foo"):
            with self.assertRaises(Blocked):
                checked_day(value)

    def test_empty_root_uploads_nothing(self):
        with TemporaryDirectory() as root:
            result = publish(root, "2026-11-23", token="FAKE-NOT-REAL")
            self.assertEqual(result["status"], "no_agent_review_files")


if __name__ == "__main__":
    unittest.main()
