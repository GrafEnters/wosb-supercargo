"""Cell-level check: our recognizer against RapidOCR on every price/stock cell of every tooltip.

    python -m tests.cells --frames data/bench

Needs rapidocr_onnxruntime and OpenCV installed (the reference). Stricter than the field comparison:
it looks at the raw text and confidence of each cell, before any clean-up.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from supercargo import bench, numocr, paths, textrec, tooltip


def cells_of(img: Image.Image) -> list[Image.Image]:
    """The cell crops the parser would send to the recognizer."""
    captured = []
    original = numocr.BatchReader.run

    def spy(self):
        captured.extend(self.images)
        return original(self)

    numocr.BatchReader.run = spy
    try:
        tooltip.read_from_screenshot(img)
    except tooltip.TooltipNotFound:
        pass
    finally:
        numocr.BatchReader.run = original
    return captured


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=Path, action="append", default=[])
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    import cv2  # noqa: F401  (reference path needs it)
    from rapidocr_onnxruntime import RapidOCR

    reference = RapidOCR().text_recognizer
    ours = textrec.Recognizer()
    files = bench.frames_in([paths.DATA / "frames", *args.frames])
    total = same_text = 0
    worst = 0.0
    for f in files:
        cells = cells_of(Image.open(f).convert("RGB"))
        arrays = [np.ascontiguousarray(np.asarray(numocr.prep(c))[:, :, ::-1]) for c in cells]
        if not arrays:
            continue
        ref, _ = reference(arrays)
        got = ours(arrays)
        for (rt, rs), (gt, gs) in zip(ref, got):
            total += 1
            same_text += rt == gt
            worst = max(worst, abs(rs - gs))
            if rt != gt:
                print(f"   {f.name}: RapidOCR {rt!r} ({rs:.3f})  наш {gt!r} ({gs:.3f})")
    print(f"ячеек {total}: текст совпал в {same_text} ({same_text / max(total, 1):.1%}), "
          f"наибольшая разница уверенности {worst:.4f}")
    sys.exit(0 if same_text == total else 1)


if __name__ == "__main__":
    main()
