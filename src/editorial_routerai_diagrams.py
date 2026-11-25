"""Deterministic, source-bound templates for the private science evaluation."""
from functools import lru_cache
import io
from math import log10

from .editorial_routerai import UnsafeToTest

ESA_SOURCE = "https://www.esa.int/Science_Exploration/Space_Science/Rosetta/Comets_-_An_Introduction"
ISON_SOURCE = "https://images.nasa.gov/details/PIA18153"
CAVE_SOURCE = "https://www.nps.gov/subjects/caves/speleothems.htm"
ASTEROID_SOURCE = "https://www.esa.int/Space_Safety/Planetary_Defence"
COMET_SOURCE = "https://science.nasa.gov/solar-system/comets/facts/"

WATER_STORAGE_SOURCE = "https://www.usgs.gov/faqs/how-much-natural-water-there"
WATER_STORAGE = (
    ("Groundwater (fresh and saline)", 23_400_000),
    ("Fresh streams, lakes and swamps", 104_590),
    ("Saline lakes", 85_400),
)


def cave_diagram_replacement(scene):
    if scene.get("template") != "cave-formations":
        return None
    subject = scene["subject"].casefold()
    if any(word in subject for word in ("hydrology", "water table", "saturation")):
        return "cave-groundwater-zones"
    if any(word in subject for word in ("aquifer scale", "groundwater volume")):
        return "groundwater-scale"
    if any(word in subject for word in ("time-lapse", "dripstone growth")):
        return "cave-dripstone-growth"
    if any(word in subject for word in ("classification", "morphology")):
        return "cave-speleothem-types"
    return None


@lru_cache(maxsize=1)
def water_storage_evidence():
    from .editorial_review import sources

    return sources([WATER_STORAGE_SOURCE], ("usgs.gov",))


def cave_cross_section(draw, font):
    draw.text((76, 77), "Cave formation and the water table", fill="white", font=font)
    draw.rectangle((76, 210, 1118, 402), fill="#9e8167")
    draw.rectangle((76, 403, 1118, 661), fill="#315e78")
    draw.line((76, 404, 1118, 404), fill="#bcefff", width=8)
    draw.ellipse((190, 257, 520, 368), fill="#172636")
    draw.ellipse((190, 506, 520, 610), fill="#83c9e9")
    draw.text((550, 263), "Aeration zone: deposition in air", fill="white", font=font)
    draw.text((550, 364), "Water table", fill="#eafaff", font=font)
    draw.text((550, 544), "Saturated zone: cave formation", fill="white", font=font)
    for x in (280, 347, 412):
        draw.polygon([(x - 10, 269), (x + 10, 269), (x, 305)], fill="#e7c9a4")


def water_storage_chart(draw, font):
    draw.text((76, 80), "Where water is stored · global volume", fill="white", font=font)
    draw.text((76, 132), "USGS estimate · logarithmic axis · km³", fill="#bcefff", font=font)
    start, width = 560, 515
    for power in range(4, 9):
        x = start + (power - 4) * width / 4
        draw.line((x, 225, x, 649), fill="#49637d", width=2)
        draw.text((x - 23, 663), f"10^{power}", fill="white", font=font)
    for i, (label, value) in enumerate(WATER_STORAGE):
        y = 280 + i * 126
        draw.text((76, y - 4), label, fill="white", font=font)
        draw.rectangle((start, y, start + (log10(value) - 4) * width / 4, y + 31), fill="#68ceff")
        draw.text((76, y + 38), f"{value:,} km³", fill="#bcefff", font=font)


