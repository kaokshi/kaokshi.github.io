#!/usr/bin/env python3
"""Public-key encryption for private editorial drafts in public CI artifacts.

Only the ciphertext is uploaded by GitHub Actions. The owner keeps the
X25519 private key; the corresponding raw 32-byte public key is non-secret.
Version 1 wire format: magic (12), ephemeral public key (32), nonce (12),
AES-256-GCM ciphertext. HKDF derives one fresh symmetric key for each archive.
"""
import os

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat,
)

MAGIC=b"ZENREVIEWv1!"
HKDF_INFO=b"zen-editorial-private-owner-review-v1"
MAX_INPUT=128*1024*1024


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
