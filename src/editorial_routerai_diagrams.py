"""Deterministic, source-bound templates for the private science evaluation."""
import io

from .editorial_routerai import UnsafeToTest

ESA_SOURCE = "https://www.esa.int/Science_Exploration/Space_Science/Rosetta/Comets_-_An_Introduction"
ISON_SOURCE = "https://images.nasa.gov/details/PIA18153"
COMET_SOURCE = "https://science.nasa.gov/solar-system/comets/facts/"


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
    else:
        raise UnsafeToTest("diagram_template_or_evidence_unavailable")
    if not any(row["url"].rstrip("/") == url.rstrip("/") and row["status"] == "accessible"
               and all(term in row["text"].casefold() for term in terms) for row in records):
        raise UnsafeToTest("diagram_template_or_evidence_unavailable")

    image = Image.new("RGB", (1200, 800), "#102033")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=29)
    if template == "comet-cold-storage":
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
