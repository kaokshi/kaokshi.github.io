"""Authenticated checkpoints and retrieval of verified owner review packages."""
import argparse
from datetime import date, datetime
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from zipfile import ZIP_DEFLATED, ZipFile

from .editorial_encrypted_review import MAX_INPUT, decrypt, encrypt
from .editorial_length import check_length
from .editorial_memory import PrivateGit, StorageError, digest, read_json, write
from .editorial_providers import Blocked

ROOT = Path(__file__).resolve().parents[1]
STATE_MAGIC = b"ZENSTATEv1!"
PUBLIC_REPOSITORY = "kaokshi/kaokshi.github.io"
WORKFLOW = "editorial-production.yml"


def private_path(path):
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise Blocked("private_output_must_be_outside_public_repository")
    return path


def owner_key():
    from cryptography.hazmat.primitives.asymmetric import x25519
    key = os.environ.get("EDITORIAL_REVIEW_PUBLIC_KEY", "")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", key):
        raise Blocked("storage_configuration_pending: owner_public_key_missing")
    try:
        x25519.X25519PrivateKey.generate().exchange(x25519.X25519PublicKey.from_public_bytes(bytes.fromhex(key)))
    except ValueError:
        raise Blocked("storage_configuration_pending: owner_public_key_invalid") from None
    return key


def state_key():
    key = os.environ.get("EDITORIAL_STATE_KEY_HEX", "")
    if not key:
        return None
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", key):
        raise Blocked("storage_configuration_pending: state_key_invalid")
    return bytes.fromhex(key)


def seal_state(data):
    key = state_key()
    if key is None:
        return encrypt(data, owner_key())
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return STATE_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, STATE_MAGIC)


def unseal_state(data):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = state_key()
    if not key or not data.startswith(STATE_MAGIC):
        raise Blocked("storage_configuration_pending: automated_checkpoint_decryption_unavailable")
    prefix = len(STATE_MAGIC)
    try:
        return AESGCM(key).decrypt(data[prefix:prefix + 12], data[prefix + 12:], STATE_MAGIC)
    except (ValueError, InvalidTag):
        raise Blocked("storage_configuration_pending: checkpoint_authentication_failed") from None


def safe_zip(data):
    archive = ZipFile(io.BytesIO(data))
    names = set()
    total = 0
    for entry in archive.infolist():
        path = PurePosixPath(entry.filename)
        total += entry.file_size
        if (not path.parts or path.as_posix() != entry.filename or path.is_absolute() or "\\" in entry.filename
                or ".." in path.parts or entry.filename in names or entry.is_dir()
                or total > MAX_INPUT * 3):
            archive.close()
            raise Blocked("unsafe_encrypted_archive")
        names.add(entry.filename)
    if archive.testzip() is not None:
        archive.close()
        raise Blocked("encrypted_archive_integrity_failure")
    return archive


def bundle_state(root, current):
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if (not path.is_file() or path.is_symlink() or path.name == ".lock"
                    or path.suffix == ".tmp"):
                continue
            if relative.parts[0] not in {current, "feedback"} and (
                    "review" in relative.parts or path.suffix in {".jpg", ".zip", ".docx", ".sha256"}):
                continue
            archive.write(path, relative.as_posix())
    if out.tell() > MAX_INPUT:
        raise Blocked("encrypted_checkpoint_size_limit")
    return out.getvalue()


def save_outputs(root, day, outgoing):
    owner = owner_key()
    state = seal_state(bundle_state(root, day))
    write(outgoing / "checkpoint.enc", state)
    packet = root / day / "Комплект согласования.zip"
    if packet.is_file():
        raw = packet.read_bytes()
        verify_review(raw, allow_incomplete=True)
        written_sha = packet.with_suffix(".sha256").read_text(encoding="utf-8").strip()
        if digest(raw) != written_sha:
            raise Blocked("review_packet_sha_mismatch")
        write(outgoing / "owner-review.enc", encrypt(raw, owner))
    return {"storage": "encrypted_checkpoint_created",
            "memory_restore": "configured" if state_key() else "storage_configuration_pending",
            "delivery": "delivery_integration_pending"}


def unpack(data, target):
    with safe_zip(data) as archive:
        for entry in archive.infolist():
            write(target / entry.filename, archive.read(entry))


def restore_blob(payload, root, day):
    data = unseal_state(payload)
    with safe_zip(data) as archive:
        name = day + "/state.json"
        if name not in archive.namelist() or json.loads(archive.read(name))["day"] != day:
            raise Blocked("checkpoint_date_mismatch")
        packet = day + "/Комплект согласования.zip"
        if packet in archive.namelist():
            raw = archive.read(packet)
            report = verify_review(raw, allow_incomplete=True)
            sha = archive.read(day + "/Комплект согласования.sha256").decode().strip()
            if report["date"] != day or report["package_sha256"] != sha:
                raise Blocked("checkpoint_review_mismatch")
    unpack(data, root)


