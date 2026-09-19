"""Map window: ports are scanned automatically while you hover them on the in-game map;
see progress on the map and get routes drawn as arrows.

Modes:
  collect - after "Обновить данные": every known port is gray until rescanned, then gets a green check;
  routes  - best routes drawn as arrows on the map, no scan markers;
  edit    - "Двигать порты": drag port markers to fix their positions (saved to ports_layout.json).
"""
import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from . import mapgeo, router
from .autoscan import AutoScanner
from .store import Store

DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"
# Clean top-down map (rectified from a screenshot with mapgeo), VIEW_PX_PER_CELL px per cell.
BACKGROUND = Path(__file__).resolve().parent / "background_map.png"

ROUTE_COLORS = ["#d62828", "#1d4ed8", "#7b2cbf", "#2a9d3f", "#f77f00"]
SORT_LABELS = {"margin": "Маржа %", "profit": "Прибыль/шт", "distance": "Прибыль/клетку пути"}
MARKER_R = 13
VIEW_PX_PER_CELL = 100


def short(port: str) -> str:
    return port.replace("Бухта ", "").replace("Порт ", "")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Суперкарго - World of Sea Battle")
        self.geometry("1500x900")
        self.configure(bg="#1e1e1e")
        self.store = Store()
        self.view = Image.open(BACKGROUND).convert("RGB")  # top-down background map
        self.view_tk = None
        self.view_tk_key = None
        self.routes: list[router.Route] = []
        self.selected: int | None = None
        self.sort = tk.StringVar(value="margin")
        self.events: queue.Queue = queue.Queue()

        self._build_ui()
        if self.all_scanned():
            self.build_routes()
        else:
            self.mode = "collect"
            self.refresh_list()
            self.redraw()
        AutoScanner(self.events, self.store.known_goods,
                    lambda name: self.store.position(self.store.resolve_name(name)) is None).start()
        self.after(50, self.poll)

    # ---------- UI ----------
    def _build_ui(self):
        self.canvas = tk.Canvas(self, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<ButtonPress-1>", self.on_drag_start)
        self.canvas.bind("<B1-Motion>", self.on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag_end)
        self.drag: dict | None = None
        self.tag_of: dict[str, str] = {}
        self.name_of: dict[str, str] = {}

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

        # Admin tools, hidden behind a dim gear in the bottom corner (packed before the list so it stays visible).
        bottom = tk.Frame(side, bg="#262626")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(0, 6))
        gear = tk.Label(bottom, text="⚙", bg="#262626", fg="#3c3c3c", font=("Segoe UI", 9), cursor="hand2")
        gear.pack(side=tk.RIGHT)
        gear.bind("<Button-1>", lambda e: self.toggle_admin())
        self.admin_frame = tk.Frame(bottom, bg="#262626")
        self.edit_btn = ttk.Button(self.admin_frame, text="Двигать порты", command=self.toggle_edit)
        self.edit_btn.pack(side=tk.LEFT)
        self.admin = False

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
        names = self.store.all_names()
        return len(names) >= 2 and all(self.store.is_scanned(n) for n in names)

    def update_status(self):
        names = self.store.all_names()
        done = sum(self.store.is_scanned(n) for n in names)
        if self.mode == "edit":
            self.status.config(text=f"Расположение портов: {len(names)}")
            hint = ("Перетаскивай порты мышью на их значки - позиция сохраняется сразу "
                    "(supercargo/ports_layout.json). ПКМ по порту - удалить. "
                    "Нажми «Готово», чтобы вернуться.")
        elif self.mode == "collect":
            self.status.config(text=f"Сбор данных: {done} / {len(names)} портов")
            if not names:
                hint = "Открой карту в игре и задержи курсор на порту, пока не появится подсказка. Каждый новый порт добавится на карту."
            else:
                hint = (f"Открой карту в игре и по очереди задерживай курсор на серых портах. Когда все станут зелёными, "
                        f"маршрут построится сам. Новые порты добавляются автоматически. "
                        f"ПКМ по порту - забыть его.")
        else:
            oldest = min((p["updated"] for p in self.store.ports.values()), default=time.time())
            age = max(0.0, time.time() - oldest) / 60
            self.status.config(text=f"Маршруты по {len(names)} портам")
            hint = (f"Самым старым ценам {age:.0f} мин. Наведи курсор на порт на карте - его цены обновятся, "
                    f"«Обновить данные» - пересканировать все порты. Клик по маршруту в списке - показать только его.")
        self.hint.config(text=hint)

    # ---------- geometry ----------
    def view_xy(self, name: str):
        """Port position in background-view pixels."""
        xy = self.store.position(name)
        return self.cell_to_view(xy) if xy else None

    def canvas_to_cells(self, x, y):
        s, ox, oy = self._transform()
        bx0, by0, _, _ = mapgeo.bounds()
        return (x - ox) / s / VIEW_PX_PER_CELL + bx0, (y - oy) / s / VIEW_PX_PER_CELL + by0

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
    def port_tag(self, name: str) -> str:
        """Canvas tag for all items of one port (port names contain spaces, which tags can't)."""
        tag = self.tag_of.get(name)
        if tag is None:
            tag = self.tag_of[name] = f"p{len(self.tag_of)}"
            self.name_of[tag] = name
        return tag

    def redraw(self):
        c = self.canvas
        c.delete("all")
        s, ox, oy = self._transform()
        size = (max(1, int(self.view.width * s)), max(1, int(self.view.height * s)))
        if self.view_tk_key != size:
            self.view_tk = ImageTk.PhotoImage(self.view.resize(size, Image.LANCZOS))
            self.view_tk_key = size
        c.create_image(ox, oy, image=self.view_tk, anchor="nw")

        if self.mode in ("collect", "edit"):
            for name in self.store.all_names():
                xy = self.view_xy(name)
                if xy:
                    if self.mode == "edit":
                        self.draw_edit_marker(name, self.to_canvas(xy))
                    else:
                        self.draw_scan_marker(name, self.to_canvas(xy), self.store.is_scanned(name))
        else:
            self.draw_routes()

        self.update_status()

    def draw_scan_marker(self, name, xy, scanned):
        c, (x, y), r, tags = self.canvas, xy, MARKER_R, ("port", self.port_tag(name))
        fill, outline = ("#2a9d3f", "#dfffe6") if scanned else ("#6b6b6b", "#d0d0d0")
        c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=2, tags=tags)
        c.create_text(x, y, text="✓" if scanned else "?", fill="white", font=("Segoe UI", 11, "bold"), tags=tags)
        self._label(x, y + r + 9, short(name), "#111", "#f0e6c8", tags=tags)

    def draw_edit_marker(self, name, xy):
        c, (x, y), r, tags = self.canvas, xy, 7, ("port", self.port_tag(name))
        c.create_line(x - r - 5, y, x + r + 5, y, fill="#00e5ff", width=2, tags=tags)
        c.create_line(x, y - r - 5, x, y + r + 5, fill="#00e5ff", width=2, tags=tags)
        c.create_oval(x - r, y - r, x + r, y + r, outline="#00e5ff", width=2, tags=tags)
        self._label(x, y + r + 14, short(name), "#111", "#bff6ff", tags=tags)

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
            xy = self.view_xy(name)
            if not xy:
                continue
            x, y = self.to_canvas(xy)
            r = MARKER_R - 2
            tags = ("port", self.port_tag(name))
            self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, width=3, tags=tags)
            self._label(x, y + r + 10, short(name), "#111", "#f0e6c8", tags=tags)

    def _label(self, x, y, text, fg, bg, font=("Segoe UI", 9, "bold"), tags=()):
        t = self.canvas.create_text(x, y, text=text, fill=fg, font=font, tags=tags)
        x0, y0, x1, y1 = self.canvas.bbox(t)
        rect = self.canvas.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1, fill=bg, outline="", tags=tags)
        self.canvas.tag_lower(rect, t)

    def draw_route(self, route: router.Route, color, bold=False):
        sp, dp = self.view_xy(route.src), self.view_xy(route.dst)
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

    def toggle_admin(self):
        self.admin = not self.admin
        if self.admin:
            self.admin_frame.pack(side=tk.LEFT)
        else:
            if self.mode == "edit":
                self.toggle_edit()
            self.admin_frame.pack_forget()

    def toggle_edit(self):
        if self.mode == "edit":
            self.edit_btn.config(text="Двигать порты")
            if self.mode_before_edit == "routes":
                self.build_routes()  # distances may have changed
                return
            self.mode = self.mode_before_edit
        else:
            self.mode_before_edit = self.mode
            self.mode = "edit"
            self.edit_btn.config(text="Готово")
        self.refresh_list()
        self.redraw()

    def _port_at(self, x, y):
        r = 9  # markers are hollow: search a small box so clicking inside the ring still hits it
        for it in reversed(self.canvas.find_overlapping(x - r, y - r, x + r, y + r)):
            for t in self.canvas.gettags(it):
                if t in self.name_of:
                    return self.name_of[t]
        return None

    def on_drag_start(self, e):
        if self.mode != "edit":
            return
        name = self._port_at(e.x, e.y)
        if name:
            px, py = self.to_canvas(self.view_xy(name))
            self.drag = {"name": name, "dx": px - e.x, "dy": py - e.y}

    def on_drag_move(self, e):
        if not self.drag:
            return
        name = self.drag["name"]
        # Move this port's items live; the full redraw happens on release.
        px, py = self.drag.get("pos") or self.to_canvas(self.view_xy(name))
        nx, ny = e.x + self.drag["dx"], e.y + self.drag["dy"]
        self.canvas.move(self.port_tag(name), nx - px, ny - py)
        self.drag["pos"] = (nx, ny)

    def on_drag_end(self, e):
        if not self.drag:
            return
        d, self.drag = self.drag, None
        if "pos" in d:
            self.store.set_position(d["name"], self.canvas_to_cells(*d["pos"]))
        self.redraw()

    def refresh(self):
        if self.mode == "edit":
            self.edit_btn.config(text="Двигать порты")
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
        name = self._port_at(e.x, e.y)
        if name and messagebox.askyesno("Забыть порт", f"Убрать «{name}» с карты и из базы?"):
            self.store.remove(name)
            self.build_routes() if self.mode == "routes" else self.redraw()

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
                if kind == "scan":
                    _, info, map_xy = ev
                    name = self.store.update(info, map_xy=map_xy)
                    print(f"scanned: {name} ({len(info.goods)} goods)")
                    if self.mode == "edit":
                        self.redraw()
                    elif self.mode == "routes":
                        self.build_routes()  # prices changed: keep the route view up to date
                    elif self.all_scanned():
                        self.build_routes()
                        self.bring_to_front()
                    else:
                        self.redraw()
        except queue.Empty:
            pass
        self.after(50, self.poll)


def run():
    App().mainloop()
