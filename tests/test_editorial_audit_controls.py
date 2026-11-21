import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import patch

from PIL import Image, ImageOps
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_cloud_budget import reserve
from editorial_cloudflare_vision import REQUIRED, VisionPending, parse_review
from editorial_image_guardrails import detect_severe_crop
from editorial_media import produce_one
from editorial_scene_contract import make_contract, fingerprint
from editorial_scene_review import MODEL, KLEIN, prepare_scene
from editorial_science_asset import specification_hash
from editorial_writer import Pending


def encoded(img):
    output = io.BytesIO()
    img.save(output, "PNG")
    return output.getvalue()


class Controls(unittest.TestCase):
    def test_budget_concurrent_and_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"ZEN_CF_LEDGER": tmp + "/budget", "CLOUDFLARE_ACCOUNT_ID": "a"*32,
                   "ZEN_CF_BUDGET_DATE": datetime.now(timezone.utc).date().isoformat(), "ZEN_CF_MAX_REQUESTS": "2"}
            def attempt(i):
                try:
                    reserve(env)
                    return True
                except ValueError:
                    return False
            with ThreadPoolExecutor(8) as pool:
                self.assertEqual(sum(pool.map(attempt, range(8))), 2)
            with self.assertRaises(ValueError):
                reserve(dict(env, ZEN_CF_BUDGET_DATE="yesterday"))
            with self.assertRaises(ValueError):
                reserve(dict(env, GITHUB_ACTIONS="true", GITHUB_RUN_ID="999", GITHUB_RUN_ATTEMPT="1"))

    def test_commons_requires_allowed_https_host_on_redirect(self):
        from editorial_media import commons_url
        self.assertEqual(commons_url("https://upload.wikimedia.org/a"), "https://upload.wikimedia.org/a")
        for url in ("http://commons.wikimedia.org/a", "https://upload.wikimedia.org.example.org/a",
                    "https://me@upload.wikimedia.org/a", "https://upload.wikimedia.org:8443/a"):
            with self.assertRaises(ValueError):
                commons_url(url)

    def test_only_preflight_budget_failure_releases_request(self):
        from editorial_cloud_budget import BudgetUnavailable
        from editorial_scene_review import checkpoint_call
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            folder, state = Path(tmp), {}
            with self.assertRaises(BudgetUnavailable):
                checkpoint_call(folder,state,"original",Mock(side_effect=BudgetUnavailable("not sent")))
            connection = Mock(side_effect=TimeoutError("response unknown"))
            with self.assertRaises(TimeoutError):
                checkpoint_call(folder,state,"original",connection)
            with self.assertRaises(Pending):
                checkpoint_call(folder,state,"original",connection)
            connection.assert_called_once()
            self.assertEqual(json.loads((folder/"state.json").read_text())["original"]["status"], "requested")

    def test_strict_vision_response(self):
        response = {"checks": dict.fromkeys(REQUIRED, True), "defects": ["coat replaced"]}
        self.assertEqual(parse_review(json.dumps(response), b"a", b"b")["status"], "reject_candidate")
        for raw in (json.dumps(dict(response, decision=True)), '{"checks":{},"checks":{},"defects":[]}', None):
            with self.assertRaises(VisionPending):
                parse_review(raw, b"a", b"b")

    def test_square_to_wide_crop(self):
        image = Image.effect_mandelbrot((512,512), (-2,-1,1,1), 45).convert("RGB")
        self.assertEqual(detect_severe_crop(encoded(image), encoded(ImageOps.fit(image,(1280,720))))["status"], "reject")

    def test_review_resume_and_stale_approval(self):
        original = encoded(Image.effect_noise((512,512),40).convert("RGB"))
        edited = encoded(Image.effect_noise((1280,720),40).convert("RGB"))
        contract = make_contract("Girl opens wooden door", subject="girl", location="hall", action="opens door",
                                 must_keep=["green coat"], fix="remove artifact on knob")
        row = {"id":"image-1","prompt":contract["scene"],"scene_contract":contract,"edit_original_sha256":sha256(original).hexdigest()}
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"ZEN_IMAGE_FINAL_KLEIN":"true",
                "ZEN_IMAGE_VISION_PILOT":"false"}), patch("editorial_scene_review.generate_scene",
                return_value=(original, {})) as generate, patch("editorial_scene_review.refine_image",
                return_value=(edited, {})) as refine:
            root = Path(tmp)
            with self.assertRaises(Pending):
                prepare_scene(row, root)
            generate.assert_not_called()
            entry = {"license":"test", "approved":True, "reviewer":"tester", "source_url":"https://example.org/license"}
            (root/"model-rights.json").write_text(json.dumps({MODEL:entry, KLEIN:entry}))
            for _ in range(2):
                with self.assertRaises(Pending):
                    prepare_scene(row, root)
            folder = next((root/".scene-reviews").iterdir())
            evidence = dict.fromkeys(REQUIRED, True)
            evidence.update(reviewer_kind="human", reviewer="tester", decision="approve",
                            scene_contract_sha256=fingerprint(contract), original_sha256=sha256(original).hexdigest(),
                            edited_sha256=sha256(edited).hexdigest())
            (folder/"review.json").write_text(json.dumps(evidence))
            self.assertEqual(prepare_scene(row,root)[0], edited)
            evidence["edited_sha256"] = "stale"
            (folder/"review.json").write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):
                prepare_scene(row,root)
            generate.assert_called_once()
            refine.assert_called_once()

    def test_rendered_diagram_is_private_candidate(self):
        from editorial_science_asset import INNER_PLANETS, PLANETS_SOURCE
        row = dict(id="image-3", section=1, media_type="explanatory_2d", claim=INNER_PLANETS,
                   caption="Порядок планет", rationale="пространственная последовательность")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(Pending):
                produce_one("science",row,root,None)
            folder = root / ".science-reviews" / specification_hash(row)
            record = json.loads((folder/"candidate.json").read_text())
            self.assertEqual(record["scientific_source"], PLANETS_SOURCE)
            self.assertEqual(record["sha256"],sha256((folder/"illustration.png").read_bytes()).hexdigest())
            self.assertFalse((root/"content").exists())

    def test_moon_phase_3d_has_distinct_projected_lit_areas(self):
        from editorial_science_asset import moon_phase_model
        image = moon_phase_model()
        areas = [sum(image.getpixel((x,y))[0] > 35 for x in range(c-125,c+126) for y in range(220,471))
                 for c in (225,640,1055)]
        self.assertLess(areas[0], areas[1])
        self.assertLess(areas[1], areas[2])
        self.assertAlmostEqual(areas[1] / areas[2], 0.5, delta=0.04)

    def test_holdout_source_leakage_and_ambiguous_predictions(self):
        from editorial_vision_holdout import score
        with tempfile.TemporaryDirectory() as tmp:
            labels, predictions = Path(tmp)/"labels", Path(tmp)/"predictions"
            ids = ["1"*64, "2"*64]
            labels.write_text(json.dumps([dict(case_sha256=key, human_reviewer="test",
                source_group="private-set", accept=bool(i), defect_type="crop") for i,key in enumerate(ids)]))
            predictions.write_text(json.dumps(dict(zip(ids, [False, None]))))
            digest = sha256(labels.read_bytes()).hexdigest()
            self.assertEqual(score(labels,predictions,set(),digest), {"crop":{"TN":1,"unavailable":1}})
            with self.assertRaises(ValueError):
                score(labels,predictions,{"private-set"},digest)

    def test_scientific_still_requires_review_and_visible_label(self):
        row = dict(id="image-1",section=2,media_type="explanatory_2d",claim="claim",caption="caption",rationale="mechanism")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(Pending):
                produce_one("science",row,root,None)
            digest = specification_hash(row)
            folder = root/".science-reviews"/digest
            folder.mkdir(parents=True)
            data = encoded(Image.new("RGB",(160,120)))
            (folder/"illustration.png").write_bytes(data)
            record = dict(approved=True,reviewer="test",artist="test",license="test",scientific_basis="reason",
                          specification_sha256=digest,sha256=sha256(data).hexdigest())
            record.update(dict.fromkeys(("source","scientific_source","license_url"),"https://example.org/evidence"))
            (folder/"review.json").write_text(json.dumps(record))
            result = produce_one("science",row,root,None)
            self.assertTrue(result["caption"].startswith("Поясняющая 2D-иллюстрация"))
            self.assertEqual(result["media_type"], "explanatory_2d")
