"""Check ciphertext recovery and owner delivery without provider calls."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
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
from src.editorial_delivery import restore_blob, retrieve, save_outputs
from src.editorial_groq_budget import snapshot
from src.editorial_groq_ledger import archive_document, collection
from src.editorial_memory import digest, read_json, write_json
from src.editorial_package import ArticleHTML, package
from src.editorial_providers import Blocked
from src.editorial_review import canonical, parse_article


DAY = "2026-11-22"
FIXTURE = parse_article("**Проверка восстановления**\nКонтрольный текст. Подпишитесь.")
ARTICLE_IDS = ("legends-01", "science-01")


def build_fixture(root):
    memory = Memory(root, DAY, datetime.now(timezone.utc), settings())
    image_folder = root / "images"
    image_folder.mkdir(parents=True)
    for article_id in ARTICLE_IDS:
        images = []
        for i in range(7):
            name = f"{i}.jpg"
            file = image_folder / name
            Image.new("RGB", (80, 60), (i * 20, 70, 90)).save(file)
            images.append({"file": name, "section": 1, "cover_score": i,
                           "scene": "Контрольная иллюстрация", "kind": "generated",
                           "sha256": digest(file.read_bytes())})
        package(memory.folder / "review" / article_id, article_id, [],
                {"article": FIXTURE, "text_sha256": digest(canonical(FIXTURE)), "facts": {"evidence": []}},
                images, image_folder, {"status": "rights_review_pending"})
        memory.data["articles"][article_id] = {"status": "rights_review_pending"}
    review(memory)


def write_owner_key(path, key):
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    path.chmod(0o600)


def fixture_bindings(key, symmetric):
    return {"EDITORIAL_REVIEW_PUBLIC_KEY": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
            "EDITORIAL_STATE_KEY_HEX": symmetric.hex()}


def assert_opened(root, expected_sha):
    for article_id in ARTICLE_IDS:
        folder = root / article_id
        document = Document(folder / "Статья.docx")
        if len(document.inline_shapes) != 7:
            raise ValueError("fixture_docx_images_missing")
        html = ArticleHTML()
        html.feed((folder / "Статья.html").read_text(encoding="utf-8"))
        if digest("\n".join(html.lines)) != expected_sha or not all(
                (folder / image).is_file() for image in html.images):
            raise ValueError("fixture_html_images_or_sha_mismatch")


def validate_restored(encrypted, workspace, key):
    restored = workspace / "restored"
    restore_blob((encrypted / "checkpoint.enc").read_bytes(), restored, DAY)
    packet = restored / DAY / "Комплект согласования.zip"
    expected = packet.with_suffix(".sha256").read_text(encoding="utf-8").strip()
    if digest(packet.read_bytes()) != expected:
        raise ValueError("fixture_checkpoint_packet_sha_mismatch")
    repacked = workspace / "reissued"
    save_outputs(restored, DAY, repacked)
    pem = workspace / "owner.pem"
    write_owner_key(pem, key)
    received = workspace / "owner"
    result = retrieve(received, pem, day=DAY, artifact=repacked / "owner-review.enc")
    if result["status"] != "verified_files_available" or result["package_sha256"] != expected:
        raise ValueError("fixture_receipt_mismatch")
    assert_opened(received, digest(canonical(FIXTURE)))
    return {"status": "fixture_verified_and_opened", "articles": len(ARTICLE_IDS), "images": 14}


class StorageTests(unittest.TestCase):
    def test_pack_restore_and_open_after_original_workspace_removed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original, encrypted = root / "runner-a", root / "sealed"
            build_fixture(original)
            key = x25519.X25519PrivateKey.generate()
            bindings = fixture_bindings(key, os.urandom(32))
            env = {**os.environ, **bindings}
            proc = subprocess.run([sys.executable, "-m", "src.editorial_delivery", "seal",
                                   "--date", DAY, "--root", str(original), "--output", str(encrypted)],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            shutil.rmtree(original)
            with patch.dict(os.environ, bindings):
                self.assertEqual(validate_restored(encrypted, root / "runner-b", key)["images"], 14)
            with patch.dict(os.environ, {"EDITORIAL_STATE_KEY_HEX": os.urandom(32).hex()}):
                with self.assertRaisesRegex(Blocked, "checkpoint_authentication_failed"):
                    restore_blob((encrypted / "checkpoint.enc").read_bytes(), root / "wrong", DAY)
            self.assertFalse((root / "wrong").exists())


def write_quota_fixture(out, run):
    now = datetime.now(timezone.utc)
    base = {"lane": "production", "at": now.isoformat()}
    docs = [archive_document("reservation", run, [{**base, "request_id": "a" * 32,
            "status": "run_reservation", "estimated_total_tokens": 50000, "estimated_requests": 34}]),
            archive_document("usage", run, [{**base, "request_id": "b" * 32,
            "status": "responded", "total_tokens": 6280, "input_tokens": 3000, "output_tokens": 3280}])]
    write_json(out / "groq-synthetic.json", docs)


def check_quota_fixture(source):
    documents = read_json(source / "groq-synthetic.json")
    now = datetime.fromisoformat(documents[0]["rows"][0]["at"])
    before = snapshot(collection(documents[:1]), now)
    after = snapshot(collection(documents + documents), now)
    if before["accounted_tokens"] != 50000 or after["known_tokens"] != 6280 or after["requests"] != 1:
        raise ValueError("fixture_groq_settlement_mismatch")
    return {"fixture_groq_reserved_tokens": before["accounted_tokens"],
            "fixture_groq_settled_tokens": after["known_tokens"]}


def run_ci_fixture(command, encrypted):
    """Public, reproducible fixture credentials apply exclusively to the synthetic texts above."""
    run = os.environ["GITHUB_RUN_ID"].encode()
    key = x25519.X25519PrivateKey.from_private_bytes(sha256(b"public-test-recipient/" + run).digest())
    symmetric = sha256(b"public-test-checkpoint/" + run).digest()
    with patch.dict(os.environ, fixture_bindings(key, symmetric)), TemporaryDirectory() as work:
        workspace = Path(work)
        if command == "seal":
            root = workspace / "synthetic-state"
            build_fixture(root)
            result = save_outputs(root, DAY, encrypted)
            write_quota_fixture(encrypted, os.environ["GITHUB_RUN_ID"] + "." + os.environ["GITHUB_RUN_ATTEMPT"])
        elif command == "restore":
            result = {**validate_restored(encrypted, workspace, key), **check_quota_fixture(encrypted)}
        else:
            raise ValueError("unknown_fixture_command")
        print(json.dumps(result))


if __name__ == "__main__":
    run_ci_fixture(sys.argv[1], Path(sys.argv[2]))
