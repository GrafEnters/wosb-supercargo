"""Map window: scan ports with the hotkey, see progress on the map, get routes drawn as arrows."""
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

from . import capture, router, tooltip
from .store import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"
BACKGROUND = DATA_DIR / "background.png"

VK = {f"F{i}": 0x6F + i for i in range(1, 13)}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

ROUTE_COLORS = ["#d62828", "#1d4ed8", "#7b2cbf", "#2a9d3f", "#f77f00"]
SORT_LABELS = {"margin": "Маржа %", "profit": "Прибыль/шт", "distance": "Прибыль/путь"}
MARKER_R = 13


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
            self.out.put(("error", str(e), None, None))
            winsound.Beep(400, 150)
            return
        try:
            info, crop = tooltip.read_from_screenshot(img, self.known_goods())
            DEBUG_DIR.mkdir(exist_ok=True)
            crop.save(DEBUG_DIR / "last_tooltip.png")
            if not info.goods:
                raise tooltip.TooltipNotFound("в подсказке не найдено товаров")
        except Exception as e:
            if not isinstance(e, tooltip.TooltipNotFound):
                traceback.print_exc()
            DEBUG_DIR.mkdir(exist_ok=True)
            img.save(DEBUG_DIR / f"fail_{int(time.time())}.png")
            self.out.put(("error", str(e), cursor, img))
            winsound.Beep(400, 150)
            return
        self.out.put(("scan", info, cursor, img))
        winsound.Beep(1200, 100)


