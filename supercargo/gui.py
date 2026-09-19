"""Map window: scan ports with the hotkey, see progress on the map, get routes drawn as arrows.

Two modes:
  collect - after "Обновить данные": every known port is gray until rescanned, then gets a green check;
  routes  - best routes drawn as arrows on the map, no scan markers.
"""
import ctypes
import ctypes.wintypes as wt
import math
import queue
import threading
import time
import tkinter as tk
import traceback
import winsound
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

from . import capture, mapgeo, router, tooltip
from .store import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"
BACKGROUND = DATA_DIR / "background_map.png"  # top-down map, VIEW_PX_PER_CELL px per cell

VK = {f"F{i}": 0x6F + i for i in range(1, 13)}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

ROUTE_COLORS = ["#d62828", "#1d4ed8", "#7b2cbf", "#2a9d3f", "#f77f00"]
SORT_LABELS = {"margin": "Маржа %", "profit": "Прибыль/шт", "distance": "Прибыль/клетку пути"}
MARKER_R = 13
VIEW_PX_PER_CELL = 100


def short(port: str) -> str:
    return port.replace("Бухта ", "").replace("Порт ", "")


class HotkeyWorker(threading.Thread):
    """Owns the global hotkey; on press grabs the screen and parses the tooltip."""

    def __init__(self, key: str, out: queue.Queue, known_goods):
        super().__init__(daemon=True)
        self.key, self.out, self.known_goods = key, out, known_goods

    def run(self):
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, MOD_NOREPEAT, VK[self.key]):
            self.out.put(("fatal", f"Клавиша {self.key} занята другой программой"))
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.scan()

    def scan(self):
        try:
            img, _, cursor = capture.grab_game()
        except Exception as e:
            self.out.put(("error", str(e), None))
            winsound.Beep(400, 150)
            return
        self.process(img, cursor)

    def process(self, img: Image.Image, cursor: tuple[int, int]):
        geo = None
        try:
            geo = mapgeo.locate(img)
            info, crop, box = tooltip.read_from_screenshot(img, self.known_goods())
            DEBUG_DIR.mkdir(exist_ok=True)
            crop.save(DEBUG_DIR / "last_tooltip.png")
            if not info.goods:
                raise tooltip.TooltipNotFound("в подсказке не найдено товаров")
            if geo is None:
                raise tooltip.TooltipNotFound("карта не распознана - открой карту мира")
        except Exception as e:
            if not isinstance(e, tooltip.TooltipNotFound):
                traceback.print_exc()
            DEBUG_DIR.mkdir(exist_ok=True)
            img.save(DEBUG_DIR / f"fail_{int(time.time())}.png")
            self.out.put(("error", str(e), geo.to_map(*cursor) if geo else None))
            winsound.Beep(400, 150)
            return
        # Top-down frame for the background, with the tooltip area marked invalid.
        frame = geo.rectify(img, VIEW_PX_PER_CELL)
        mask = Image.new("L", img.size, 255)
        l, t, r, b = box
        pad = 30  # the estimated box can be a bit smaller than the real tooltip
        mask.paste(0, (max(0, l - pad), max(0, t - pad), min(img.width, r + pad), min(img.height, b + pad)))
        mask = geo.rectify(mask, VIEW_PX_PER_CELL)
        self.out.put(("scan", info, geo.to_map(*cursor), frame, mask))
        winsound.Beep(1200, 100)


