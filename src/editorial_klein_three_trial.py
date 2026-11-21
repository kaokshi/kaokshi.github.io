"""Private three-frame trial: one prompt per scene, FLUX.2 Klein + light OpenCV grade.
Never launches modal, never publishes media or articles.
"""
from pathlib import Path
import argparse
import json
from PIL import Image
from editorial_klein_trial import klein_edit, atmosphere_polish

SCENES = {
    1: (
        "Edit reference image 0. Preserve the original atmospheric old late-19th-century apartment corridor, "
        "the SAME young adult dark-haired woman wearing a dark forest-green coat and holding a flashlight, "
        "the impossible doorway revealing a deep snowy evergreen forest at night. Preserve the primary "
        "composition and camera viewpoint, with the door standing upright. Fix broken spatial geometry: "
        "the doorway must provide an UNOBSTRUCTED, clearly passable opening; there must be no bench, "
        "furniture, objects or wall blocking the threshold. Make floorboards and door frame geometrically "
        "continuous, physically plausible. Improve believable film-still photographic realism: natural "
        "textures, subtle fog, restrained amber light from the doorway and cold blue moonlight, "
        "natural lighting and shadow falloff, no painted/plastic look. No additional people, "
        "no signs, no lettering, no watermarks."
    ),
    2: (
        "Edit reference image 0, keeping the SAME dark-haired young woman in a forest-green coat "
        "holding the SAME small flashlight, her scale and appearance, the nighttime snowy fir forest, "
        "cold blue moonlight and the open old wooden doorway leading to the abandoned apartment corridor "
        "behind her. Correct any illogical geometry: REMOVE any wooden bench or chair blocking the "
        "doorway, so the threshold is clearly open and can be walked through; place no objects across "
        "the entrance. Snow should flow naturally across the foreground, with believable footprints "
        "leading into the forest and thin authentic mist. Preserve silhouette and viewpoint, improve "
        "film-like photorealism with physically plausible shadows, detailed snow and bark, restrained "
        "warm amber highlights near doorway, rich but natural shadows, no illustration/plastic style, "
        "no text, no watermarks, no new characters."
    ),
    3: (
        "Edit reference image 0. Preserve the scene: the SAME young adult dark-haired woman in the "
        "SAME dark forest-green coat and dark trousers holding a small flashlight inside an abandoned "
        "late-19th-century corridor at night, with peeling wallpaper, aged damp wooden floorboards, "
        "an old closed door at the far end leaking an eerie narrow line of cold blue winter light "
        "and melting snowy footprints on the floor. Correct implausible interior geometry and unnatural "
        "objects. Ensure corridor walls and floor remain structurally continuous, straight sensible "
        "doorframes and believable footprints. Atmospheric, near-photographic cinematic film still: "
        "natural material wear, realistic nuanced shadows, subdued cool moonlight, modest amber lamp "
        "glow, subtle haze. Keep same camera placement and female silhouette; no second people, "
        "no stylized painting, no exaggerated HDR, no text, no watermarks."
    ),
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for number, prompt in SCENES.items():
        original = args.scene_dir / f"cloudflare-scene-{number}.jpg"
        if not original.is_file():
            raise RuntimeError("Missing original scene " + str(number))
        dest = args.output_dir / f"scene-{number}"
        dest.mkdir(exist_ok=True)
        with Image.open(original) as image:
            image.load()
            size = image.size
            image.convert("RGB").save(dest / "01-original.jpg", quality=95)
        edited = klein_edit(original, dest / "02-klein.jpg", prompt)
        final = atmosphere_polish(edited, dest / "03-klein-polished.jpg")
        with Image.open(final) as image:
            image.verify()
        report.append({"scene": number, "original_size": list(size),
                       "klein": "completed", "opencv_pillow": "completed",
                       "prompt": prompt,
                       "files": [f"scene-{number}/01-original.jpg",
                                 f"scene-{number}/02-klein.jpg",
                                 f"scene-{number}/03-klein-polished.jpg"]})
        print("SCENE_%s_COMPLETED=true" % number)
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
