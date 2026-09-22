"""Two digit models side by side on many variants of the test frames.

Each hand-checked frame is shifted by a pixel or two and made a bit darker or brighter: for OCR these are
new pictures, while the right answers stay the same. Both models read every variant; accuracy is counted
against data/truth.json. Used to decide whether a smaller model is as good as the original.

    python -m tests.robustness data/quant/rec_fp16w.onnx
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageEnhance

from supercargo import bench, numocr, paths, textrec, tooltip

SHIFTS = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (-1, 0), (0, -1), (-2, -1)]
BRIGHTNESS = [0.88, 1.0, 1.12]


def variants(img: Image.Image):
    for dx, dy in SHIFTS:
        shifted = Image.new("RGB", img.size)
        shifted.paste(img, (dx, dy))
        for k in BRIGHTNESS:
            yield f"{dx:+d}{dy:+d}×{k}", ImageEnhance.Brightness(shifted).enhance(k)


def read_with(model: textrec.Recognizer, img: Image.Image) -> dict:
    numocr._engine = model
    try:
        info, _, _ = tooltip.read_from_screenshot(img)
    except tooltip.TooltipNotFound as e:
        return {"error": str(e)}
    return {"name": info.name, "tax": info.tax, "shallow": info.shallow,
            "goods": [{"name": g.name, "buy": g.buy, "sell": g.sell, "stock": g.stock} for g in info.goods]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate", type=Path, help="the model to compare with the original")
    ap.add_argument("--frames", type=Path, default=paths.DATA / "bench")
    ap.add_argument("--truth", type=Path, default=paths.DATA / "truth.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    models = {"исходная": textrec.Recognizer(), "кандидат": textrec.Recognizer(args.candidate)}
    tally = {k: [0, 0] for k in models}  # correct fields, all fields
    disagree = 0
    for f in bench.frames_in([args.frames]):
        if f.name not in truth:
            continue
        want = {k: v for k, v in truth[f.name].items() if k != "header"}
        for label, img in variants(Image.open(f).convert("RGB")):
            got = {k: read_with(m, img) for k, m in models.items()}
            for k, res in got.items():
                errors = bench.compare(f.name, want, res)
                total = bench.field_count(want)
                tally[k][0] += total - len(errors)
                tally[k][1] += total
            if got["исходная"] != got["кандидат"]:
                disagree += 1
                for d in bench.compare(f"{f.name} {label}", got["исходная"], got["кандидат"]):
                    print("   ", d)
    for k, (ok, total) in tally.items():
        print(f"{k}: {ok}/{total} полей верно ({ok / total:.2%})")
    print(f"вариантов, где модели прочитали по-разному: {disagree}")


if __name__ == "__main__":
    main()
