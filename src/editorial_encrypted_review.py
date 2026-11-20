#!/usr/bin/env python3
"""Public-key encryption for private editorial drafts in public CI artifacts.

Only the ciphertext is uploaded by GitHub Actions. The owner keeps the
X25519 private key; the corresponding raw 32-byte public key is non-secret.
Version 1 wire format: magic (12), ephemeral public key (32), nonce (12),
AES-256-GCM ciphertext. HKDF derives one fresh symmetric key for each archive.
"""
import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, load_pem_private_key,
)

MAGIC=b"ZENREVIEWv1!"
HKDF_INFO=b"zen-editorial-private-owner-review-v1"
ALLOWED={"draft.md","draft.json","reports.json","sources.json"}
MAX_INPUT=32*1024*1024


def derive(shared):
    return HKDF(algorithm=hashes.SHA256(),length=32,
                salt=None,info=HKDF_INFO).derive(shared)


def encrypt(plaintext, public_key_hex):
    if len(plaintext)>MAX_INPUT:
        raise ValueError("Owner review packet too large")
    if len(public_key_hex)!=64:
        raise ValueError("X25519 public key must contain 64 hex digits")
    recipient=x25519.X25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    ephemeral=x25519.X25519PrivateKey.generate()
    pub=ephemeral.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    nonce=os.urandom(12)
    prefix=MAGIC+pub+nonce
    ciphertext=AESGCM(derive(ephemeral.exchange(recipient))).encrypt(nonce,plaintext,prefix)
    return prefix+ciphertext


def decrypt(payload, private_key):
    if not payload.startswith(MAGIC) or len(payload)<len(MAGIC)+32+12+16:
        raise ValueError("Malformed encrypted review payload")
    offset=len(MAGIC)
    ephemeral=x25519.X25519PublicKey.from_public_bytes(payload[offset:offset+32])
    nonce=payload[offset+32:offset+44]
    prefix=payload[:offset+44]
    return AESGCM(derive(private_key.exchange(ephemeral))).decrypt(
        nonce,payload[offset+44:],prefix)


def collect_private_review(workspace, records):
    """Store existing generated drafts only; no missing files synthesized."""
    prepared=[]
    for row in records:
        case_dir=Path(row["case_dir"])
        draft=case_dir/"draft.md"
        case=case_dir/"case.json"
        if not case.is_file():
            continue
        content=json.loads(case.read_text(encoding="utf-8"))
        versions=content.get("versions")
        if not isinstance(versions,list) or not versions:
            continue
        chosen=next((v for v in versions if v.get("text_sha256")==content.get("recommended_text_sha256")),
                    versions[-1])
        article=chosen.get("article",{})
        if not article.get("body_html") or not article.get("title"):
            continue
        prepared.append((row,chosen,article,case_dir))
    if not prepared:
        return None,0
    out=io.BytesIO()
    manifest={"schema":"zen-private-review-v1","owner_approval":False,
              "publication_authorized":False,"rss_authorized":False,"articles":[]}
    with ZipFile(out,"w",ZIP_DEFLATED) as archive:
        for i,(row,chosen,article,case_dir) in enumerate(prepared,1):
            path=f"articles/{i:02d}-{row['channel']}"
            # Case files are trusted private workspace output; do not traverse paths.
            archive.writestr(path+"/draft.json",json.dumps(article,ensure_ascii=False,indent=2)+"\n")
            for name in ("draft.md","reports.json","sources.json"):
                source=case_dir/name
                if source.is_file() and source.stat().st_size<MAX_INPUT:
                    archive.writestr(path+"/"+name,source.read_bytes())
            manifest["articles"].append({"channel":row["channel"],
                "text_sha256":chosen["text_sha256"],
                "status":row.get("status"),"ready_for_owner":bool(row.get("article_ready_for_owner")),
                "checks_may_be_pending":not row.get("last_checks_completed",False),
                "folder":path})
        archive.writestr("MANIFEST.json",json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
        archive.writestr("README.txt","Приватные черновики. Для проверки владельцем, не для публикации.\n")
    return out.getvalue(),len(prepared)


def save_encrypted_review(workspace, rows, output, public_key_hex):
    output=Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Never overwrite owner review archive")
    if output.suffix!=".enc" or not output.parent.is_dir():
        raise ValueError("Encrypted .enc output required")
    payload,n=collect_private_review(workspace,rows)
    if payload is None:
        return {"status":"no_generated_drafts","included":0,"transport":"not_connected"}
    protected=encrypt(payload,public_key_hex)
    output.write_bytes(protected)
    os.chmod(output,0o600)
    return {"status":"encrypted_ready_for_owner_download","included":n,
            "encrypted_sha256":sha256(protected).hexdigest(),
            "transport":"github_encrypted_artifact_not_chat_delivery"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("encrypted_file",type=Path)
    parser.add_argument("--private-key",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Use a new output directory under your control")
    private=load_pem_private_key(args.private_key.read_bytes(),password=None)
    if not isinstance(private,x25519.X25519PrivateKey):
        parser.error("Expected X25519 PEM private key")
    plain=decrypt(args.encrypted_file.read_bytes(),private)
    with ZipFile(io.BytesIO(plain)) as zipfile:
        names=zipfile.namelist()
        if any(p.startswith("/") or ".." in Path(p).parts for p in names):
            raise ValueError("Unsafe path in encrypted review")
        args.output.mkdir(mode=0o700)
        for name in names:
            target=args.output/name
            target.parent.mkdir(parents=True,exist_ok=True)
            if not name.endswith("/"):
                target.write_bytes(zipfile.read(name))
                os.chmod(target,0o600)
    print(json.dumps({"status":"decrypted_locally","output":str(args.output)}))


if __name__=="__main__":
    main()
