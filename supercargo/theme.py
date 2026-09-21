"""Look of the supercargo's logbook: dark wood, parchment, ink and brass. Small tk widgets styled to match."""
import tkinter as tk

import numpy as np
from PIL import Image

WOOD = "#241811"        # window background, dark walnut
WOOD_LIGHT = "#3a2a1d"  # frame edges
PAPER = "#e8dcbd"       # parchment page
PAPER_DIM = "#dccd9f"   # hovered / secondary areas
PAPER_LINE = "#c9b98a"  # rules and borders on paper
PAPER_HALO = "#d9cfb0"  # soft halo around course lines on the map
INK = "#2b2118"
INK_SOFT = "#5c4b3a"
INK_FAINT = "#8f7f6a"
BRASS = "#c8a24a"
BRASS_LIGHT = "#e3c46a"
BRASS_DARK = "#8f6f28"
WAX = "#8c2a2a"
SEA = "#2f6f5e"
SEA_LIGHT = "#5f9d8b"
AMBER = "#b07a1e"

# Course colours: inks a navigator would have in the drawer.
ROUTE_COLORS = ["#9b2c2c", "#2b4a7a", "#2f6f5e", "#b07a1e", "#6b3b7a"]

SERIF = "Palatino Linotype"
F_TITLE = (SERIF, 18, "bold")
F_SUBTITLE = (SERIF, 10, "italic")
F_HEAD = (SERIF, 12, "bold")
F_UI = (SERIF, 10)
F_UI_BOLD = (SERIF, 10, "bold")
F_SMALL = (SERIF, 9)
F_SMALL_ITALIC = (SERIF, 9, "italic")
F_NUM = ("Consolas", 10)
F_NUM_SMALL = ("Consolas", 9)
F_MAP = (SERIF, 9, "bold")
F_MAP_SMALL = (SERIF, 8, "bold")
F_SYMBOL = ("Segoe UI Symbol", 11)


def mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colours, t=0 -> a, t=1 -> b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


_wood_cache: dict[tuple[int, int], Image.Image] = {}


def wood_texture(w: int, h: int) -> Image.Image:
    """Procedural dark-wood grain for the table under the map."""
    key = (w, h)
    if key not in _wood_cache:
        rng = np.random.default_rng(11)
        x = np.arange(w, dtype=np.float32)
        grain = np.zeros(w, dtype=np.float32)
        for freq, amp in ((0.021, 1.0), (0.057, 0.55), (0.19, 0.25)):
            grain += amp * np.sin(x * freq + rng.uniform(0, 6.28))
        grain += np.convolve(rng.normal(0, 1, w + 40), np.ones(20) / 20, mode="valid")[:w] * 0.9
        grain = (grain - grain.mean()) / (grain.std() + 1e-6)
        base = np.array([40, 27, 18], dtype=np.float32)
        col = base[None, :] + grain[:, None] * np.array([6, 4, 2.5], dtype=np.float32)
        img = np.repeat(col[None, :, :], h, axis=0)
        img += rng.normal(0, 2.2, (h, w, 1)).astype(np.float32)
        # plank seams every so often
        for sx in range(0, w, 230):
            img[:, max(0, sx - 1):sx + 1] -= 10
        _wood_cache.clear()
        _wood_cache[key] = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return _wood_cache[key]


class Button(tk.Label):
    """Flat brass button (primary) or ink-outlined one (secondary) with hover and press states."""

    def __init__(self, parent, text, command, primary=False, **kw):
        self.command = command
        self.primary = primary
        if primary:
            self.colors = dict(bg=BRASS, hover=BRASS_LIGHT, press=BRASS_DARK, fg=INK, border=BRASS_DARK)
        else:
            self.colors = dict(bg=PAPER, hover=PAPER_DIM, press=PAPER_LINE, fg=INK, border=INK_FAINT)
        super().__init__(parent, text=text, font=F_UI_BOLD, padx=14, pady=4, cursor="hand2",
                         bg=self.colors["bg"], fg=self.colors["fg"], highlightthickness=1,
                         highlightbackground=self.colors["border"], **kw)
        self.bind("<Enter>", lambda e: self.config(bg=self.colors["hover"]))
        self.bind("<Leave>", lambda e: self.config(bg=self.colors["bg"]))
        self.bind("<ButtonPress-1>", lambda e: self.config(bg=self.colors["press"]))
        self.bind("<ButtonRelease-1>", self._release)

    def _release(self, e):
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self.config(bg=self.colors["hover"] if inside else self.colors["bg"])
        if inside:
            self.command()


