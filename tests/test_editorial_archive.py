"""Protect the read-only duplicate index across the provider retirement."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src import editorial_archive
from src.editorial_memory import write_json


class ArchiveTests(unittest.TestCase):
    def test_public_assets_and_legacy_checkpoints_remain_indexed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = root / "content/articles"
            content.mkdir(parents=True)
            image = content / "accepted.jpg"
            image.write_bytes(b"fixture")
            outside = root / "outside.jpg"
            outside.write_bytes(b"fixture")
            write_json(content / "public.json", {"title": "Public fixture", "channel": "cinema",
                       "body_html": "<p>Recorded public text</p>",
                       "image_files": [str(image.relative_to(root)), outside.name]})
            state = root / "archive"
            previous = state / "2026-11-22"
            current = state / "2026-11-23"
            write_json(previous / "state.json", {"articles": {"legends-01": {
                "title": "Private fixture", "text_sha256": "saved"}}})
            write_json(current / "state.json", {"articles": {"science-01": {"title": "Current fixture"}}})
            source = previous / "legends-01/versions/saved.txt"
            source.parent.mkdir(parents=True)
            source.write_text("Recorded private text", encoding="utf-8")
            with patch.multiple(editorial_archive, ROOT=root, CONTENT=content):
                titles, prose, assets = editorial_archive.archive(state, exclude_day=current.name)
            self.assertEqual(titles, {"legends": ["Private fixture"], "science": [], "cinema": ["Public fixture"]})
            self.assertEqual([title for title, _ in prose], ["Public fixture", "Private fixture"])
            self.assertEqual(assets, {image})
