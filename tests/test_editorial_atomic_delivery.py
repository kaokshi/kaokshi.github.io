"""Owner package retrieval must never leave a misleading partial final folder."""
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.editorial_delivery import publish_verified_directory, unpack
from src.editorial_http import Blocked


class AtomicOwnerDeliveryTests(unittest.TestCase):
    def test_complete_verified_directory_appears_with_all_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "Статья.html").write_text("проверено", encoding="utf-8")
            (source / "images").mkdir()
            (source / "images" / "1.jpg").write_bytes(b"image")
            final = root / "output" / "approved"
            publish_verified_directory(source, final)
            self.assertEqual((final / "Статья.html").read_text(encoding="utf-8"), "проверено")
            self.assertEqual((final / "images" / "1.jpg").read_bytes(), b"image")
            self.assertFalse(any(final.parent.glob(".zen-owner-stage-*")))

    def test_interrupted_copy_does_not_poison_final_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "Статья.html").write_text("исходник", encoding="utf-8")
            final = root / "output" / "approved"

            def interrupted_copy(src, dst):
                dst.mkdir()
                (dst / "incomplete.txt").write_text("partial", encoding="utf-8")
                raise OSError("simulated copy failure")

            with patch("src.editorial_delivery.shutil.copytree", side_effect=interrupted_copy):
                with self.assertRaises(OSError):
                    publish_verified_directory(source, final)
            self.assertFalse(final.exists())
            self.assertFalse(any(final.parent.glob(".zen-owner-stage-*")))
            publish_verified_directory(source, final)
            self.assertEqual((final / "Статья.html").read_text(encoding="utf-8"), "исходник")

    def test_corrupt_late_archive_member_never_changes_existing_state(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "state"
            destination.mkdir()
            (destination / "state.json").write_text("original", encoding="utf-8")

            class Entry:
                def __init__(self, name):
                    self.filename = name

            class CorruptArchive:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def infolist(self):
                    return [Entry("state.json"), Entry("last.bin")]

                def read(self, entry):
                    if entry.filename == "last.bin":
                        raise OSError("corrupt late archive member")
                    return b"new state that must not be written"

            with patch("src.editorial_delivery.safe_zip", return_value=CorruptArchive()):
                with self.assertRaisesRegex(OSError, "corrupt late archive member"):
                    unpack(b"fixture", destination)
            self.assertEqual((destination / "state.json").read_text(encoding="utf-8"), "original")
            self.assertFalse((destination / "last.bin").exists())

    def test_conflicting_zip_paths_rejected_before_any_write(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "restored"
            buffer = BytesIO()
            with ZipFile(buffer, "w") as archive:
                archive.writestr("article", "looks like a file")
                archive.writestr("article/images/1.jpg", "image")
            with self.assertRaisesRegex(Blocked, "unsafe_encrypted_archive"):
                unpack(buffer.getvalue(), destination)
            self.assertFalse((destination / "article").exists())

    def test_restore_does_not_follow_symlinked_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "state"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            (destination / "2026-11-23").symlink_to(outside, target_is_directory=True)
            buffer = BytesIO()
            with ZipFile(buffer, "w") as archive:
                archive.writestr("2026-11-23/state.json", '{"day":"2026-11-23"}')
            with self.assertRaisesRegex(Blocked, "unsafe_restore_symlink"):
                unpack(buffer.getvalue(), destination)
            self.assertFalse((outside / "state.json").exists())

    def test_restore_does_not_follow_symlinked_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "state"
            destination.mkdir()
            outside = root / "secret"
            outside.write_text("keep", encoding="utf-8")
            (destination / "state.json").symlink_to(outside)
            buffer = BytesIO()
            with ZipFile(buffer, "w") as archive:
                archive.writestr("state.json", "overwrite")
            with self.assertRaisesRegex(Blocked, "unsafe_restore_symlink"):
                unpack(buffer.getvalue(), destination)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_existing_output_is_never_overwritten(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            final = root / "owner-package"
            final.mkdir()
            (final / "existing.txt").write_text("preserve")
            with self.assertRaisesRegex(Blocked, "retrieval_output_already_exists"):
                publish_verified_directory(source, final)
            self.assertEqual((final / "existing.txt").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
