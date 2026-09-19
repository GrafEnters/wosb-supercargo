"""Find the port tooltip on a game screenshot and parse it into structured data."""
import difflib
import re
import statistics
from dataclasses import dataclass, field

from PIL import Image

from . import numocr, ocr
from .ocr import Line, Word

KNOWN_GOODS = [
    "Древесина", "Ром", "Ткань", "Зерно", "Смола", "Свежее мясо", "Вода", "Медь", "Уголь",
    "Железо", "Животные",
]


def fix_decimal(buy: float | None, sell: float | None) -> tuple[float | None, float | None, str | None]:
    """OCR sometimes drops the decimal comma ('12,3' -> 123). Normally sell <= buy and buy < ~3x sell."""
    if buy is None or sell is None:
        return buy, sell, None
    if sell > buy and sell / 10 <= buy:
        return buy, sell / 10, f"sell {sell:g} -> {sell / 10:g} (lost decimal)"
    if buy > 3.5 * sell and buy / 10 >= sell:
        return buy / 10, sell, f"buy {buy:g} -> {buy / 10:g} (lost decimal)"
    return buy, sell, None


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


def canonical_good(text: str, extra_names=()) -> str | None:
    """Known good name for an OCR'd row label; None if it doesn't look like a good at all."""
    text = re.sub(r"^[^А-Яа-яЁё]+", "", text).strip()  # drop icon garbage before the name
    tokens = text.split()
    while len(tokens) > 1 and len(tokens[0]) <= 2:  # the item icon is sometimes read as a letter
        tokens.pop(0)
    text = " ".join(tokens).capitalize()
    match = difflib.get_close_matches(text, KNOWN_GOODS, n=1, cutoff=0.6)
    if match:
        return match[0]
    # A good we don't know yet: accept only plausible words; learned names must match closely.
    match = difflib.get_close_matches(text, list(extra_names), n=1, cutoff=0.8)
    if match:
        return match[0]
    return text if re.fullmatch(r"[А-ЯЁ][а-яё]{2,}( [а-яё]{2,})?", text) else None


def _table_header(lines: list[Line]) -> tuple[Word, Word] | None:
    """The "Купить" / "Продать" column headers: the most reliable anchor of the tooltip
    ("Торговый дом" often gets merged with its icon by OCR)."""
    buy = _find_word(lines, r"купить")
    sell = _find_word(lines, r"продать")
    if buy is None or sell is None or not (0 < sell.x - buy.x < 20 * buy.h) or abs(sell.y - buy.y) > buy.h:
        return None
    return buy, sell


def locate(img: Image.Image) -> tuple[int, int, int, int]:
    """Find the trade-house table on a full screenshot; return a crop box for the tooltip."""
    header = _table_header(ocr.recognize(img))
    if header is None:
        raise TooltipNotFound("port tooltip not found on screen")
    buy = header[0]
    h = buy.h
    # Tooltip spans ~14 text-heights left of "Купить" and ~17 right; header block above, up to ~12 goods below.
    left = max(0, int(buy.x - 14 * h))
    right = min(img.width, int(buy.x + 17 * h))
    top = max(0, int(buy.y - 22 * h))
    bottom = min(img.height, int(buy.y + 20 * h))
    return left, top, right, bottom


def parse(img: Image.Image, known_names=()) -> PortInfo:
    """Parse a tooltip image (full screenshot or crop)."""
    lines = ocr.recognize(img, scale=2.0)
    warnings: list[str] = []

    header = _table_header(lines)
    if header is None:
        raise TooltipNotFound("trade table header not found")
    buy_hdr, sell_hdr = header
    header_y = buy_hdr.y

    lots = _find_line(lines, r"лотов")
    table_bottom = lots.y if lots else img.height

    # Title: first line with "[n/n]" above the table, else the top-most line.
    title_line = None
    for ln in sorted(lines, key=lambda l: l.y):
        if ln.y < header_y and re.search(r"\[\s*\d+\s*/\s*\d+\s*\]", ln.text):
            title_line = ln
            break
    if title_line is None:
        above = [l for l in lines if l.y < header_y]
        title_line = min(above, key=lambda l: l.y) if above else None
        warnings.append("title without [n/n] marker")
    name = re.sub(r"\s*\[.*$", "", title_line.text).strip() if title_line else "?"
    name = name[:1].upper() + name[1:]

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

    # Item rows: lines between the header and "Лотов", left of "Купить", starting at a common x.
    col_gap = sell_hdr.x - buy_hdr.x
    candidates = []
    for ln in lines:
        words = [w for w in ln.words if w.x < buy_hdr.x - 5]
        if words and header_y + buy_hdr.h * 0.8 < min(w.y for w in words) < table_bottom - 3:
            candidates.append(words)
    rows = []
    if candidates:
        left_x = statistics.median(ws[0].x for ws in candidates)
        rows = [ws for ws in candidates if abs(ws[0].x - left_x) < col_gap / 2]
    rows.sort(key=lambda ws: ws[0].y)

    centers = [statistics.mean(w.y + w.h / 2 for w in ws) for ws in rows]
    pitch = statistics.median([b - a for a, b in zip(centers, centers[1:])]) if len(centers) > 1 else 20
    half = pitch * 0.45

    goods: list[Good] = []
    for ws, cy in zip(rows, centers):
        vol_words = [w for w in ws if "(" in w.text or ")" in w.text]
        name_words = [w for w in ws if w not in vol_words]
        raw = " ".join(w.text for w in ws)
        good_name = canonical_good(" ".join(w.text for w in name_words), known_names)
        if good_name is None:
            warnings.append(f"skipped row '{raw}'")
            continue
        g = Good(good_name, None, None, None, raw)

        def cell(x0, x1):
            box = (int(x0), int(cy - half), int(x1), int(cy + half))
            text, score = numocr.read_cell(img.crop(box))
            return text, score

        if vol_words:
            vx0 = min(w.x for w in vol_words) - 2
            # Windows OCR word boxes sometimes stop before "k)": read up to the price column instead.
            vx1 = buy_hdr.x - 6
            t, _ = cell(vx0, vx1)
            g.stock = numocr.parse_number(t)
            if g.stock is not None and g.stock < 1000:
                g.warnings.append(f"stock: suspicious '{t}'")
        for attr, x0 in (("buy", buy_hdr.x - 4), ("sell", sell_hdr.x - 4)):
            t, score = cell(x0, x0 + col_gap - 10)
            v = numocr.parse_number(t)
            setattr(g, attr, v)
            if v is None:
                g.warnings.append(f"{attr}: unreadable '{t}'")
            elif score < 0.4:
                g.warnings.append(f"{attr}: low confidence '{t}' ({score:.2f})")
        g.buy, g.sell, fixed = fix_decimal(g.buy, g.sell)
        if fixed:
            g.warnings.append(fixed)
        if g.buy is not None and g.sell is not None and g.sell > g.buy:
            g.warnings.append(f"sell {g.sell} > buy {g.buy}, suspicious")
        goods.append(g)

    if not goods:
        warnings.append("no goods rows found")
    return PortInfo(name, tax, shallow, goods, warnings)


def read_from_screenshot(img: Image.Image, known_names=()) -> tuple[PortInfo, Image.Image, tuple]:
    """Returns (parsed info, tooltip crop, crop box in screenshot coords)."""
    box = locate(img)
    crop = img.crop(box)
    return parse(crop, known_names), crop, box
