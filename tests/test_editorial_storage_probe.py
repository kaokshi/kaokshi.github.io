"""Offline verification of synthetic private GitHub storage probe and cleanup."""
import base64
import unittest
from unittest.mock import patch

from src import editorial_storage_probe as store
from src.editorial_providers import Blocked


class PrivateStorageProbeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.content = b"encrypted-test-fixture"
        self.sha = "a" * 40

    def fake_api(self, token, endpoint, *, method="GET", data=None):
        self.calls.append((method, endpoint, data))
        if endpoint == "repos/kaokshi/zen-editorial-review":
            return {"visibility": "private", "full_name": "kaokshi/zen-editorial-review"}
        if method == "PUT":
            self.assertEqual(base64.b64decode(data["content"]), self.content)
            return {"content": {"sha": self.sha}}
        if method == "DELETE":
            self.assertEqual(data["sha"], self.sha)
            return {}
        return {"content": base64.b64encode(self.content).decode("ascii")}

    def test_encrypted_roundtrip_and_cleanup(self):
        # The plaintext contains a random UUID generated inside probe().
        # Test encryption and decryption with actual ephemeral symmetric keys.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os
        key = AESGCM.generate_key(bit_length=256)
        cipher = AESGCM(key)

        def seal(raw):
            self.plaintext = raw
            return b"NONCE" + (nonce := os.urandom(12)) + cipher.encrypt(nonce, raw, None)

        def unseal(raw):
            self.assertTrue(raw.startswith(b"NONCE"))
            return cipher.decrypt(raw[5:17], raw[17:], None)

        def api(token, endpoint, *, method="GET", data=None):
            self.calls.append((method, endpoint))
            if endpoint == "repos/kaokshi/zen-editorial-review":
                return {"visibility": "private", "full_name": store.EXPECTED_REPOSITORY}
            if method == "PUT":
                self.content = base64.b64decode(data["content"])
                return {"content": {"sha": self.sha}}
            if method == "DELETE":
                return {}
            return {"content": base64.b64encode(self.content).decode()}

        with patch.object(store, "owner_key", return_value="f" * 64), \
             patch.object(store, "seal_state", side_effect=seal), \
             patch.object(store, "unseal_state", side_effect=unseal):
            result = store.probe(store.EXPECTED_REPOSITORY, "fake-token", call=api)
        self.assertEqual(result["status"], "private_encrypted_roundtrip_verified")
        self.assertEqual([row[0] for row in self.calls], ["GET", "PUT", "GET", "DELETE"])

    def test_public_repository_refused_before_any_write(self):
        def api(token, endpoint, *, method="GET", data=None):
            self.calls.append(method)
            return {"visibility": "public", "full_name": store.EXPECTED_REPOSITORY}
        with patch.object(store, "owner_key", return_value="f" * 64), \
             patch.object(store, "seal_state", return_value=b"ciphertext"), \
             patch.object(store, "unseal_state", return_value=b"bad"):
            with self.assertRaises(Blocked):
                store.probe(store.EXPECTED_REPOSITORY, "fake-token", call=api)
        self.assertNotIn("PUT", self.calls)

    def test_missing_token_and_wrong_repository_rejected(self):
        for repo, token in ((store.EXPECTED_REPOSITORY, ""), ("kaokshi/kaokshi.github.io", "token")):
            with self.assertRaisesRegex(Blocked, "private_probe_repository_or_token_missing"):
                store.probe(repo, token, call=self.fake_api)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
