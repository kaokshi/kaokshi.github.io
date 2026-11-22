"""Resume and receive an encrypted editorial checkpoint in independent processes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from docx import Document
from PIL import Image

from src.editorial_brain import Memory, review, settings
from src.editorial_delivery import restore_blob
from src.editorial_memory import digest
from src.editorial_package import ArticleHTML, package
from src.editorial_providers import Blocked
from src.editorial_review import canonical, parse_article


class StorageTests(unittest.TestCase):
    def test_pack_restore_and_open_after_original_workspace_removed(self):
        day = "2026-11-22"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original, encrypted, restored, received = (root / name for name in
                                                       ("runner-a", "sealed", "runner-b", "owner"))
            memory = Memory(original, day, datetime.now(timezone.utc), settings())
            image_folder = original / "images"
            image_folder.mkdir(parents=True)
            article = parse_article("**Проверка восстановления**\nТекст с иллюстрацией. Подпишитесь.")
            expected_sha = digest(canonical(article))
            for article_id in ("legends-01", "science-01"):
                images = []
                for i in range(7):
                    name = f"{i}.jpg"
                    file = image_folder / name
                    Image.new("RGB", (80, 60), (i * 20, 70, 90)).save(file)
                    images.append({"file": name, "section": 1, "cover_score": i,
                                   "scene": "Тестовая иллюстрация", "kind": "generated",
                                   "sha256": digest(file.read_bytes())})
                package(memory.folder / "review" / article_id, article_id, [],
                        {"article": article, "text_sha256": expected_sha, "facts": {"evidence": []}},
                        images, image_folder, {"status": "rights_review_pending"})
                memory.data["articles"][article_id] = {"status": "rights_review_pending"}
            review(memory)
            key = x25519.X25519PrivateKey.generate()
            pem = root / "owner.pem"
            pem.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            env = {**os.environ, "EDITORIAL_REVIEW_PUBLIC_KEY":
                   key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
                   "EDITORIAL_STATE_KEY_HEX": os.urandom(32).hex()}
            self.invoke("seal", "--date", day, "--root", original, "--output", encrypted, env=env)
            import shutil
            shutil.rmtree(original)
            program = ("from pathlib import Path; import sys; from src.editorial_delivery import restore_blob;"
                       "restore_blob(Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]), sys.argv[3])")
            subprocess.run([sys.executable, "-c", program, str(encrypted / "checkpoint.enc"),
                            str(restored), day], env=env, capture_output=True, check=True, text=True)
            packet = restored / day / "Комплект согласования.zip"
            self.assertEqual(digest(packet.read_bytes()), packet.with_suffix(".sha256").read_text().strip())
            restored_output = root / "reissued"
            self.invoke("seal", "--date", day, "--root", restored, "--output", restored_output, env=env)
            response = self.invoke("retrieve", "--date", day, "--private-key", pem, "--output", received,
                                   "--artifact", restored_output / "owner-review.enc", env=env)
            self.assertEqual(response["status"], "verified_files_available")
            self.assertEqual(response["package_sha256"], digest(packet.read_bytes()))
            for article_id in ("legends-01", "science-01"):
                folder = received / article_id
                document = Document(folder / "Статья.docx")
                self.assertEqual(len(document.inline_shapes), 7)
                html = ArticleHTML()
                html.feed((folder / "Статья.html").read_text())
                self.assertEqual(digest("\n".join(html.lines)), expected_sha)
                self.assertTrue(all((folder / image).is_file() for image in html.images))
            wrong = root / "wrong"
            with patch.dict(os.environ, {"EDITORIAL_STATE_KEY_HEX": os.urandom(32).hex()}):
                with self.assertRaisesRegex(Blocked, "checkpoint_authentication_failed"):
                    restore_blob((encrypted / "checkpoint.enc").read_bytes(), wrong, day)
            self.assertFalse(wrong.exists())

    def invoke(self, *args, env):
        result = subprocess.run([sys.executable, "-m", "src.editorial_delivery", *map(str, args)],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)
