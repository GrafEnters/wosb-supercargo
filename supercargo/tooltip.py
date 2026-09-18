"""Find the port tooltip on a game screenshot and parse it into structured data."""
import difflib
import re
import statistics
from dataclasses import dataclass, field

from PIL import Image

from . import numocr, ocr
from .ocr import Line, Word

KNOWN_GOODS = [
    "Древесина", "Ром", "Ткань", "Зерно", "Смола", "Свежее мясо", "Вода", "Медь",
]


@dataclass
class Good:
    name: str
    buy: float | None
    sell: float | None
    stock: float | None
    raw: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class PortInfo:
    name: str
    tax: float | None
    shallow: str | None
    goods: list[Good]
    warnings: list[str] = field(default_factory=list)


class TooltipNotFound(Exception):
    pass


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def _find_line(lines: list[Line], pattern: str) -> Line | None:
    rx = re.compile(pattern, re.I)
    for ln in lines:
        if rx.search(_norm(ln.text)):
            return ln
    return None


def _find_word(lines: list[Line], pattern: str) -> Word | None:
    rx = re.compile(pattern, re.I)
    for ln in lines:
        for w in ln.words:
            if rx.fullmatch(_norm(w.text)):
                return w
    return None


def canonical_good(text: str, extra_names=()) -> str:
    text = re.sub(r"^[^А-Яа-яЁё]+", "", text).strip()  # drop icon garbage before the name
    tokens = text.split()
    while len(tokens) > 1 and len(tokens[0]) <= 2:  # the item icon is sometimes read as a letter
        tokens.pop(0)
    text = " ".join(tokens)
    candidates = list(dict.fromkeys([*KNOWN_GOODS, *extra_names]))
    match = difflib.get_close_matches(text.capitalize(), candidates, n=1, cutoff=0.7)
    return match[0] if match else text.capitalize()


def locate(img: Image.Image) -> tuple[int, int, int, int]:
    """Find the trade-house table on a full screenshot; return a crop box for the tooltip."""
    lines = ocr.recognize(img)
    anchor = _find_line(lines, r"торгов\w*\s+дом")
    if anchor is None:
        raise TooltipNotFound("'Торговый дом' not found on screen - is a port tooltip open?")
    ax, ay = anchor.x, anchor.y
    h = max(w.h for w in anchor.words)
    # Tooltip is roughly 25 text-heights wide; header above the table ~15 lines, table below ~12.
    left = max(0, int(ax - 3 * h))
    right = min(img.width, int(ax + 28 * h))
    top = max(0, int(ay - 22 * h))
    bottom = min(img.height, int(ay + 20 * h))
    return left, top, right, bottom


def parse(img: Image.Image, known_names=()) -> PortInfo:
    """Parse a tooltip image (full screenshot or crop)."""
    lines = ocr.recognize(img, scale=2.0)
    warnings: list[str] = []

    anchor = _find_line(lines, r"торгов\w*\s+дом")
    buy_hdr = _find_word(lines, r"купить")
    sell_hdr = _find_word(lines, r"продать")
    if anchor is None or buy_hdr is None or sell_hdr is None:
        raise TooltipNotFound("trade table header not found")

    lots = _find_line(lines, r"лотов")
    table_bottom = lots.y if lots else img.height

    # Title: first line with "[n/n]" above the table, else the top-most line.
    title_line = None
    for ln in sorted(lines, key=lambda l: l.y):
        if ln.y < anchor.y and re.search(r"\[\s*\d+\s*/\s*\d+\s*\]", ln.text):
            title_line = ln
            break
    if title_line is None:
        above = [l for l in lines if l.y < anchor.y]
        title_line = min(above, key=lambda l: l.y) if above else None
        warnings.append("title without [n/n] marker")
    name = re.sub(r"\s*\[.*$", "", title_line.text).strip() if title_line else "?"

    tax = None
    tax_line = _find_line(lines, r"налог")
    if tax_line:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", tax_line.text)
        tax = float(m.group(1).replace(",", ".")) if m else None
    shallow = None
    sh_line = _find_line(lines, r"мелководье")
    if sh_line:
        m = re.search(r"мелководье\s+([IVXLl1\-–]+)", sh_line.text, re.I)
        shallow = m.group(1).replace("l", "I").replace("1", "I") if m else sh_line.text

    # Item rows: lines between header and "Лотов", starting near the header's x, left of "Купить".
    col_gap = sell_hdr.x - buy_hdr.x
    rows = []
    for ln in lines:
        words = [w for w in ln.words if w.x < buy_hdr.x - 5]
        if not words or ln is anchor:
            continue
        cy = min(w.y for w in words)
        if anchor.y + anchor.words[0].h * 0.8 < cy < table_bottom - 3 and abs(words[0].x - anchor.x) < col_gap:
            rows.append(words)
    rows.sort(key=lambda ws: ws[0].y)

    centers = [statistics.mean(w.y + w.h / 2 for w in ws) for ws in rows]
    pitch = statistics.median([b - a for a, b in zip(centers, centers[1:])]) if len(centers) > 1 else 20
    half = pitch * 0.45

    goods: list[Good] = []
    for ws, cy in zip(rows, centers):
        vol_words = [w for w in ws if "(" in w.text or ")" in w.text]
        name_words = [w for w in ws if w not in vol_words]
        raw = " ".join(w.text for w in ws)
        g = Good(canonical_good(" ".join(w.text for w in name_words), known_names), None, None, None, raw)

        def cell(x0, x1):
            box = (int(x0), int(cy - half), int(x1), int(cy + half))
            text, score = numocr.read_cell(img.crop(box))
            return text, score

        if vol_words:
            vx0 = min(w.x for w in vol_words) - 2
            vx1 = max(w.x + w.w for w in vol_words) + 2
            t, _ = cell(vx0, vx1)
            g.stock = numocr.parse_number(t)
        for attr, x0 in (("buy", buy_hdr.x - 4), ("sell", sell_hdr.x - 4)):
            t, score = cell(x0, x0 + col_gap - 10)
            v = numocr.parse_number(t)
            setattr(g, attr, v)
            if v is None:
                g.warnings.append(f"{attr}: unreadable '{t}'")
            elif score < 0.4:
                g.warnings.append(f"{attr}: low confidence '{t}' ({score:.2f})")
        if g.buy is not None and g.sell is not None and g.sell > g.buy:
            g.warnings.append(f"sell {g.sell} > buy {g.buy}, suspicious")
        goods.append(g)

    if not goods:
        warnings.append("no goods rows found")
    return PortInfo(name, tax, shallow, goods, warnings)


def read_from_screenshot(img: Image.Image, known_names=()) -> tuple[PortInfo, Image.Image]:
    box = locate(img)
    crop = img.crop(box)
    return parse(crop, known_names), crop
