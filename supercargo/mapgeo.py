"""The in-game map lies on a table seen at an angle, and its zoom/position differs between sessions.

Each screenshot is matched against a reference top-down map (SIFT features + RANSAC homography),
which gives an exact screen <-> map transform for that very frame.

Map coordinates are in grid cells: column A spans x 0..1, ... L spans 9..10 (no I/J);
row 1 spans y 0..1, ... row 8 spans 7..8. Cells are square, so distances are in cells.
"""
import math
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

COLS, ROWS = 10, 8
COL_NAMES = "ABCDEFGHKL"
# The visible map frame (thin inner line) in cell coordinates; it lies slightly outside the labelled grid.
FRAME = (-0.611, -0.595, 10.575, 8.527)  # x0, y0, x1, y1
MARGIN = 0.12

# Reference screenshot whose frame corners were measured by hand (top-left, top-right, bottom-right, bottom-left).
REFERENCE_IMAGE = Path(__file__).resolve().parent.parent / "samples" / "live_map_1920.png"
REFERENCE_CORNERS = [(386, 229), (1380, 229), (1545, 1016), (163, 1016)]
REF_PX_PER_CELL = 60
MIN_INLIERS = 40


def _homography(src, dst) -> np.ndarray:
    """3x3 matrix H with dst ~ H @ src for 4 point pairs."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = np.linalg.solve(np.array(a, float), np.array(b, float))
    return np.append(h, 1).reshape(3, 3)


def _apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
    u, v, w = h @ (x, y, 1)
    return u / w, v / w


def bounds():
    x0, y0, x1, y1 = FRAME
    return x0 - MARGIN, y0 - MARGIN, x1 + MARGIN, y1 + MARGIN


def _cells_to_px(s: float) -> np.ndarray:
    """Matrix: cell coords -> pixels of a rectified image with s px per cell."""
    bx0, by0, _, _ = bounds()
    return np.array([[s, 0, -bx0 * s], [0, s, -by0 * s], [0, 0, 1]])


class MapGeometry:
    def __init__(self, to_map_h: np.ndarray):
        self.to_map_h = to_map_h / to_map_h[2, 2]
        self.to_screen_h = np.linalg.inv(self.to_map_h)

    @classmethod
    def from_corners(cls, corners) -> "MapGeometry":
        x0, y0, x1, y1 = FRAME
        return cls(_homography(corners, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))

    def to_map(self, x, y) -> tuple[float, float]:
        return _apply(self.to_map_h, x, y)

    def to_screen(self, mx, my) -> tuple[float, float]:
        return _apply(self.to_screen_h, mx, my)

    def rectify(self, img: Image.Image, px_per_cell: int = 100) -> Image.Image:
        """Top-down view of the map. Pixel (px, py) of the result = cell bounds()[:2] + (px, py) / s.
        Works for RGB images and for 'L' masks (areas outside the screenshot become 0)."""
        bx0, by0, bx1, by1 = bounds()
        s = px_per_cell
        w, h = round((bx1 - bx0) * s), round((by1 - by0) * s)
        # PIL wants the output->input mapping: output pixel -> map cell -> screen pixel.
        m = self.to_screen_h @ np.linalg.inv(_cells_to_px(s))
        m = m / m[2, 2]
        return img.transform((w, h), Image.PERSPECTIVE, tuple(m.flatten()[:8]), Image.BICUBIC)


def distance(a, b) -> float:
    return math.dist(a, b)


def cell_name(mx: float, my: float) -> str:
    c = COL_NAMES[min(max(int(mx), 0), COLS - 1)]
    return f"{c}{min(max(int(my), 0), ROWS - 1) + 1}"


class _Matcher:
    """Lazily built SIFT index of the reference map."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sift = None

    def _init(self):
        ref_raw = Image.open(REFERENCE_IMAGE).convert("RGB")
        ref = MapGeometry.from_corners(REFERENCE_CORNERS).rectify(ref_raw, REF_PX_PER_CELL).convert("L")
        self.sift = cv2.SIFT_create(4000)
        self.ref_kp, self.ref_desc = self.sift.detectAndCompute(np.asarray(ref), None)
        self.bf = cv2.BFMatcher()

    def locate(self, img: Image.Image) -> MapGeometry | None:
        with self.lock:
            if self.sift is None:
                self._init()
            kp, desc = self.sift.detectAndCompute(np.asarray(img.convert("L")), None)
        if desc is None or len(kp) < MIN_INLIERS:
            return None
        pairs = self.bf.knnMatch(desc, self.ref_desc, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        if len(good) < MIN_INLIERS:
            return None
        src = np.float32([kp[m.queryIdx].pt for m in good])
        dst = np.float32([self.ref_kp[m.trainIdx].pt for m in good])
        h, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if h is None or int(mask.sum()) < MIN_INLIERS:
            return None
        # screen -> reference pixels -> cells
        return MapGeometry(np.linalg.inv(_cells_to_px(REF_PX_PER_CELL)) @ h)


_matcher = _Matcher()


def locate(img: Image.Image) -> MapGeometry | None:
    """Find the map on a game screenshot. None if the map isn't visible."""
    return _matcher.locate(img)