def dripstone_growth(draw, font):
    draw.text((76, 80), "Dripstone develops in stages", fill="white", font=font)
    for i, label in enumerate(("Ceiling deposit", "Drip and floor deposit", "Joined column")):
        x = 56 + i * 374
        draw.rectangle((x, 215, x + 335, 585), fill="#202c3b", outline="#59758c", width=3)
        draw.rectangle((x + 5, 218, x + 330, 264), fill="#9e8167")
        draw.rectangle((x + 5, 536, x + 330, 582), fill="#9e8167")
        center = x + 163
        tip = 400 if i == 2 else 378
        draw.polygon([(center - 49, 260), (center + 49, 260), (center + 4, tip),
                      (center - 4, tip)], fill="#edcda6")
        if i != 0:
            floor_tip = 400 if i == 2 else 480
            draw.polygon([(center - 75, 540), (center + 75, 540), (center + 7, floor_tip),
                          (center - 7, floor_tip)], fill="#edcda6")
        if i != 2:
            draw.ellipse((center - 3, 417, center + 3, 426), fill="#68ceff")
        draw.text((x + 18, 625), label, fill="white", font=font)


def speleothem_types(draw, font):
    draw.text((76, 80), "Speleothem forms and positions", fill="white", font=font)
    for i, (label, detail) in enumerate((
            ("Stalactite", "Hangs from the ceiling"), ("Stalagmite", "Rises from the floor"),
            ("Column", "Joins ceiling and floor"))):
        y = 180 + 170 * i
        draw.rectangle((76, y, 1120, y + 140), fill="#294357")
        draw.line((120, y + 15, 390, y + 15), fill="#c9b393", width=8)
        draw.line((120, y + 125, 390, y + 125), fill="#c9b393", width=8)
        if i != 1:
            draw.polygon([(198, y + 15), (312, y + 15), (272, y + 79), (238, y + 79)], fill="#ffe0ac")
        if i != 0:
            draw.polygon([(195, y + 125), (317, y + 125), (270, y + (74 if i == 2 else 53)),
                          (242, y + (74 if i == 2 else 53))], fill="#ffe0ac")
        draw.text((445, y + 26), label, fill="white", font=font)
        draw.text((445, y + 79), detail, fill="#cee5ef", font=font)


