"""Deterministic guardrail for severe center-crop regressions.

Compares center-crop hypotheses, including changed aspect ratios. A changed aspect ratio is not
proof of a crop. Conservative abstention avoids mistaking legitimate Klein edits.
Uses Pillow for ANALYSIS only, not for image visual post-processing.
"""
from io import BytesIO
from PIL import Image, ImageChops, ImageStat


def _mean_difference(a,b):
    gray_a=a.convert("L").resize((96,96))
    gray_b=b.convert("L").resize((96,96))
    delta=ImageChops.difference(gray_a,gray_b)
    return ImageStat.Stat(delta).mean[0]


def detect_severe_crop(original, candidate):
    with Image.open(BytesIO(original)) as src, Image.open(BytesIO(candidate)) as dst:
        src.load()
        dst.load()
        if src.size!=dst.size:
            from PIL import ImageOps
            fitted = ImageOps.fit(src, dst.size)
            kept_area = min(src.width * dst.height / (src.height * dst.width),
                            src.height * dst.width / (src.width * dst.height))
            difference = _mean_difference(fitted, dst)
            baseline = _mean_difference(src, dst)
            if kept_area < 0.70 and difference < 10 and difference < baseline * 0.40:
                return {"status": "reject", "reason": "severe_center_crop", "retained_fraction": kept_area}
            return {"status":"abstain","reason":"dimensions_differ"}
        src=src.convert("RGB")
        dst=dst.convert("RGB")
        full=_mean_difference(src,dst)
        if full<8:
            return {"status":"pass","reason":"nearly_identical"}
        width,height=src.size
        crops=[]
        for fraction in (.10,.20,.28,.30,.35):
            crop=src.crop((int(width*fraction),int(height*fraction),
                           int(width*(1-fraction)),int(height*(1-fraction))))
            diff=_mean_difference(crop,dst)
            crops.append((diff,fraction))
        best,fraction=min(crops)
        # Exact center crops with resampling should be MUCH closer than full image.
        if best<12 and best<full*0.48:
            return {"status":"reject","reason":"severe_center_crop",
                    "crop_fraction":fraction,"full_difference":round(full,2),
                    "crop_difference":round(best,2)}
        return {"status":"abstain","reason":"no_high_confidence_crop_pattern",
                "full_difference":round(full,2),
                "best_crop_difference":round(best,2)}
