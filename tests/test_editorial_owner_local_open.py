"""Offline regression for owner-side decryption of a real-article style bundle."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from src.editorial_encrypted_review import encrypt
from tools import open_truman_private_review as opener


def sha(data):
    return sha256(data).hexdigest()


def fixture_archive():
    files = {
        "Статья.docx": b"synthetic word data",
        "Статья.html": b"<p>synthetic test only</p>",
        "Текст для SHA-256.txt": b"synthetic test only",
    }
    files.update({"images/%02d.jpg" % n: b"jpg-test-" + bytes([n]) for n in range(1, 9)})
    checks = {"storage_only": True, "publication_authorized": False,
              "text_sha256": sha(files["Текст для SHA-256.txt"]),
              "images": [{"file": "%02d.jpg" % n, "sha256": sha(files["images/%02d.jpg" % n])}
                         for n in range(1, 9)]}
    files["Статус и проверки.json"] = json.dumps(checks).encode()
    buffer = BytesIO()
    with ZipFile(buffer, "w") as z:
        z.writestr("Статус комплекта.json", json.dumps({
            "day": "2026-11-23", "articles": {"cinema-01": "rights_review_pending"},
            "publication_authorized": False}).encode())
        for name, raw in files.items():
            z.writestr("cinema-01/" + name, raw)
        manifest = "".join(sha(raw) + "  " + name + "\n" for name, raw in sorted(files.items()))
        z.writestr("cinema-01/SHA256SUMS.txt", manifest)
    return buffer.getvalue()


class LocalOwnerOpenTests(unittest.TestCase):
    def test_owner_can_open_complete_synthetic_package_offline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            secret = X25519PrivateKey.generate()
            public = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            pem = root / "owner.pem"
            pem.write_bytes(secret.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            archive = fixture_archive()
            ciphertext = encrypt(archive, public)
            encrypted = root / "review.enc"
            encrypted.write_bytes(ciphertext)
            with patch.object(opener, "EXPECTED_ENCRYPTED_SHA", sha(ciphertext)), \
                 patch.object(opener, "EXPECTED_ARCHIVE_SHA", sha(archive)):
                opener.open_owner_package(encrypted, pem, root / "opened")
            self.assertTrue((root / "opened" / "cinema-01" / "Статья.docx").is_file())
            self.assertEqual(len(list((root / "opened" / "cinema-01" / "images").glob("*.jpg"))), 8)

    def test_wrong_private_key_does_not_create_output(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            recipient = X25519PrivateKey.generate()
            pub = recipient.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            wrong = X25519PrivateKey.generate()
            pem = root / "wrong.pem"
            pem.write_bytes(wrong.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            archive = fixture_archive()
            ciphertext = encrypt(archive, pub)
            encrypted = root / "review.enc"
            encrypted.write_bytes(ciphertext)
            with patch.object(opener, "EXPECTED_ENCRYPTED_SHA", sha(ciphertext)), \
                 patch.object(opener, "EXPECTED_ARCHIVE_SHA", sha(archive)):
                with self.assertRaises(Exception):
                    opener.open_owner_package(encrypted, pem, root / "opened")
            self.assertFalse((root / "opened").exists())


if __name__ == "__main__":
    unittest.main()
