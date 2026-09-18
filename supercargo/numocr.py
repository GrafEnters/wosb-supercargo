"""Digit recognition for small price/stock cells via RapidOCR (recognition model only).

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
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def prep(img: Image.Image, lo: int = 60, hi: int = 200) -> Image.Image:
    """Colored text on dark background -> dark text on white, contrast-stretched."""
    a = np.asarray(img.convert("RGB")).astype(np.float32).max(axis=2)
    a = np.clip((a - lo) / (hi - lo), 0, 1) * 255
    return Image.fromarray(255 - a.astype(np.uint8)).convert("RGB")


def read_cell(img: Image.Image) -> tuple[str, float]:
    """Recognize a single-line crop. Returns (text, confidence)."""
    arr = np.asarray(prep(img))[:, :, ::-1]  # RGB -> BGR
    res, _ = _get_engine().text_recognizer([np.ascontiguousarray(arr)])
    text, score = res[0]
    return unicodedata.normalize("NFKC", text).strip(), float(score)


_NUM_RE = re.compile(r"(\d+(?:[.,:]\d+)?)\s*([kкKКmмMМ]?)")


def parse_number(text: str) -> float | None:
    """'3,9' -> 3.9, '168k' -> 168000, '(12,4k)' -> 12400."""
    m = _NUM_RE.search(text.replace(" ", ""))
    if not m:
        return None
    value = float(m.group(1).replace(",", ".").replace(":", "."))
    suffix = m.group(2).lower()
    if suffix in ("k", "к"):
        value *= 1_000
    elif suffix in ("m", "м"):
        value *= 1_000_000
    return value
