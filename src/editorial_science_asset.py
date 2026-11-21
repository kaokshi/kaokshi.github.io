"""Import a privately reviewed scientific still for a specific section and claim."""
from hashlib import sha256
from io import BytesIO
import json
from urllib.parse import urlsplit

from PIL import Image
from editorial_writer import Pending, private_path


def specification_hash(row):
    fields = {key: row[key] for key in ("id", "section", "media_type", "claim", "caption", "rationale")}
    return sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def reviewed_still(root, row):
    digest = specification_hash(row)
    folder = private_path(root) / ".science-reviews" / digest
    reason = "Требуется проверка научной иллюстрации " + str(folder)
    if row["media_type"] == "animation":
        raise Pending("science_visual_review_pending", "Для анимации требуется подтверждение поддержки формата")
    review_path = folder / "review.json"
    image_path = folder / "illustration.png"
    if not review_path.is_file() or not image_path.is_file() or image_path.stat().st_size > 15_000_000:
        raise Pending("science_visual_review_pending", reason)
    review = json.loads(review_path.read_text())
    if (review.get("specification_sha256") != digest or review.get("approved") is not True
            or not all(review.get(k) for k in ("reviewer", "artist", "license", "scientific_basis"))
            or not all(urlsplit(review.get(k, "")).scheme == "https" and urlsplit(review[k]).hostname
                       for k in ("source", "license_url", "scientific_source"))):
        raise Pending("science_visual_review_pending", reason)
    data = image_path.read_bytes()
    if review.get("sha256") != sha256(data).hexdigest():
        raise Pending("science_visual_review_pending", reason)
    with Image.open(BytesIO(data)) as img:
        if img.format != "PNG" or img.width * img.height > 16_000_000 or img.is_animated:
            raise Pending("science_visual_review_pending", reason)
        img.verify()
    return data, {k: review[k] for k in ("artist", "license", "license_url", "source", "scientific_basis",
                                          "scientific_source", "specification_sha256", "reviewer")}
