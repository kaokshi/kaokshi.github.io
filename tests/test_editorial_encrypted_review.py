"""Public-key encrypted owner ZIP: roundtrip, wrong-key rejection, no plaintext fallback."""
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from io import BytesIO
import json
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from editorial_encrypted_review import encrypt,decrypt,save_encrypted_review,MAGIC

class EncryptedReviewTests(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        owner=x25519.X25519PrivateKey.generate()
        stranger=x25519.X25519PrivateKey.generate()
        public=owner.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        article=b"PRIVATE ARTICLE: full text from model"
        sealed=encrypt(article,public)
        self.assertTrue(sealed.startswith(MAGIC))
        self.assertNotIn(article,sealed)
        self.assertEqual(decrypt(sealed,owner),article)
        with self.assertRaises(InvalidTag):
            decrypt(sealed,stranger)
        with self.assertRaises(InvalidTag):
            decrypt(sealed[:-1]+bytes([sealed[-1]^1]),owner)

    def test_archive_includes_only_actual_generated_article(self):
        with TemporaryDirectory() as tmp:
            home=Path(tmp)
            w=home/"case"
            w.mkdir()
            article={"channel":"legends","title":"Ночная лестница",
                     "body_html":"<p>Тестовый текст</p>","release_hold":True}
            digest="a"*64
            case={"versions":[{"article":article,"text_sha256":digest}],
                  "decision":"owner_review_pending"}
            (w/"case.json").write_text(json.dumps(case,ensure_ascii=False),encoding="utf-8")
            (w/"draft.md").write_text("SECRET DRAFT",encoding="utf-8")
            owner=x25519.X25519PrivateKey.generate()
            public=owner.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
            output=home/"private.enc"
            rows=[{"case_dir":str(w),"channel":"legends","status":"owner_review_pending",
                   "article_ready_for_owner":False,"last_checks_completed":False},
                  {"case_dir":str(home/"missing"),"channel":"science","status":"not_created"}]
            summary=save_encrypted_review(home,rows,output,public)
            self.assertEqual(summary["included"],1)
            self.assertEqual(summary["status"],"encrypted_ready_for_owner_download")
            raw=output.read_bytes()
            self.assertNotIn(b"SECRET DRAFT",raw)
            with ZipFile(BytesIO(decrypt(raw,owner))) as z:
                names=z.namelist()
                self.assertIn("articles/01-legends/draft.md",names)
                self.assertIn("MANIFEST.json",names)
                manifest=json.loads(z.read("MANIFEST.json"))
                self.assertEqual(len(manifest["articles"]),1)
                self.assertFalse(manifest["articles"][0]["ready_for_owner"])
            with self.assertRaisesRegex(ValueError,"overwrite"):
                save_encrypted_review(home,rows,output,public)

    def test_no_cases_creates_no_artifact(self):
        with TemporaryDirectory() as td:
            out=Path(td)/"private.enc"
            owner=x25519.X25519PrivateKey.generate()
            public=owner.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
            summary=save_encrypted_review(td,[{"case_dir":td+"/missing","channel":"cinema"}],out,public)
            self.assertEqual(summary["included"],0)
            self.assertFalse(out.exists())

if __name__=="__main__":
    unittest.main()
