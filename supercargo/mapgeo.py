"""Where the world map sits on a screenshot.

The map lies on a table seen at an angle, and its zoom and position differ between sessions, so each
screenshot is matched against the clean top-down map (SIFT features + RANSAC homography). That gives
an exact screen -> map transform for that very frame; it is used to place ports seen for the first time.

OpenCV is only needed for that and is optional: the stand-alone build leaves it out (it is 110 MB),
and locate() then returns None - every port already has a fixed spot in ports_layout.json anyway.

Map coordinates are in grid cells: column A spans x 0..1, ... L spans 9..10 (no I/J);
row 1 spans y 0..1, ... row 8 spans 7..8. Cells are square, so distances are in cells.
"""
import threading
from pathlib import Path

import numpy as np
from PIL import Image

COLS, ROWS = 10, 8
COL_NAMES = "ABCDEFGHKL"
# The visible map frame (thin inner line) in cell coordinates; it lies slightly outside the labelled grid.
FRAME = (-0.611, -0.595, 10.575, 8.527)  # x0, y0, x1, y1
MARGIN = 0.12

# The clean top-down map the logbook draws; its pixel (px, py) is cell bounds()[:2] + (px, py) / 100.
REFERENCE = Path(__file__).resolve().parent / "background_map.png"
REFERENCE_PX_PER_CELL = 100
MATCH_PX_PER_CELL = 60  # the reference is matched at this scale: plenty of features, quicker to search
MIN_INLIERS = 40


def bounds():
    x0, y0, x1, y1 = FRAME
    return x0 - MARGIN, y0 - MARGIN, x1 + MARGIN, y1 + MARGIN


def _cells_to_px(s: float) -> np.ndarray:
    """Matrix: cell coords -> pixels of a top-down image with s px per cell."""
    bx0, by0, _, _ = bounds()
    return np.array([[s, 0, -bx0 * s], [0, s, -by0 * s], [0, 0, 1]])


class MapGeometry:
    def __init__(self, to_map_h: np.ndarray):
        self.to_map_h = to_map_h / to_map_h[2, 2]

    def to_map(self, x, y) -> tuple[float, float]:
        u, v, w = self.to_map_h @ (x, y, 1)
        return u / w, v / w


def cell_name(mx: float, my: float) -> str:
    c = COL_NAMES[min(max(int(mx), 0), COLS - 1)]
    return f"{c}{min(max(int(my), 0), ROWS - 1) + 1}"


class _Matcher:
    """Lazily built SIFT index of the reference map."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sift = None

    def _init(self):
        import cv2  # optional: see the module docstring
        self.cv2 = cv2
        ref = Image.open(REFERENCE).convert("L")
        s = MATCH_PX_PER_CELL / REFERENCE_PX_PER_CELL
        ref = ref.resize((round(ref.width * s), round(ref.height * s)), Image.LANCZOS)
        self.sift = cv2.SIFT_create(4000)
        self.ref_kp, self.ref_desc = self.sift.detectAndCompute(np.asarray(ref), None)
        self.bf = cv2.BFMatcher()

    def locate(self, img: Image.Image) -> MapGeometry | None:
        with self.lock:
            if self.sift is None:
                try:
                    self._init()
                except ImportError:
                    return None
            cv2 = self.cv2
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
        return MapGeometry(np.linalg.inv(_cells_to_px(MATCH_PX_PER_CELL)) @ h)


_matcher = _Matcher()


def locate(img: Image.Image) -> MapGeometry | None:
    """Find the map on a game screenshot. None if the map isn't visible."""
    return _matcher.locate(img)
