"""Digit recognition for small price/stock cells with the PP-OCRv3 recognizer (see textrec).

Windows OCR is good at Russian words but drops short standalone numbers like "9";
the PaddleOCR recognizer handles those reliably when fed one tight cell at a time.
"""
import re
import unicodedata

import numpy as np
from PIL import Image

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from .textrec import Recognizer
        _engine = Recognizer()
    return _engine


def prep(img: Image.Image, lo: int = 60, hi: int = 200) -> Image.Image:
    """Colored text on dark background -> dark text on white, contrast-stretched."""
    a = np.asarray(img.convert("RGB")).astype(np.float32).max(axis=2)
    a = np.clip((a - lo) / (hi - lo), 0, 1) * 255
    return Image.fromarray(255 - a.astype(np.uint8)).convert("RGB")


class Cell:
    """A price cell queued for recognition; text and score appear after BatchReader.run()."""

    def __init__(self, reader: "BatchReader", index: int):
        self.reader, self.index = reader, index

    @property
    def text(self) -> str:
        return self.reader.results[self.index][0]

    @property
    def score(self) -> float:
        return self.reader.results[self.index][1]


class BatchReader:
    """All cells of one tooltip go through the recognizer in a single pass - it is ~25% faster
    than one call per cell, and the per-call overhead is paid once."""

    def __init__(self):
        self.images: list[Image.Image] = []
        self.results: list[tuple[str, float]] = []

    def __call__(self, img: Image.Image) -> Cell:
        self.images.append(img)
        return Cell(self, len(self.images) - 1)

    def run(self):
        if not self.images:
            return
        arrays = [np.ascontiguousarray(np.asarray(prep(im))[:, :, ::-1]) for im in self.images]
        res = _get_engine()(arrays)
        self.results = [(unicodedata.normalize("NFKC", t).strip(), float(score)) for t, score in res]


_NUM_RE = re.compile(r"(\d+(?:[.,:]\d+)?)\s*([kкKКmмMМ]?)")


def parse_number(text: str) -> float | None:
    """'3,9' -> 3.9, '168k' -> 168000, '(12,4k)' -> 12400."""
    m = _NUM_RE.search(text.replace(" ", ""))
    if not m:
        return None
    value = float(m.group(1).replace(",", ".").replace(":", "."))
    suffix = m.group(2).lower()
    if suffix in ("k", "к"):
        value = float(round(value * 1_000))  # 64.1 * 1000 is 64099.999... in floating point
    elif suffix in ("m", "м"):
        value = float(round(value * 1_000_000))
    return value
