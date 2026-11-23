"""Required dense Dzen article layout: TOC -> cover -> intro -> heading -> image -> prose.

Exactly one distinct image is assigned to each section, plus one unique cover.
All source/rights/semantic checks remain mandatory in the image pipeline.
"""
from collections import Counter

from .editorial_images import select_cover
from .editorial_providers import Blocked


def layout_issues(article, images, *, minimum=7, maximum=8):
    sections = article.get("sections", [])
    count = len(sections)
    issues = []
    if not sections or not all(row.get("heading") and row.get("paragraphs") for row in sections):
        issues.append("layout_sections_missing")
    if len(images) != count + 1 or not minimum <= len(images) <= maximum:
        issues.append("layout_image_count_must_equal_sections_plus_cover")
    if any(not isinstance(row, dict) or type(row.get("section")) is not int
           or not 1 <= row["section"] <= count for row in images):
        issues.append("layout_image_section_invalid")
    if any(not isinstance(row, dict) or not row.get("file") or not row.get("sha256") for row in images):
        issues.append("layout_image_metadata_missing")
    if issues:
        return issues
    if len({row["file"] for row in images}) != len(images):
        issues.append("layout_duplicate_image_file")
    if len({row["sha256"] for row in images}) != len(images):
        issues.append("layout_duplicate_image_bytes")
    distribution = Counter(row["section"] for row in images)
    if distribution[1] != 2 or any(distribution[section] != 1 for section in range(2, count + 1)):
        issues.append("layout_image_distribution_invalid")
    if not select_cover(images):
        issues.append("layout_cover_missing")
    return issues


def arrange(article, images, *, minimum=7, maximum=8):
    issues = layout_issues(article, images, minimum=minimum, maximum=maximum)
    if issues:
        raise Blocked("layout_not_ready: " + ", ".join(issues))
    cover = select_cover(images)
    hero = next(row for row in images if row["file"] == cover)
    section_pictures = {}
    for section in range(1, len(article["sections"]) + 1):
        options = [row for row in images if row["section"] == section and row["file"] != cover]
        if len(options) != 1:
            raise Blocked("layout_image_section_missing")
        section_pictures[section] = options[0]
    return hero, section_pictures