class App(tk.Tk):
    def __init__(self, key: str = "F8"):
        super().__init__()
        self.title("Суперкарго - World of Sea Battle")
        self.geometry("1500x860")
        self.configure(bg="#1e1e1e")
        self.store = Store()
        self.key = key
        self.frames: deque[np.ndarray] = deque(maxlen=5)
        self.bg_image: Image.Image | None = Image.open(BACKGROUND).convert("RGB") if BACKGROUND.exists() else None
        self.bg_tk = None
        self.routes: list[router.Route] = []
        self.selected: int | None = None
        self.flash: tuple[tuple[int, int], float] | None = None  # failed-scan position and time
        self.sort = tk.StringVar(value="margin")
        self.events: queue.Queue = queue.Queue()

        self._build_ui()
        if all(self.store.is_scanned(n) for n in self.store.ports) and len(self.store.ports) >= 2:
            self.build_routes()
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

    def update_status(self):
        names = list(self.store.ports)
        done = sum(self.store.is_scanned(n) for n in names)
        self.status.config(text=f"Отсканировано портов: {done} / {len(names)}")
        if len(names) == 0:
            hint = f"Открой карту в игре, наведи курсор на порт и нажми {self.key}. Каждый новый порт добавится на карту."
        elif done < len(names):
            hint = (f"Наведи курсор на серые порты и нажми {self.key}. Когда все станут зелёными, "
                    f"маршрут построится сам. Новые порты добавляются автоматически. "
                    f"ПКМ по порту - забыть его.")
        else:
            hint = f"Все порты отсканированы. {self.key} над портом обновит его цены."
        self.hint.config(text=hint)

    # ---------- geometry ----------
    def _transform(self):
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        iw, ih = self.bg_image.size if self.bg_image else (1920, 1051)
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
        if self.bg_image:
            size = (max(1, int(self.bg_image.width * s)), max(1, int(self.bg_image.height * s)))
            self.bg_tk = ImageTk.PhotoImage(self.bg_image.resize(size, Image.LANCZOS))
            c.create_image(ox, oy, image=self.bg_tk, anchor="nw")
        else:
            c.create_text(c.winfo_width() / 2, c.winfo_height() / 2, fill="#888", font=("Segoe UI", 14),
                          text=f"Фон карты появится после первого скана ({self.key} над портом)")

        routes = list(enumerate(self.routes[:len(ROUTE_COLORS)]))
        if self.selected is not None:
            routes = [(i, r) for i, r in routes if i == self.selected] or \
                     [(self.selected, self.routes[self.selected])]
        for i, r in reversed(routes):
            self.draw_route(r, ROUTE_COLORS[i % len(ROUTE_COLORS)], bold=self.selected is not None)

        for name, p in self.store.ports.items():
            if p.get("map_pos"):
                self.draw_port(name, self.to_canvas(p["map_pos"]), self.store.is_scanned(name))

        if self.flash and time.time() - self.flash[1] < 2.5:
            x, y = self.to_canvas(self.flash[0])
            c.create_oval(x - 18, y - 18, x + 18, y + 18, outline="#ff3030", width=3)
            c.create_text(x, y - 28, text="не распознано", fill="#ff5050", font=("Segoe UI", 10, "bold"))
        self.update_status()

    def draw_port(self, name, xy, scanned):
        c, (x, y), r = self.canvas, xy, MARKER_R
        fill, outline = ("#2a9d3f", "#dfffe6") if scanned else ("#6b6b6b", "#d0d0d0")
        c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=2, tags=("port", name))
        c.create_text(x, y, text="✓" if scanned else "?", fill="white", font=("Segoe UI", 11, "bold"),
                      tags=("port", name))
        self._label(x, y + r + 9, short(name), "#111", "#f0e6c8")

    def _label(self, x, y, text, fg, bg, font=("Segoe UI", 9, "bold")):
        t = self.canvas.create_text(x, y, text=text, fill=fg, font=font)
        x0, y0, x1, y1 = self.canvas.bbox(t)
        rect = self.canvas.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1, fill=bg, outline="")
        self.canvas.tag_lower(rect, t)

    def draw_route(self, route: router.Route, color, bold=False):
        sp, dp = self.store.ports[route.src].get("map_pos"), self.store.ports[route.dst].get("map_pos")
        if not sp or not dp:
            return
        (x0, y0), (x1, y1) = self.to_canvas(sp), self.to_canvas(dp)
        length = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        nx, ny = -uy * 5, ux * 5  # shift sideways so A->B and B->A don't overlap
        pad = MARKER_R + 3
        width = 6 if bold else 4
        self.canvas.create_line(x0 + ux * pad + nx, y0 + uy * pad + ny, x1 - ux * pad + nx, y1 - uy * pad + ny,
                                fill=color, width=width, arrow=tk.LAST, arrowshape=(16, 20, 7), capstyle=tk.ROUND)
        d = route.best
        self._label((x0 + x1) / 2 + nx * 3, (y0 + y1) / 2 + ny * 3,
                    f"{d.good} +{d.margin:.0%}", "white", color)

    def refresh_list(self):
        self.route_list.delete(0, tk.END)
        if not self.routes:
            self.route_list.insert(tk.END, "Маршрутов пока нет.")
            return
        for i, r in enumerate(self.routes[:30]):
            mark = "■" if i < len(ROUTE_COLORS) else " "
            self.route_list.insert(tk.END, f"{mark} {short(r.src)} -> {short(r.dst)}")
            if i < len(ROUTE_COLORS):
                self.route_list.itemconfig(tk.END, fg=ROUTE_COLORS[i])
            for d in r.deals[:3]:
                self.route_list.insert(tk.END, f"     {d.good:<12} {d.buy:g} -> {d.sell:g}  +{d.profit:.2f} ({d.margin:.0%})")
                self.route_list.itemconfig(tk.END, fg="#b8b8b8")

    # ---------- actions ----------
    def build_routes(self):
        self.routes = router.find_routes(self.store.ports, self.sort.get())
        self.selected = None
        self.refresh_list()
        self.redraw()

    def refresh(self):
        self.store.new_session()
        self.routes, self.selected = [], None
        self.refresh_list()
        self.redraw()

    def on_select(self, _):
        sel = self.route_list.curselection()
        if not sel or not self.routes:
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
                    self.build_routes() if self.routes else self.redraw()
                return

    def add_frame(self, img: Image.Image):
        """Background = per-pixel median of recent screenshots, which erases the tooltips."""
        arr = np.asarray(img.convert("RGB"))
        if self.frames and self.frames[0].shape != arr.shape:
            self.frames.clear()
        self.frames.append(arr)
        if len(self.frames) >= 3:
            bg = Image.fromarray(np.median(np.stack(self.frames), axis=0).astype(np.uint8))
        elif self.bg_image is None or self.bg_image.size != img.size:
            bg = img.convert("RGB")
        else:
            return
        self.bg_image = bg
        DATA_DIR.mkdir(exist_ok=True)
        bg.save(BACKGROUND)

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
                    _, msg, cursor, img = ev
                    print("scan failed:", msg)
                    if cursor:
                        self.flash = (cursor, time.time())
                        self.after(2600, self.redraw)
                    if img is not None:
                        self.add_frame(img)
                    self.redraw()
                elif kind == "scan":
                    _, info, cursor, img = ev
                    name = self.store.update(info, map_pos=cursor)
                    print(f"scanned: {name} ({len(info.goods)} goods)")
                    self.add_frame(img)
                    names = list(self.store.ports)
                    if len(names) >= 2 and all(self.store.is_scanned(n) for n in names):
                        self.build_routes()
                        self.bring_to_front()
                    elif self.routes:
                        self.build_routes()  # keep an existing route view up to date
                    else:
                        self.redraw()
        except queue.Empty:
            pass
        self.after(50, self.poll)


def run(key: str = "F8"):
    App(key.upper()).mainloop()
