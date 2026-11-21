"""Import a privately reviewed scientific still for a specific section and claim."""
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont
from editorial_revision import save_case
from editorial_writer import Pending, private_path


def specification_hash(row):
    fields = {key: row[key] for key in ("id", "section", "media_type", "claim", "caption", "rationale")}
    return sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


INNER_PLANETS = "Первые четыре планеты от Солнца — Меркурий, Венера, Земля и Марс."
PLANETS_SOURCE = "https://science.nasa.gov/solar-system/planets/"


MOON_PHASES = "Вне лунного затмения Солнце освещает половину Луны, а с Земли видна разная часть освещённой поверхности."
MOON_SOURCE = "https://science.nasa.gov/moon/moon-phases/"


def inner_planets_diagram():
    """Illustrate ordering only. Neither distances nor radii are to scale."""
    image = Image.new("RGB", (1280, 720), "#f7f7f2")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 34)
    heading = ImageFont.truetype("DejaVuSans.ttf", 48)
    draw.text((60, 65), "Первые четыре планеты от Солнца", font=heading, fill="#122634")
    draw.text((60, 190), "Направление от Солнца", font=font, fill="#122634")
    draw.line((68, 273, 1175, 273), fill="#425968", width=6)
    draw.polygon([(1175,259),(1202,273),(1175,287)], fill="#425968")
    for n, label in enumerate(("Меркурий","Венера","Земля","Марс"), 1):
        x = 155 + (n-1)*310
        draw.ellipse((x-40,340,x+40,420), fill="#286280")
        draw.text((x,380), str(n), font=font, anchor="mm", fill="white")
        draw.text((x,465), label, font=font, anchor="mm", fill="#122634")
    draw.text((60,600), "Условная схема. Размеры и расстояния вне масштаба.", font=font, fill="#425968")
    return image


def moon_phase_model():
    """Orthographic projection of a sphere with directional Lambertian lighting."""
    image = Image.new("RGB", (1280, 720), "#122634")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 31)
    heading = ImageFont.truetype("DejaVuSans.ttf", 47)
    draw.text((65, 65), "Какая часть освещённой стороны видна?", font=heading, fill="white")
    radius = 125
    for center, angle, label in [(225, 135, "Серп"), (640, 90, "Четверть"), (1055, 0, "Полнолуние")]:
        angle = math.radians(angle)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                z_squared = 1 - (dx*dx + dy*dy) / radius**2
                if z_squared < 0:
                    continue
                light = dx / radius * math.sin(angle) + math.sqrt(z_squared) * math.cos(angle)
                luminance = round(29 + 209 * max(light, 0))
                image.putpixel((center + dx, 345 + dy), (luminance,) * 3)
        draw.text((center, 512), label, font=font, anchor="mm", fill="white")
    draw.text((65, 620), "3D-модель освещения сферы. Рельеф Луны не показан.", font=font, fill="white")
    return image


def render_candidate(folder, row, digest):
    from editorial_scene_review import scene_lock
    renderers = {("explanatory_2d", INNER_PLANETS): (inner_planets_diagram, PLANETS_SOURCE),
                 ("explanatory_3d", MOON_PHASES): (moon_phase_model, MOON_SOURCE)}
    renderer = renderers.get((row["media_type"], row["claim"].strip()))
    if renderer is None:
        return
    make_image, source = renderer
    with scene_lock(folder):
        target = folder / "illustration.png"
        if target.exists():
            return
        with NamedTemporaryFile(dir=folder) as stream:
            make_image().save(stream, format="PNG")
            stream.flush()
            stream.seek(0)
            digest_image = sha256(stream.read()).hexdigest()
            os.link(stream.name, target)
        save_case(folder / "candidate.json", {"specification_sha256": digest,
                  "sha256": digest_image, "scientific_source": source,
                  "scientific_basis": row["claim"], "status": "awaiting_review",
                  "provenance": "editorial_science_asset." + make_image.__name__,
                  "media_type": row["media_type"]})


def reviewed_still(root, row):
    digest = specification_hash(row)
    folder = private_path(root) / ".science-reviews" / digest
    if not folder.resolve().is_relative_to(private_path(root)):
        raise ValueError("Private science directory escaped workspace")
    reason = "Требуется проверка научной иллюстрации " + str(folder)
    if row["media_type"] == "animation":
        raise Pending("science_visual_review_pending", "Для анимации требуется подтверждение поддержки формата")
    render_candidate(folder, row, digest)
    review_path = folder / "review.json"
    image_path = folder / "illustration.png"
    if (review_path.is_symlink() or image_path.is_symlink() or not review_path.is_file()
            or not image_path.is_file() or image_path.stat().st_size > 15_000_000):
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