def verify_review(data, *, allow_incomplete=False):
    with safe_zip(data) as archive:
        names = set(archive.namelist())
        index = json.loads(archive.read("Статус комплекта.json"))
        expected = index["articles"]
        if (not isinstance(expected, dict) or not all(re.fullmatch(r"(?:legends|science|cinema)-\d{2}", x)
                                                        for x in expected)):
            raise Blocked("invalid_review_article_index")
        present = {name.split("/")[0] for name in names if name.endswith("/SHA256SUMS.txt")}
        if not allow_incomplete and (present != expected.keys() or any(
                value not in {"review_pending", "rights_review_pending"} for value in expected.values())):
            raise Blocked("review_packet_incomplete")
        if not present <= expected.keys() or any(name != "Статус комплекта.json" and
                ("/" not in name or name.split("/", 1)[0] not in present) for name in names):
            raise Blocked("unindexed_review_file")
        for article in present:
            manifest = archive.read(article + "/SHA256SUMS.txt").decode("utf-8")
            verified = set()
            for line in manifest.splitlines():
                hashed, path = line.split("  ", 1)
                entry = article + "/" + path
                if entry not in names or digest(archive.read(entry)) != hashed:
                    raise Blocked("review_file_sha_mismatch")
                verified.add(entry)
            required = {article + "/" + name for name in
                        ("Статья.docx", "Статья.html", "Текст для SHA-256.txt", "Статус и проверки.json")}
            if not required <= verified or {x for x in names if x.startswith(article + "/")} - verified != {
                    article + "/SHA256SUMS.txt"}:
                raise Blocked("review_manifest_incomplete")
            report = json.loads(archive.read(article + "/Статус и проверки.json"))
            canonical = archive.read(article + "/Текст для SHA-256.txt")
            if digest(canonical) != report["text_sha256"]:
                raise Blocked("review_canonical_text_sha_mismatch")
            _, length_issue = check_length(canonical.decode("utf-8"),
                                           report.get("article_checks", {}).get("length_exception_reason"))
            if length_issue and not allow_incomplete:
                raise Blocked("review_article_length_invalid: " + length_issue)
            assets = {name for name in verified if "/images/" in name}
            by_name = {article + "/images/" + row["file"]: row["sha256"] for row in report["images"]}
            if (assets != by_name.keys() or len(by_name) != len(report["images"])
                    or any(digest(archive.read(name)) != sha for name, sha in by_name.items())):
                raise Blocked("review_image_manifest_mismatch")
        return {"date": index["day"], "articles": expected, "package_sha256": digest(data)}


def gh(*args):
    try:
        proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=False, timeout=55)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("delivery_integration_pending: github_transport_unavailable") from None
    if proc.returncode:
        raise Blocked("delivery_integration_pending: github_transport_rejected") from None
    return proc.stdout


def namespace():
    if os.environ.get("EDITORIAL_ISOLATED_VALIDATION") != "true":
        return ""
    label = os.environ.get("EDITORIAL_VALIDATION_LABEL", "")
    if label and not re.fullmatch(r"[a-z0-9]{1,18}", label):
        raise Blocked("invalid_validation_label")
    return f"validation-{label}-" if label else "validation-"


def trusted_run(run_id, validation=False, refs=None):
    row = json.loads(gh("api", f"repos/{PUBLIC_REPOSITORY}/actions/runs/{run_id}"))
    ref = os.environ.get("EDITORIAL_VALIDATION_REF", "codex/editorial-production") if validation else "main"
    return (row["path"].split("@")[0] == ".github/workflows/" + WORKFLOW
            and row["head_repository"]["full_name"] == PUBLIC_REPOSITORY
            and row["head_branch"] in (refs or {ref})
            and row["event"] in {"workflow_dispatch", "schedule"})


def artifact_rows(prefix, *, refs=None, day=None, run_attempt_suffix=False):
    suffix = f"&name={prefix}{day}" if day else ""
    pages = json.loads(gh("api", f"repos/{PUBLIC_REPOSITORY}/actions/artifacts?per_page=100{suffix}",
                          "--paginate", "--slurp"))
    pattern = re.escape(prefix) + r"\d{4}-\d{2}-\d{2}"
    if run_attempt_suffix:
        pattern += r"(?:-[1-9]\d*)?"
    rows = sorted((row for page in pages for row in page.get("artifacts", [])
                  if re.fullmatch(pattern, row["name"])
                  and (not day or row["name"] == prefix + day) and not row["expired"]), key=lambda row: (row["name"], row["created_at"], row["id"]),
                  reverse=True)
    checked = {}
    for row in rows:
        run_id = row["workflow_run"]["id"]
        if run_id not in checked:
            checked[run_id] = trusted_run(run_id, bool(namespace()), refs)
        if checked[run_id]:
            yield row


def download_artifact(row, destination):
    gh("run", "download", str(row["workflow_run"]["id"]), "-R", PUBLIC_REPOSITORY,
       "-n", row["name"], "-D", str(destination))


