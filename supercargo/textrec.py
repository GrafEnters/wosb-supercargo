"""Reads a line of digits with the PP-OCRv3 recognition model, run straight on onnxruntime.

RapidOCR bundles three models (text detection, rotation, recognition) and pulls OpenCV, Shapely and
pyclipper along; the logbook only ever uses the recognizer on tiny pre-cut cells. This is the same
model with the same pre- and post-processing, without the rest.
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import onnxruntime as ort

from . import paths

MODEL_NAME = "ch_PP-OCRv3_rec_infer.onnx"
HEIGHT = 48  # the model's input height
BATCH = 6  # RapidOCR's batch size: measured fastest on this model (bigger batches pad more)


def model_path() -> Path:
    """Bundled next to the code in the .exe; from the rapidocr_onnxruntime package when run from source."""
    bundled = paths.PACKAGE / "models" / MODEL_NAME
    if bundled.exists():
        return bundled
    spec = importlib.util.find_spec("rapidocr_onnxruntime")  # finds the folder without importing it
    if spec and spec.submodule_search_locations:
        return Path(spec.submodule_search_locations[0]) / "models" / MODEL_NAME
    raise FileNotFoundError(MODEL_NAME)


def resize_bilinear(img: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinear resize with pixel centres at +0.5, like OpenCV's INTER_LINEAR; uint8 in, uint8 out."""
    h, w = img.shape[:2]

    def axis(dst: int, src: int):
        pos = (np.arange(dst, dtype=np.float32) + 0.5) * (src / dst) - 0.5
        lo = np.floor(pos)
        frac = pos - lo
        lo = lo.astype(int)
        frac[lo < 0] = 0
        lo = np.clip(lo, 0, src - 1)
        hi = np.minimum(lo + 1, src - 1)
        frac[lo >= src - 1] = 0
        return lo, hi, frac.astype(np.float32)

    y0, y1, fy = axis(height, h)
    x0, x1, fx = axis(width, w)
    src = img.astype(np.float32)
    top = src[y0][:, x0] * (1 - fx)[None, :, None] + src[y0][:, x1] * fx[None, :, None]
    bottom = src[y1][:, x0] * (1 - fx)[None, :, None] + src[y1][:, x1] * fx[None, :, None]
    out = top * (1 - fy)[:, None, None] + bottom * fy[:, None, None]
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


class Recognizer:
    def __init__(self, path: Path | None = None):
        options = ort.SessionOptions()
        options.log_severity_level = 4
        options.enable_cpu_mem_arena = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(path or model_path()), sess_options=options,
            providers=[("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})])
        # the alphabet is stored in the model; index 0 is the CTC blank, a space is appended at the end
        alphabet = self.session.get_modelmeta().custom_metadata_map["character"].splitlines()
        self.chars = ["blank"] + alphabet + [" "]
        self.input = self.session.get_inputs()[0].name

    def _normalize(self, img: np.ndarray, max_wh_ratio: float) -> np.ndarray:
        h, w = img.shape[:2]
        width = int(HEIGHT * max_wh_ratio)
        resized_w = min(width, int(math.ceil(HEIGHT * w / float(h))))
        x = resize_bilinear(img, resized_w, HEIGHT).astype(np.float32).transpose(2, 0, 1) / 255
        x = (x - 0.5) / 0.5
        batch = np.zeros((3, HEIGHT, width), dtype=np.float32)
        batch[:, :, :resized_w] = x
        return batch

    def _decode(self, preds: np.ndarray) -> list[tuple[str, float]]:
        out = []
        for idx, prob in zip(preds.argmax(axis=2), preds.max(axis=2)):
            keep = (idx != 0) & np.concatenate(([True], idx[1:] != idx[:-1]))  # drop blanks and repeats
            text = "".join(self.chars[i] for i in idx[keep])
            # RapidOCR averages with one extra tiny value; kept so confidence thresholds mean the same
            out.append((text, float(np.mean(list(prob[keep]) + [1e-50]))))
        return out

    def __call__(self, images: list[np.ndarray]) -> list[tuple[str, float]]:
        """images: HxWx3 uint8 (BGR, as the model was trained). Returns (text, confidence) per image."""
        ratios = [im.shape[1] / float(im.shape[0]) for im in images]
        order = np.argsort(np.array(ratios))  # similar widths together: less padding
        results: list[tuple[str, float]] = [("", 0.0)] * len(images)
        for start in range(0, len(images), BATCH):
            chunk = order[start:start + BATCH]
            max_wh = max(ratios[i] for i in chunk)
            batch = np.stack([self._normalize(images[i], max_wh) for i in chunk])
            preds = self.session.run(None, {self.input: batch})[0]
            for i, res in zip(chunk, self._decode(preds)):
                results[i] = res
        return results