class App(tk.Tk):
    def __init__(self, key: str = "F8"):
        super().__init__()
        self.title("Суперкарго - World of Sea Battle")
        self.geometry("1500x900")
        self.configure(bg="#1e1e1e")
        self.store = Store()
        self.key = key
        self.frames: deque[tuple[np.ndarray, np.ndarray]] = deque(maxlen=10)  # (rgb, valid mask)
        self.view: Image.Image | None = None  # top-down background map
        self.view_tk = None
        self.view_tk_key = None
        if BACKGROUND.exists():
            self.set_background(Image.open(BACKGROUND).convert("RGB"), save=False)
        self.routes: list[router.Route] = []
        self.selected: int | None = None
        self.flash: tuple[tuple[int, int], float] | None = None  # failed-scan screen position and time
        self.sort = tk.StringVar(value="margin")
        self.events: queue.Queue = queue.Queue()

        self._build_ui()
        if self.all_scanned():
            self.build_routes()
        else:
            self.mode = "collect"
            self.refresh_list()
            self.redraw()
        HotkeyWorker(key, self.events, self.store.known_goods).start()
        self.after(50, self.poll)

    # ---------- UI ----------
    def _build_ui(self):
        self.canvas = tk.Canvas(self, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-3>", self.on_right_click)

        side = tk.Frame(self, bg="#262626", width=440)
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)
        fg, font = "#e8e8e8", ("Segoe UI", 10)

        self.status = tk.Label(side, bg="#262626", fg=fg, font=("Segoe UI", 12, "bold"),
                               justify=tk.LEFT, anchor="w", wraplength=410)
        self.status.pack(fill=tk.X, padx=12, pady=(12, 4))
        self.hint = tk.Label(side, bg="#262626", fg="#a0a0a0", font=font, justify=tk.LEFT,
                             anchor="w", wraplength=410)
        self.hint.pack(fill=tk.X, padx=12)

        btns = tk.Frame(side, bg="#262626")
        btns.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(btns, text="Обновить данные", command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(btns, text="Построить сейчас", command=self.build_routes).pack(side=tk.LEFT, padx=6)

        sort_row = tk.Frame(side, bg="#262626")
        sort_row.pack(fill=tk.X, padx=12)
        tk.Label(sort_row, text="Сортировка:", bg="#262626", fg=fg, font=font).pack(anchor="w")
        for k, label in SORT_LABELS.items():
            tk.Radiobutton(sort_row, text=label, value=k, variable=self.sort, command=self.build_routes,
                           bg="#262626", fg=fg, selectcolor="#3a3a3a", activebackground="#262626",
                           activeforeground=fg, font=font).pack(anchor="w", padx=(10, 0))

        self.route_list = tk.Listbox(side, bg="#1b1b1b", fg=fg, font=("Consolas", 10), activestyle="none",
                                     selectbackground="#444", highlightthickness=0, borderwidth=0)
        self.route_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        self.route_list.bind("<<ListboxSelect>>", self.on_select)

    def all_scanned(self) -> bool:
        names = list(self.store.ports)
        return len(names) >= 2 and all(self.store.is_scanned(n) for n in names)

    def update_status(self):
        names = list(self.store.ports)
        done = sum(self.store.is_scanned(n) for n in names)
        if self.mode == "collect":
            self.status.config(text=f"Сбор данных: {done} / {len(names)} портов")
            if not names:
                hint = f"Открой карту в игре, наведи курсор на порт и нажми {self.key}. Каждый новый порт добавится на карту."
            else:
                hint = (f"Наведи курсор на серые порты и нажми {self.key}. Когда все станут зелёными, "
                        f"маршрут построится сам. Новые порты добавляются автоматически. "
                        f"ПКМ по порту - забыть его.")
        else:
            oldest = min((p["updated"] for p in self.store.ports.values()), default=time.time())
            age = max(0.0, time.time() - oldest) / 60
            self.status.config(text=f"Маршруты по {len(names)} портам")
            hint = (f"Самым старым ценам {age:.0f} мин. {self.key} над портом обновит его цены, "
                    f"«Обновить данные» - пересканировать все порты. Клик по маршруту в списке - показать только его.")
        self.hint.config(text=hint)

    # ---------- geometry ----------
    def set_background(self, view: Image.Image, save: bool = True):
        self.view = view
        self.view_tk_key = None
        if save:
            DATA_DIR.mkdir(exist_ok=True)
            view.save(BACKGROUND)

    def view_xy(self, port: dict):
        """Port position in background-view pixels."""
        return self.cell_to_view(port["map_xy"]) if port.get("map_xy") else None

    @staticmethod
    def cell_to_view(xy):
        bx0, by0, _, _ = mapgeo.bounds()
        return (xy[0] - bx0) * VIEW_PX_PER_CELL, (xy[1] - by0) * VIEW_PX_PER_CELL

    def _transform(self):
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        bx0, by0, bx1, by1 = mapgeo.bounds()
        iw, ih = (bx1 - bx0) * VIEW_PX_PER_CELL, (by1 - by0) * VIEW_PX_PER_CELL
        s = min(cw / iw, ch / ih)
        return s, (cw - iw * s) / 2, (ch - ih * s) / 2

    def to_canvas(self, pos):
        s, ox, oy = self._transform()
        return pos[0] * s + ox, pos[1] * s + oy

    # ---------- drawing ----------
    def redraw(self):
        c = self.canvas
        c.delete("all")
        s, ox, oy = self._transform()
        if self.view:
            size = (max(1, int(self.view.width * s)), max(1, int(self.view.height * s)))
            if self.view_tk_key != size:
                self.view_tk = ImageTk.PhotoImage(self.view.resize(size, Image.LANCZOS))
                self.view_tk_key = size
            c.create_image(ox, oy, image=self.view_tk, anchor="nw")
        else:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2, fill="#888", font=("Segoe UI", 14),
                          text=f"Фон карты появится после первого скана ({self.key} над портом)")

        if self.mode == "collect":
            for name, p in self.store.ports.items():
                xy = self.view_xy(p)
                if xy:
                    self.draw_scan_marker(name, self.to_canvas(xy), self.store.is_scanned(name))
        else:
            self.draw_routes()

        if self.flash and time.time() - self.flash[1] < 2.5:
            x, y = self.to_canvas(self.cell_to_view(self.flash[0]))
            c.create_oval(x - 18, y - 18, x + 18, y + 18, outline="#ff3030", width=3)
            c.create_text(x, y - 28, text="не распознано", fill="#ff5050", font=("Segoe UI", 10, "bold"))
        self.update_status()

    def draw_scan_marker(self, name, xy, scanned):
        c, (x, y), r = self.canvas, xy, MARKER_R
        fill, outline = ("#2a9d3f", "#dfffe6") if scanned else ("#6b6b6b", "#d0d0d0")
        c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=2, tags=("port", name))
        c.create_text(x, y, text="✓" if scanned else "?", fill="white", font=("Segoe UI", 11, "bold"),
                      tags=("port", name))
        self._label(x, y + r + 9, short(name), "#111", "#f0e6c8")

    def draw_routes(self):
        shown = list(enumerate(self.routes[:len(ROUTE_COLORS)]))
        if self.selected is not None:
            shown = [(self.selected, self.routes[self.selected])]
        endpoints = {}
        for i, r in reversed(shown):
            color = ROUTE_COLORS[i % len(ROUTE_COLORS)] if i < len(ROUTE_COLORS) else "#d62828"
            self.draw_route(r, color, bold=self.selected is not None)
            endpoints.setdefault(r.src, color)
            endpoints.setdefault(r.dst, color)
        for name, color in endpoints.items():
            xy = self.view_xy(self.store.ports[name])
            if not xy:
                continue
            x, y = self.to_canvas(xy)
            r = MARKER_R - 2
            self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, width=3, tags=("port", name))
            self._label(x, y + r + 10, short(name), "#111", "#f0e6c8")

    def _label(self, x, y, text, fg, bg, font=("Segoe UI", 9, "bold")):
        t = self.canvas.create_text(x, y, text=text, fill=fg, font=font)
        x0, y0, x1, y1 = self.canvas.bbox(t)
        rect = self.canvas.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1, fill=bg, outline="")
        self.canvas.tag_lower(rect, t)

    def draw_route(self, route: router.Route, color, bold=False):
        sp, dp = self.view_xy(self.store.ports[route.src]), self.view_xy(self.store.ports[route.dst])
        if not sp or not dp:
            return
        (x0, y0), (x1, y1) = self.to_canvas(sp), self.to_canvas(dp)
        length = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        nx, ny = -uy * 5, ux * 5  # shift sideways so A->B and B->A don't overlap
        pad = MARKER_R + 1
        self.canvas.create_line(x0 + ux * pad + nx, y0 + uy * pad + ny, x1 - ux * pad + nx, y1 - uy * pad + ny,
                                fill=color, width=6 if bold else 4, arrow=tk.LAST, arrowshape=(16, 20, 7),
                                capstyle=tk.ROUND)
        d = route.best
        dist = f" · {route.distance:.1f} кл." if route.distance else ""
        self._label((x0 + x1) / 2 + nx * 3, (y0 + y1) / 2 + ny * 3, f"{d.good} +{d.margin:.0%}{dist}", "white", color)

    def refresh_list(self):
        self.route_list.delete(0, tk.END)
        if self.mode == "collect" or not self.routes:
            self.route_list.insert(tk.END, "Маршрутов пока нет." if self.mode == "routes"
                                   else "Маршруты появятся, когда все порты будут отсканированы.")
            return
        for i, r in enumerate(self.routes[:30]):
            mark = "■" if i < len(ROUTE_COLORS) else " "
            dist = f"  {r.distance:.1f} кл." if r.distance else ""
            self.route_list.insert(tk.END, f"{mark} {short(r.src)} -> {short(r.dst)}{dist}")
            if i < len(ROUTE_COLORS):
                self.route_list.itemconfig(tk.END, fg=ROUTE_COLORS[i])
            for d in r.deals[:3]:
                self.route_list.insert(tk.END, f"     {d.good:<12} {d.buy:g} -> {d.sell:g}  +{d.profit:.2f} ({d.margin:.0%})")
                self.route_list.itemconfig(tk.END, fg="#b8b8b8")

    # ---------- actions ----------
    def build_routes(self):
        self.mode = "routes"
        self.routes = router.find_routes(self.store.ports, self.sort.get())
        self.selected = None
        self.refresh_list()
        self.redraw()

    def refresh(self):
        self.mode = "collect"
        self.store.new_session()
        self.routes, self.selected = [], None
        self.refresh_list()
        self.redraw()

    def on_select(self, _):
        sel = self.route_list.curselection()
        if not sel or not self.routes or self.mode != "routes":
            return
        # Map a listbox row back to its route (each route occupies 1 + len(deals[:3]) rows).
        row, idx = sel[0], 0
        for i, r in enumerate(self.routes[:30]):
            span = 1 + len(r.deals[:3])
            if row < idx + span:
                self.selected = None if self.selected == i else i
                break
            idx += span
        self.redraw()

    def on_right_click(self, e):
        items = self.canvas.find_overlapping(e.x - 2, e.y - 2, e.x + 2, e.y + 2)
        for it in items:
            tags = self.canvas.gettags(it)
            if "port" in tags:
                name = [t for t in tags if t not in ("port", "current")][0]
                if messagebox.askyesno("Забыть порт", f"Убрать «{name}» с карты и из базы?"):
                    self.store.remove(name)
                    self.build_routes() if self.mode == "routes" else self.redraw()
                return

    def add_frame(self, frame: Image.Image, mask: Image.Image):
        """Background = per-pixel median over recent top-down frames, skipping each frame's tooltip area."""
        self.frames.append((np.asarray(frame.convert("RGB")), np.asarray(mask) > 128))
        rgbs = [f for f, _ in self.frames]
        masks = [m for _, m in self.frames]
        if self.view is not None and self.view.size == frame.size:
            prev = np.asarray(self.view.convert("RGB"))
            rgbs.append(prev)
            masks.append(prev.max(axis=2) > 0)
        self.set_background(Image.fromarray(masked_median(np.stack(rgbs), np.stack(masks))))

    def bring_to_front(self):
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "fatal":
                    messagebox.showerror("Суперкарго", ev[1])
                elif kind == "error":
                    _, msg, map_xy = ev
                    print("scan failed:", msg)
                    if map_xy:
                        self.flash = (map_xy, time.time())
                        self.after(2600, self.redraw)
                    self.redraw()
                elif kind == "scan":
                    _, info, map_xy, frame, mask = ev
                    name = self.store.update(info, map_xy=map_xy)
                    print(f"scanned: {name} ({len(info.goods)} goods) at {mapgeo.cell_name(*map_xy)}")
                    self.add_frame(frame, mask)
                    if self.mode == "routes":
                        self.build_routes()  # prices changed: keep the route view up to date
                    elif self.all_scanned():
                        self.build_routes()
                        self.bring_to_front()
                    else:
                        self.redraw()
        except queue.Empty:
            pass
        self.after(50, self.poll)


def masked_median(rgbs: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Per-pixel median over frames (N,H,W,3) using only frames where valid (N,H,W) is set."""
    data = np.where(valid[..., None], rgbs.astype(np.float32), np.inf)
    data.sort(axis=0)
    count = valid.sum(axis=0)
    # Upper median: with an even count prefer the brighter value (tooltips are dark, the map is light).
    idx = np.clip(np.minimum(count // 2, count - 1), 0, None)[None, ..., None]
    med = np.take_along_axis(data, np.broadcast_to(idx, (1, *data.shape[1:])), axis=0)[0]
    med[count == 0] = 0
    return med.astype(np.uint8)


def run(key: str = "F8"):
    App(key.upper()).mainloop()