def restore_checkpoint(root, day, command):
    prefix = "editorial-state-" + namespace()
    latest = next((row for row in artifact_rows(prefix) if row["name"].removeprefix(prefix) <= day), None)
    if latest is None:
        return {"status": "no_prior_checkpoint"}
    if not state_key():
        if latest["name"] == prefix + day and command in {"daily", "retry"}:
            raise Blocked("storage_configuration_pending: same_day_checkpoint_cannot_be_restored")
        return {"status": "storage_configuration_pending", "artifact_id": latest["id"]}
    with TemporaryDirectory(prefix="zen-restore-") as work:
        download_artifact(latest, work)
        restore_blob((Path(work) / "checkpoint.enc").read_bytes(), root, latest["name"].removeprefix(prefix))
    return {"status": "checkpoint_restored"}


def retrieve(output, key_path, *, day, artifact=None, run_id=None, allow_incomplete=False,
             private_repository=None):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    output = private_path(output)
    if output.exists():
        raise Blocked("retrieval_output_already_exists")
    try:
        key = load_pem_private_key(private_path(key_path).read_bytes(), password=None)
        if not isinstance(key, X25519PrivateKey):
            raise ValueError("expected X25519")
    except (OSError, ValueError, TypeError):
        raise Blocked("delivery_integration_pending: authorized_key_unavailable") from None
    prefix = "editorial-review-" + namespace()
    if private_repository and (artifact or run_id or not day):
        raise Blocked("private_retrieval_requires_exact_date")
    with TemporaryDirectory(prefix="zen-owner-download-") as workspace:
        work = Path(workspace)
        if private_repository:
            store = PrivateGit(private_repository, work / "storage",
                               os.environ.get("EDITORIAL_PRIVATE_TOKEN") or os.environ.get("GH_TOKEN"))
            artifact = store.owner_packet(day)
        items = [None] if artifact else artifact_rows(prefix, day=day)
        for index, row in enumerate(items):
            if index >= 20:
                break
            expected_day = day
            if row:
                item_day = row["name"].removeprefix(prefix)
                if (day and item_day != day) or (run_id and str(row["workflow_run"]["id"]) != run_id):
                    continue
                expected_day = item_day
                folder = work / str(row["id"])
                download_artifact(row, folder)
                path = folder / "owner-review.enc"
            else:
                path = artifact
            try:
                data = decrypt(path.read_bytes(), key)
            except (OSError, ValueError, InvalidTag):
                raise Blocked("delivery_integration_pending: review_decryption_failed") from None
            try:
                verified = verify_review(data, allow_incomplete=allow_incomplete)
            except Blocked as exc:
                if not day and not artifact and str(exc) == "review_packet_incomplete":
                    continue
                raise
            if expected_day and verified["date"] != expected_day:
                raise Blocked("review_artifact_date_mismatch")
            target = work / "verified"
            unpack(data, target)
            # Never leave a partially copied owner package at the final path.
            # Keep the verified files on the same filesystem as the destination
            # until an atomic directory rename can expose the complete package.
            output.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".zen-owner-stage-", dir=output.parent) as staging:
                completed = Path(staging) / "verified"
                shutil.copytree(target, completed)
                if output.exists() or output.is_symlink():
                    raise Blocked("retrieval_output_already_exists")
                completed.rename(output)
            return {**verified, "status": "verified_files_available", "directory": str(output)}
    raise Blocked("delivery_integration_pending: matching_complete_review_not_found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "seal", "retrieve", "reference"))
    parser.add_argument("--date", help="Omit on retrieval to select the latest complete package")
    parser.add_argument("--today", action="store_true", help="Retrieve today's exact Moscow edition")
    parser.add_argument("--private-repository",
                        help="Read the private GitHub store with the authorized gh account")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--private-key", type=Path,
                        default=os.environ.get("EDITORIAL_OWNER_KEY_FILE"))
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--cycle-command", default="daily", choices=("daily", "retry", "review", "learn", "audit", "status"))
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.today:
            if args.command != "retrieve" or args.date:
                raise Blocked("invalid_today_option")
            args.date = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        if (args.command != "retrieve" and not args.date) or (
                args.date and date.fromisoformat(args.date).isoformat() != args.date):
            raise Blocked("invalid_review_date")
        if args.command == "reference":
            prefix = "editorial-review-" + namespace()
            row = next((item for item in artifact_rows(prefix)
                        if item["name"] == prefix + args.date), None)
            result = {"delivery": "delivery_integration_pending",
                      "encrypted_review_artifact_id": row["id"] if row else None}
        elif args.command == "retrieve":
            if not args.output or not args.private_key:
                raise Blocked("delivery_integration_pending: authorized_key_and_output_required")
            repository = args.private_repository
            if not repository and not args.artifact and not args.run_id:
                repository = read_json(ROOT / "config" / "editorial_production.json")["schedule"]["private_repository"]
            result = retrieve(args.output, args.private_key, day=args.date, artifact=args.artifact,
                              run_id=args.run_id, allow_incomplete=args.allow_incomplete,
                              private_repository=repository)
        elif args.command == "restore":
            result = restore_checkpoint(private_path(args.root), args.date, args.cycle_command)
        else:
            result = save_outputs(private_path(args.root), args.date, private_path(args.output))
        print(json.dumps(result, ensure_ascii=False))
        if args.command == "reference":
            return 2
    except (Blocked, StorageError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": str(exc) if isinstance(exc, (Blocked, StorageError)) else type(exc).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
