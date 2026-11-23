"""Generate local owner keys for the encrypted editorial store.

Run only on the owner's trusted computer. No network calls are made.
NEVER commit the generated directory or send private files to anyone.
"""
import argparse
import os
from pathlib import Path
from secrets import token_bytes

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def prepare(destination):
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink() or destination.exists():
        raise SystemExit("Папка назначения уже существует. Выберите новую пустую папку.")
    destination.mkdir(mode=0o700, parents=True)
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    values = {
        "owner_private_key.pem": pem,
        "state_key_hex.txt": (token_bytes(32).hex() + "\n").encode("ascii"),
        "owner_public_key_hex.txt": (public + "\n").encode("ascii"),
    }
    for name, raw in values.items():
        path = destination / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    print("Ключи созданы локально. Ни один ключ не отправлялся в интернет.")
    print("Публичный ключ X25519 (не секрет):", public)
    print("Закрытый ключ сохранён в owner_private_key.pem. НИКОМУ НЕ ОТПРАВЛЯТЬ.")
    print("Для GitHub Actions Secret EDITORIAL_STATE_KEY_HEX скопируйте содержимое state_key_hex.txt.")
    print("Сохраните оба секретных файла в надёжном защищённом месте и сделайте безопасную резервную копию.")
    print("Внимание: на Windows права 0o600 не заменяют защиту учётной записи и шифрование диска.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="A new local folder, outside GitHub repositories and synchronized cloud folders")
    args = parser.parse_args()
    prepare(args.output)
