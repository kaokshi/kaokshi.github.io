"""Open the verified cinema-01 encrypted private review, locally and offline.

Requires: python -m pip install cryptography
Never upload owner_private_key.pem or the decrypted folder to a public repo.
"""
import argparse
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_private_key

MAGIC = b"ZENREVIEWv1!"
INFO = b"zen-editorial-private-owner-review-v1"
EXPECTED_ENCRYPTED_SHA = "0cd5fd15ff799c9e051540f9320125f811bffd1482cebf19ab2233b6766d7ecf"
EXPECTED_ARCHIVE_SHA = "d3048aaaf166150d5a5ead9b518f3eb5ff1681b2b486480ad060e35e8a2c9d5a"
MAX_ARCHIVE = 128 * 1024 * 1024


def sha(data):
    return sha256(data).hexdigest()


def open_owner_package(encrypted, private_key_path, destination):
    encrypted = Path(encrypted).expanduser()
    private_key_path = Path(private_key_path).expanduser()
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Папка для результата уже существует. Укажи новую папку.")
    payload = encrypted.read_bytes()
    if sha(payload) != EXPECTED_ENCRYPTED_SHA:
        raise ValueError("Контрольная сумма зашифрованного файла не совпадает с тестом GitHub.")
    key = load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(key, X25519PrivateKey):
        raise ValueError("Требуется owner_private_key.pem формата X25519.")
    offset = len(MAGIC)
    if not payload.startswith(MAGIC) or len(payload) < offset + 60:
        raise ValueError("Неверный формат зашифрованного комплекта.")
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    ephemeral = X25519PublicKey.from_public_bytes(payload[offset:offset + 32])
    nonce = payload[offset + 32:offset + 44]
    aad = payload[:offset + 44]
    symmetric = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(key.exchange(ephemeral))
    raw = AESGCM(symmetric).decrypt(nonce, payload[offset + 44:], aad)
    if sha(raw) != EXPECTED_ARCHIVE_SHA:
        raise ValueError("Контрольная сумма расшифрованного архива не совпадает.")
    with ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        if (len(names) != len(set(names)) or z.testzip() is not None
                or sum(x.file_size for x in z.infolist()) > MAX_ARCHIVE):
            raise ValueError("Архив повреждён или слишком велик.")
        for member in names:
            part = PurePosixPath(member)
            if (not part.parts or ".." in part.parts or part.is_absolute()
                    or "\\" in member or member != part.as_posix() or member.endswith("/")
                    or (member != "Статус комплекта.json" and part.parts[0] != "cinema-01")):
                raise ValueError("Недопустимый путь в архиве.")
        contents = {name: z.read(name) for name in names}
    status = json.loads(contents["Статус комплекта.json"])
    if (status.get("day") != "2026-11-23"
            or status.get("publication_authorized") is not False
            or status.get("articles") != {"cinema-01": "rights_review_pending"}):
        raise ValueError("Проверка статуса комплекта не пройдена.")
    manifest = contents["cinema-01/SHA256SUMS.txt"].decode("utf-8")
    verified = set()
    for line in manifest.splitlines():
        checksum, name = line.split("  ", 1)
        full_name = "cinema-01/" + name
        if not re.fullmatch(r"[0-9a-f]{64}", checksum) or sha(contents[full_name]) != checksum:
            raise ValueError("Контрольная сумма файла комплекта не совпадает.")
        verified.add(full_name)
    if set(contents) != verified | {"Статус комплекта.json", "cinema-01/SHA256SUMS.txt"}:
        raise ValueError("Состав комплекта не совпадает с контрольным списком.")
    checks = json.loads(contents["cinema-01/Статус и проверки.json"])
    if (checks.get("publication_authorized") is not False or checks.get("storage_only") is not True
            or checks["text_sha256"] != sha(contents["cinema-01/Текст для SHA-256.txt"])
            or len(checks["images"]) != 8):
        raise ValueError("Контроль редакционных данных не пройден.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".zen-private-review-", dir=destination.parent) as tmp:
        folder = Path(tmp) / "verified"
        for name, data in contents.items():
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        folder.rename(destination)
    print("Готово! Статья, DOCX, HTML и 8 изображений открыты в:", destination)
    print("Важно: это тест хранения. Статья НЕ разрешена к публикации.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--encrypted", required=True)
    ap.add_argument("--private-key", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    open_owner_package(args.encrypted, args.private_key, args.output)
