"""Template matching in plain numpy: normalized cross-correlation (what OpenCV calls TM_CCOEFF_NORMED).

The numerator is a correlation, done through FFT; the per-window mean and variance come from integral
images. That is all the logbook needed OpenCV for on the hot path - and OpenCV weighs 110 MB.
"""
import numpy as np


def _fast_len(n: int) -> int:
    """Smallest size >= n made of factors 2, 3 and 5: FFT is quick on those."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _box_sums(a: np.ndarray, h: int, w: int) -> np.ndarray:
    """Sum of every h x w window (valid positions only) via an integral image."""
    c = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    np.cumsum(np.cumsum(a, axis=0), axis=1, out=c[1:, 1:])
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def ncc(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Correlation score for every placement of template inside image, in [-1, 1].
    Result shape: (H - th + 1, W - tw + 1); index [y, x] is the template's top-left corner."""
    img = image.astype(np.float64)
    tpl = template.astype(np.float64)
    th, tw = tpl.shape
    H, W = img.shape
    if H < th or W < tw:
        return np.zeros((0, 0))
    t = tpl - tpl.mean()
    t_norm = np.sqrt((t * t).sum())
    shape = (_fast_len(H + th - 1), _fast_len(W + tw - 1))
    # correlation = convolution with the template flipped both ways
    spectrum = np.fft.rfft2(img, shape) * np.fft.rfft2(t[::-1, ::-1], shape)
    numerator = np.fft.irfft2(spectrum, shape)[th - 1:H, tw - 1:W]
    n = th * tw
    s1 = _box_sums(img, th, tw)
    s2 = _box_sums(img * img, th, tw)
    variance = np.maximum(s2 - s1 * s1 / n, 0)
    denominator = np.sqrt(variance) * t_norm
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(denominator > 1e-6, numerator / denominator, 0.0)
    return np.clip(score, -1.0, 1.0)


def best(image: np.ndarray, template: np.ndarray) -> tuple[float, int, int]:
    """(score, x, y) of the best placement."""
    r = ncc(image, template)
    if r.size == 0:
        return -1.0, 0, 0
    y, x = np.unravel_index(int(np.argmax(r)), r.shape)
    return float(r[y, x]), int(x), int(y)


def half(a: np.ndarray) -> np.ndarray:
    """Half-size copy: every 2x2 block averaged (OpenCV's INTER_AREA at exactly 0.5)."""
    h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
    a = a[:h, :w].astype(np.float32)
    return (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4


def _peaks(r: np.ndarray, k: int, radius: int) -> list[tuple[int, int]]:
    """Top-k local maxima (y, x), each at least `radius` away from the others."""
    r = r.copy()
    out = []
    for _ in range(k):
        y, x = np.unravel_index(int(np.argmax(r)), r.shape)
        if r[y, x] <= -1:
            break
        out.append((int(y), int(x)))
        r[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1] = -1
    return out


def _refine(image: np.ndarray, template: np.ndarray, x: int, y: int, slack: int) -> tuple[float, int, int]:
    """Exact best placement within `slack` pixels of (x, y)."""
    th, tw = template.shape
    x0, y0 = max(0, x - slack), max(0, y - slack)
    window = image[y0:y + th + slack, x0:x + tw + slack]
    score, dx, dy = best(window, template)
    return score, x0 + dx, y0 + dy


def find(image: np.ndarray, template: np.ndarray, candidates: int = 3) -> tuple[float, int, int]:
    """Best placement of template in a big image, searched coarse-to-fine: a quarter-size pass proposes
    a few candidates, then each is refined at half and at full size. Returns (score, x, y) at full size."""
    img2, tpl2 = half(image), half(template)
    img4, tpl4 = half(img2), half(tpl2)
    coarse = ncc(img4, tpl4)
    if coarse.size == 0:
        return -1.0, 0, 0
    best_hit = (-1.0, 0, 0)
    for cy, cx in _peaks(coarse, candidates, radius=max(tpl4.shape)):
        _, hx, hy = _refine(img2, tpl2, cx * 2, cy * 2, slack=4)
        hit = _refine(image, template, hx * 2, hy * 2, slack=4)
        if hit[0] > best_hit[0]:
            best_hit = hit
    return best_hit