class Chip(tk.Label):
    """One option of a radio group, drawn as a small brass/paper tab."""

    def __init__(self, parent, text, value, variable: tk.StringVar, command):
        self.value, self.variable, self.command = value, variable, command
        super().__init__(parent, text=text, font=F_SMALL, padx=9, pady=2, cursor="hand2",
                         highlightthickness=1)
        self.bind("<Button-1>", self._pick)
        self.bind("<Enter>", lambda e: self._paint(hover=True))
        self.bind("<Leave>", lambda e: self._paint())
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _pick(self, _):
        if self.variable.get() != self.value:
            self.variable.set(self.value)
            self.command()

    def _paint(self, hover=False):
        if self.variable.get() == self.value:
            self.config(bg=BRASS if not hover else BRASS_LIGHT, fg=INK, highlightbackground=BRASS_DARK)
        else:
            self.config(bg=PAPER if not hover else PAPER_DIM, fg=INK_SOFT, highlightbackground=PAPER_LINE)


class Divider(tk.Canvas):
    """Thin rule with a brass diamond in the middle."""

    def __init__(self, parent, bg=PAPER, line=PAPER_LINE, **kw):
        super().__init__(parent, height=14, bg=bg, highlightthickness=0, bd=0, **kw)
        self.line = line
        self.bind("<Configure>", self._draw)

    def _draw(self, e):
        self.delete("all")
        w, y = e.width, 7
        self.create_line(0, y, w / 2 - 10, y, fill=self.line)
        self.create_line(w / 2 + 10, y, w, y, fill=self.line)
        self.create_polygon(w / 2 - 4, y, w / 2, y - 4, w / 2 + 4, y, w / 2, y + 4, fill=BRASS, outline=BRASS_DARK)


class ThinScrollbar(tk.Canvas):
    """6 px scrollbar in brass; hides itself when everything fits."""

    def __init__(self, parent, command, bg=PAPER):
        super().__init__(parent, width=6, bg=bg, highlightthickness=0, bd=0)
        self.command = command
        self.lo, self.hi = 0.0, 1.0
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)

    def set(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self._draw()

    def _draw(self):
        self.delete("all")
        h = self.winfo_height()
        if self.hi - self.lo >= 1.0 or h < 10:
            return
        self.create_rectangle(2, 0, 4, h, fill=PAPER_LINE, outline="")
        y0, y1 = self.lo * h, max(self.hi * h, self.lo * h + 18)
        self.create_rectangle(0, y0, 6, y1, fill=BRASS, outline="")

    def _press(self, e):
        self._grab = (e.y, self.lo)

    def _drag(self, e):
        y, lo = self._grab
        h = max(self.winfo_height(), 1)
        self.command("moveto", max(0.0, min(1.0 - (self.hi - self.lo), lo + (e.y - y) / h)))


class Field(tk.Frame):
    """Labelled number entry on parchment with a brass underline."""

    def __init__(self, parent, label, variable: tk.IntVar, on_change, width=8):
        super().__init__(parent, bg=PAPER)
        tk.Label(self, text=label, bg=PAPER, fg=INK_SOFT, font=F_SMALL).pack(side=tk.LEFT)
        self.entry = tk.Entry(self, textvariable=variable, width=width, font=F_NUM, justify=tk.RIGHT,
                              bg=PAPER_DIM, fg=INK, insertbackground=INK, relief=tk.FLAT, bd=0,
                              highlightthickness=1, highlightbackground=PAPER_LINE, highlightcolor=BRASS)
        self.entry.pack(side=tk.LEFT, padx=(5, 0), ipady=2)
        self.entry.bind("<Return>", lambda e: on_change())
        self.entry.bind("<FocusOut>", lambda e: on_change())