def render_diagram(template, records):
    from PIL import Image, ImageDraw, ImageFont

    if template == "comet-tails":
        url, terms = COMET_SOURCE, ("ion (gas) tail", "dust tail", "away from the sun")
    elif template == "comet-cold-storage":
        url, terms = ESA_SOURCE, ("volatile", "formed at large distances", "low temperatures")
    elif template == "comet-asteroid-relationship":
        url, terms = ESA_SOURCE, ("main-belt comet", "short-period comets", "depletion")
    elif template == "comet-observation-inference":
        url, terms = ISON_SOURCE, ("nucleus is unresolved", "symmetric and smooth")
    elif template == "cave-groundwater-zones":
        url, terms = CAVE_SOURCE, ("water table", "zone of saturation", "zone of aeration")
    elif template == "groundwater-scale":
        url = WATER_STORAGE_SOURCE
        terms = tuple(f"{quantity:,} cubic kilometers" for _, quantity in WATER_STORAGE)
        records = [*records, *water_storage_evidence()]
    elif template in {"cave-dripstone-growth", "cave-speleothem-types", "cave-formations"}:
        url, terms = CAVE_SOURCE, ("stalactites", "stalagmites", "columns")
    elif template == "asteroid-observations":
        url, terms = ASTEROID_SOURCE, ("asteroid", "close approaches", "predict")
    else:
        raise UnsafeToTest("diagram_template_or_evidence_unavailable")
    if not any(row["url"].rstrip("/") == url.rstrip("/") and row["status"] == "accessible"
               and all(term in row["text"].casefold() for term in terms) for row in records):
        raise UnsafeToTest("diagram_template_or_evidence_unavailable")

    image = Image.new("RGB", (1200, 800), "#102033")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=29)
    if template == "cave-groundwater-zones":
        cave_cross_section(draw, font)
    elif template == "groundwater-scale":
        water_storage_chart(draw, font)
    elif template == "cave-dripstone-growth":
        dripstone_growth(draw, font)
    elif template == "cave-speleothem-types":
        speleothem_types(draw, font)
    elif template == "comet-cold-storage":
        panels = [("Formation far", "from the Sun"), ("Preservation at", "low temperatures"),
                  ("Volatile-rich", "material retained")]
        for i, (line1, line2) in enumerate(panels):
            x = 70 + 380 * i
            draw.rounded_rectangle((x, 290, x + 310, 478), radius=18, outline="#68ceff", width=4)
            draw.text((x + 28, 342), line1, fill="white", font=font)
            draw.text((x + 28, 391), line2, fill="white", font=font)
            if i < len(panels) - 1:
                draw.line((x + 320, 385, x + 368, 385), fill="#e2bd89", width=6)
                draw.polygon([(x + 368, 385), (x + 353, 375), (x + 353, 395)], fill="#e2bd89")
    elif template in {"cave-formations", "asteroid-observations"}:
        labels = (["Ceiling: stalactites", "Floor: stalagmites", "Joined: a column"]
                  if template == "cave-formations" else
                  ["Observe positions", "Fit a possible orbit", "Estimate future position"])
        for i, label in enumerate(labels):
            x = 30 + i * 390
            draw.rounded_rectangle((x, 335, x + 310, 460), radius=14, outline="#68ceff", width=4)
            draw.text((x + 18, 380), label, fill="white", font=ImageFont.load_default(size=24))
            if i < 2:
                draw.line((x + 326, 398, x + 367, 398), fill="#e2bd89", width=5)
                draw.polygon([(x + 374, 398), (x + 360, 391), (x + 360, 405)], fill="#e2bd89")
    elif template == "comet-asteroid-relationship":
        draw.rounded_rectangle((80, 205, 1120, 385), radius=20, outline="#68ceff", width=4)
        draw.text((120, 253), "Asteroid-belt object", fill="white", font=font)
        draw.text((515, 253), "May display comet-like activity", fill="#68ceff", font=font)
        draw.rounded_rectangle((80, 430, 1120, 610), radius=20, outline="#e2bd89", width=4)
        draw.text((120, 478), "Short-period comet", fill="white", font=font)
        draw.text((515, 478), "May become asteroid-like", fill="#e2bd89", font=font)
        for y, color in ((270, "#68ceff"), (495, "#e2bd89")):
            draw.line((452, y, 498, y), fill=color, width=4)
            draw.polygon([(498, y), (485, y - 8), (485, y + 8)], fill=color)
    elif template == "comet-observation-inference":
        draw.line((600, 220, 600, 625), fill="#68819b", width=3)
        draw.text((85, 245), "Hubble observation", fill="#68ceff", font=font)
        draw.text((85, 350), "Smooth, symmetric coma", fill="white", font=font)
        draw.text((85, 440), "Nucleus unresolved", fill="white", font=font)
        draw.text((655, 245), "Inference", fill="#e2bd89", font=font)
        draw.text((655, 350), "Consistent with an intact nucleus", fill="white", font=font)
        draw.text((655, 440), "Not a direct view of the nucleus", fill="white", font=font)
    else:
        draw.ellipse((90, 315, 230, 455), fill="#ffc254")
        draw.text((125, 485), "Sun", fill="white", font=font)
        # The ion tail lies on the anti-solar line. The dust tail curves.
        draw.line((535, 385, 1080, 385), fill="#68ceff", width=12)
        draw.polygon([(1080, 385), (1054, 369), (1054, 401)], fill="#68ceff")
        draw.line([(540 + x, 385 + x*x / 1550) for x in range(0, 501, 5)],
                  fill="#e2bd89", width=16, joint="curve")
        draw.ellipse((516, 364, 555, 404), fill="#a7a5a3")
        draw.text((486, 310), "Nucleus", fill="white", font=font)
        draw.text((888, 325), "Ion tail", fill="#68ceff", font=font)
        draw.text((905, 573), "Dust tail", fill="#e2bd89", font=font)
    draw.text((76, 725), "Conceptual diagram · not to scale", fill="white", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), {
        "kind": "diagram", "source": url, "label": "Программная схема · не в масштабе",
        "ai_generated": False, "rights_status": "original_programmatic_graphic",
        "template": template,
    }
