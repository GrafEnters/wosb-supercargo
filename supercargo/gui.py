"""The supercargo's logbook: a top-down map with the ports, and the manifest of the best runs.

Ports are scanned automatically while you hover them on the in-game map.

Modes:
  collect - after "Новая опись": every known port is faded until rescanned, then gets a check mark;
  routes  - best runs drawn as course lines on the map, the manifest lists what to buy;
  edit    - admin only: drag port markers to fix their positions (saved to ports_layout.json);
  shallows- admin only: draw the shallow-water zones (saved to shallows.json).
"""
import json
import math
import queue
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import numpy as np
from PIL import Image, ImageTk

from . import mapgeo, navigation, router
from .autoscan import AutoScanner
from .store import Store
from .theme import (AMBER, BRASS, BRASS_DARK, BRASS_LIGHT, F_HEAD, F_MAP, F_MAP_SMALL, F_NUM, F_NUM_SMALL,
                    F_SMALL, F_SMALL_ITALIC, F_SUBTITLE, F_SYMBOL, F_TITLE, F_UI, F_UI_BOLD, INK, INK_FAINT,
                    INK_SOFT, PAPER, PAPER_DIM, PAPER_HALO, PAPER_LINE, ROUTE_COLORS, SEA, SEA_LIGHT, WAX, WOOD,
                    WOOD_LIGHT, Button, Check, Chip, Divider, Field, ThinScrollbar, mix, wood_texture)

HERE = Path(__file__).resolve().parent
BACKGROUND = HERE / "background_map.png"  # clean top-down map, VIEW_PX_PER_CELL px per cell
ICON = HERE / "icon.ico"
SETTINGS = HERE.parent / "data" / "settings.json"

SORT_LABELS = {"distance": "на клетку пути", "trip": "за рейс"}
LEG_LABELS = {"1": "1 переход", "2": "2 перехода"}
MARKER_R = 12
VIEW_PX_PER_CELL = 100
MAP_MARGIN = 26  # wood showing around the map
SIDEBAR_W = 470


def short(port: str) -> str:
    return port.replace("Бухта ", "").replace("Порт ", "")


def fmt_units(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")  # no-break space: "40 000" never splits across lines


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _midpoint(pts):
    """Point halfway along a polyline, so a label sits in the middle of the sailed course."""
    total = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    walked = 0.0
    for a, b in zip(pts, pts[1:]):
        step = math.dist(a, b)
        if walked + step >= total / 2:
            t = (total / 2 - walked) / (step or 1)
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        walked += step
    return pts[len(pts) // 2]


def age_text(seconds: float) -> str:
    m = max(0, int(seconds // 60))
    if m < 1:
        return "только что"
    if m < 60:
        return f"{m} мин назад"
    h = m // 60
    return f"{h} {plural(h, 'час', 'часа', 'часов')} назад"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Суперкарго · World of Sea Battle")
        self.geometry("1540x920")
        self.minsize(1100, 700)
        self.configure(bg=WOOD)
        try:
            self.iconbitmap(str(ICON))
        except tk.TclError:
            pass
        self.store = Store()
        self.view = Image.open(BACKGROUND).convert("RGB")
        self.view_tk = None
        self.view_tk_key = None
        self.wood_tk = None
        self.wood_key = None
        self.routes: list[router.Route] = []
        self.selected: int | None = None
        self.hover_route: int | None = None
        self.reveal: int | None = None  # how many course lines are drawn during the reveal animation
        self.tip_port: str | None = None
        self.sort = tk.StringVar(value="distance")
        settings = self.load_settings()
        self.hold = tk.IntVar(value=settings.get("hold", router.HOLD))
        self.hold_overload = tk.IntVar(value=settings.get("hold_overload", router.HOLD_OVERLOAD))
        self.rules = navigation.load_rules()
        self.zones = self.rules.zones
        self.peace = tk.BooleanVar(value=settings.get("peace", False))
        self.ship_rank = tk.StringVar(value=str(settings.get("ship_rank", 4)))
        self.ship_xy = tuple(settings["ship_xy"]) if settings.get("ship_xy") else None
        self.zone_rank = tk.StringVar(value=str(settings.get("zone_rank", 6)))
        self.legs = tk.StringVar(value=str(settings.get("legs", 2)))
        self.placing_ship = False
        self.draft: list[tuple[float, float]] = []  # zone being drawn
        self.cursor_cell: tuple[float, float] | None = None
        self.nav: navigation.Navigator | None = None
        self._nav_sig = None
        self.events: queue.Queue = queue.Queue()
        self.mode = "collect"
        self.mode_before_edit = "collect"

        self._build_ui()
        self.rebuild_nav()
        if self.all_scanned():
            self.build_routes(animate=False)
        else:
            self.refresh_list()
            self.redraw()
        AutoScanner(self.events, self.store.known_goods,
                    lambda name: self.store.position(self.store.resolve_name(name)) is None).start()
        self.after(50, self.poll)

    # ---------- UI ----------
    def _build_ui(self):
        # --- the chart on the table ---
        self.canvas = tk.Canvas(self, bg=WOOD, highlightthickness=0, bd=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<ButtonPress-1>", self.on_drag_start)
        self.canvas.bind("<B1-Motion>", self.on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_drag_end)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", lambda e: self.show_tip(None))
        self.drag: dict | None = None
        self.tag_of: dict[str, str] = {}
        self.name_of: dict[str, str] = {}

        # --- the logbook ---
        side = tk.Frame(self, bg=WOOD, width=SIDEBAR_W)
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)

        head = tk.Frame(side, bg=WOOD)
        head.pack(fill=tk.X, padx=18, pady=(16, 8))
        title = tk.Frame(head, bg=WOOD)
        title.pack(anchor="w")
        tk.Label(title, text="⚓", bg=WOOD, fg=BRASS, font=("Segoe UI Symbol", 17)).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(title, text="СУПЕРКАРГО", bg=WOOD, fg=BRASS_LIGHT, font=F_TITLE).pack(side=tk.LEFT)
        tk.Label(head, text="судовой журнал торговца", bg=WOOD, fg=INK_FAINT, font=F_SUBTITLE).pack(anchor="w", padx=(34, 0))

        page = tk.Frame(side, bg=PAPER, highlightthickness=1, highlightbackground=BRASS_DARK)
        page.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        self.page = page

        # status block
        self.heading = tk.Label(page, bg=PAPER, fg=INK, font=F_HEAD, anchor="w")
        self.heading.pack(fill=tk.X, padx=16, pady=(14, 0))
        self.progress = tk.Canvas(page, height=8, bg=PAPER, highlightthickness=0, bd=0)
        self.progress_state = (0, 0)
        self.progress.bind("<Configure>", lambda e: self._draw_progress())
        self.sub = tk.Label(page, bg=PAPER, fg=INK_SOFT, font=F_SMALL, anchor="w")
        self.sub.pack(fill=tk.X, padx=16, pady=(2, 0))
        self.hint = tk.Label(page, bg=PAPER, fg=INK_SOFT, font=F_SMALL_ITALIC, justify=tk.LEFT, anchor="w",
                             wraplength=SIDEBAR_W - 70)
        self.hint.pack(fill=tk.X, padx=16, pady=(8, 0))

        btns = tk.Frame(page, bg=PAPER)
        btns.pack(fill=tk.X, padx=16, pady=(12, 4))
        self.refresh_btn = Button(btns, "Новая опись", self.refresh, primary=True)
        self.refresh_btn.pack(side=tk.LEFT)
        Button(btns, "Проложить курс", self.build_routes).pack(side=tk.LEFT, padx=(8, 0))

        Divider(page).pack(fill=tk.X, padx=16, pady=(6, 2))

        # ship & sorting
        ship = tk.Frame(page, bg=PAPER)
        ship.pack(fill=tk.X, padx=16)
        Field(ship, "Трюм", self.hold, self.on_hold_change).pack(side=tk.LEFT)
        Field(ship, "с перегрузом", self.hold_overload, self.on_hold_change).pack(side=tk.LEFT, padx=(14, 0))
        rank_row = tk.Frame(page, bg=PAPER)
        rank_row.pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(rank_row, text="Ранг", bg=PAPER, fg=INK_SOFT, font=F_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        for r in range(1, navigation.MAX_RANK + 1):
            Chip(rank_row, navigation.roman(r), str(r), self.ship_rank, self.on_rank_change).pack(side=tk.LEFT, padx=(0, 3))

        pos_row = tk.Frame(page, bg=PAPER)
        pos_row.pack(fill=tk.X, padx=16, pady=(6, 0))
        self.ship_btn = Button(pos_row, "Отметить корабль", self.arm_ship)
        self.ship_btn.pack(side=tk.LEFT)
        self.ship_label = tk.Label(pos_row, bg=PAPER, fg=INK_SOFT, font=F_SMALL, anchor="w")
        self.ship_label.pack(side=tk.LEFT, padx=(8, 0))
        Check(page, "мирный флаг", self.peace, self.on_peace_change).pack(fill=tk.X, padx=16, pady=(6, 0))

        sort_row = tk.Frame(page, bg=PAPER)
        sort_row.pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(sort_row, text="Выгода", bg=PAPER, fg=INK_SOFT, font=F_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        for k, label in SORT_LABELS.items():
            Chip(sort_row, label, k, self.sort, self.on_plan_change).pack(side=tk.LEFT, padx=(0, 4))
        legs_row = tk.Frame(page, bg=PAPER)
        legs_row.pack(fill=tk.X, padx=16, pady=(6, 0))
        tk.Label(legs_row, text="Рейс", bg=PAPER, fg=INK_SOFT, font=F_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        for k, label in LEG_LABELS.items():
            Chip(legs_row, label, k, self.legs, self.on_plan_change).pack(side=tk.LEFT, padx=(0, 4))

        Divider(page).pack(fill=tk.X, padx=16, pady=(8, 0))
        tk.Label(page, text="МАНИФЕСТ", bg=PAPER, fg=INK_FAINT, font=F_SMALL, anchor="w").pack(fill=tk.X, padx=16)

        # manifest: a read-only Text styled as ledger pages
        body = tk.Frame(page, bg=PAPER)
        body.pack(fill=tk.BOTH, expand=True, padx=(16, 8), pady=(2, 10))
        self.text = tk.Text(body, bg=PAPER, fg=INK, font=F_UI, wrap=tk.WORD, bd=0, highlightthickness=0,
                            padx=4, pady=2, cursor="arrow", insertwidth=0, exportselection=False,
                            selectbackground=PAPER, selectforeground=INK, spacing3=1)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scroll = ThinScrollbar(body, self.text.yview)
        self.scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        self.text.config(yscrollcommand=self.scroll.set)
        t = self.text
        t.tag_config("head", font=F_UI_BOLD, spacing1=9)
        t.tag_config("sum", font=F_SMALL, foreground=INK_SOFT, lmargin1=16, lmargin2=16)
        t.tag_config("leg", font=F_UI_BOLD, foreground=INK_SOFT, lmargin1=8, lmargin2=8, spacing1=4)
        t.tag_config("num", font=F_NUM, foreground=INK, lmargin1=16, lmargin2=16)
        t.tag_config("dim", font=F_NUM_SMALL, foreground=INK_FAINT, lmargin1=16, lmargin2=16)
        t.tag_config("warn", font=F_SMALL_ITALIC, foreground=AMBER, lmargin1=16, lmargin2=16)
        t.tag_config("note", font=F_SMALL_ITALIC, foreground=INK_FAINT, spacing1=6)
        t.tag_config("hover", background=PAPER_DIM)
        t.tag_config("sel", background=mix(PAPER, BRASS, 0.28))
        t.tag_raise("sel", "hover")
        t.config(state=tk.DISABLED)

        # admin tools behind a dim gear in the corner
        foot = tk.Frame(side, bg=WOOD)
        # before=page, otherwise the page (fill=BOTH, expand) eats the whole cavity and the footer gets no height
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 8), before=page)
        # discreet, but it has to be findable: muted brass on the wood, with a comfortable hit area
        dim = mix(WOOD, BRASS, 0.5)
        gear = tk.Label(foot, text="⚙", bg=WOOD, fg=dim, font=("Segoe UI Symbol", 13), cursor="hand2",
                        padx=6, pady=2)
        gear.pack(side=tk.RIGHT)
        gear.bind("<Button-1>", lambda e: self.toggle_admin())
        gear.bind("<Enter>", lambda e: gear.config(fg=BRASS_LIGHT))
        gear.bind("<Leave>", lambda e: gear.config(fg=dim))
        self.admin_frame = tk.Frame(foot, bg=WOOD)
        admin_btns = tk.Frame(self.admin_frame, bg=WOOD)
        admin_btns.pack(anchor="w")
        self.edit_btn = Button(admin_btns, "Поправить карту", self.toggle_edit)
        self.edit_btn.pack(side=tk.LEFT)
        self.zone_btn = Button(admin_btns, "Мелководье", self.toggle_zones)
        self.zone_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.zone_panel = tk.Frame(self.admin_frame, bg=WOOD)
        chips = tk.Frame(self.zone_panel, bg=WOOD)
        chips.pack(anchor="w")
        tk.Label(chips, text="Ранг зоны", bg=WOOD, fg=INK_FAINT, font=F_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        for r in range(2, navigation.MAX_RANK + 1):
            Chip(chips, navigation.roman(r), str(r), self.zone_rank, lambda: None).pack(side=tk.LEFT, padx=(0, 3))
        Chip(chips, "⚑ мирный", navigation.PEACE, self.zone_rank, lambda: None).pack(side=tk.LEFT, padx=(6, 0))
        Button(self.zone_panel, "Ранги по портам", self.ranks_from_ports).pack(anchor="w", pady=(5, 0))
        self.admin = False

        self.bind("<Return>", lambda e: self.close_zone())
        self.bind("<Escape>", lambda e: self.cancel_draft())
        self.bind("<BackSpace>", lambda e: self.undo_point())
        self.canvas.bind("<Double-Button-1>", lambda e: self.close_zone())

    @property
    def rank(self) -> int:
        try:
            return int(self.ship_rank.get())
        except (tk.TclError, ValueError):
            return navigation.MAX_RANK

    def rebuild_nav(self, force=False):
        """Rebuild the visibility graph when the ports, the zones or the ship's rank change."""
        pos = {n: tuple(self.store.position(n)) for n in self.store.all_names() if self.store.position(n)}
        ranks = {n: navigation.min_rank(p.get("shallow")) for n, p in self.store.ports.items()}
        sig = (tuple(sorted(pos.items())), tuple(sorted((k, v) for k, v in ranks.items() if v)), self.rank,
               tuple((z.rank, z.kind, len(z.points)) for z in self.zones),
               bool(self.peace.get()), tuple(sorted(self.rules.peace_ports)))
        if not force and self.nav is not None and sig == self._nav_sig:
            self.nav.set_ship(self.ship_xy)
            return
        self._nav_sig = sig
        self.nav = navigation.Navigator(self.zones, self.rank, pos, ranks, peace=bool(self.peace.get()),
                                        peace_ports=self.rules.peace_ports)
        self.nav.set_ship(self.ship_xy)

    def all_scanned(self) -> bool:
        names = self.store.all_names()
        return len(names) >= 2 and all(self.store.is_scanned(n) for n in names)

    def update_status(self):
        names = self.store.all_names()
        done = sum(self.store.is_scanned(n) for n in names)
        self.progress.pack_forget()
        if self.mode == "shallows":
            self.heading.config(text="Мелководье")
            peace_zones = sum(1 for z in self.zones if z.kind == navigation.PEACE)
            blocked = sum(1 for z in self.zones if z.kind == navigation.SHALLOW and z.rank > self.rank)
            self.sub.config(text=f"{len(self.zones)} {plural(len(self.zones), 'зона', 'зоны', 'зон')} · "
                                 f"{blocked} не по зубам рангу {navigation.roman(self.rank)} · "
                                 f"мирных: {peace_zones}, портов под мирным флагом: {len(self.rules.peace_ports)}")
            hint = ("Клик ставит вершину, клик по первой вершине или Enter замыкает зону. Backspace убирает "
                    "точку, Esc бросает начатое, правый клик по зоне стирает её. Ранг внизу: VI значит "
                    "«пройдут ранги VI–VII», «⚑ мирный» — зона, закрытая мирному флагу. Shift+клик ставит "
                    "ранг готовой зоне, Ctrl+клик по порту закрывает его для мирного флага, "
                    "«Ранги по портам» проставит ранги сам.")
        elif self.mode == "edit":
            self.heading.config(text="Поправка карты")
            self.sub.config(text=f"{len(names)} {plural(len(names), 'порт', 'порта', 'портов')} в журнале")
            hint = ("Перетащи метки на значки портов — позиции запишутся сразу. "
                    "Правый клик по порту — вычеркнуть его из журнала. «Готово» — вернуться.")
        elif self.mode == "collect":
            self.heading.config(text="Опись цен")
            self.progress.pack(fill=tk.X, padx=16, pady=(8, 0), after=self.heading)
            self.progress_state = (done, len(names))
            self._draw_progress()
            self.sub.config(text=f"{done} из {len(names)} портов записано" if names else "журнал пуст")
            if not names:
                hint = ("Разверни карту в игре и задержи курсор на порту, пока не всплывёт подсказка. "
                        "Каждый новый порт ляжет в журнал сам.")
            else:
                hint = ("Обойди курсором серые порты на карте в игре. Когда все получат отметку, "
                        "курс проложится сам. Правый клик по порту здесь — вычеркнуть его.")
        else:
            self.heading.config(text="Курсы проложены")
            oldest = min((p["updated"] for p in self.store.ports.values()), default=time.time())
            self.sub.config(text=f"{len(names)} портов · самая старая запись {age_text(time.time() - oldest)}")
            hint = ("Наведи курсор на порт в игре — его цены обновятся. Порт на этой карте под курсором "
                    "покажет свою опись. Клик по курсу в манифесте оставит на карте только его.")
        if self.placing_ship:
            hint = "Кликни по карте там, где сейчас стоит корабль."
        self.hint.config(text=hint)
        if self.ship_xy:
            cell = mapgeo.cell_name(*self.ship_xy)
            unreachable = sum(1 for n in self.store.all_names() if self.nav and not self.nav.reachable(n))
            self.ship_label.config(text=f"⚓ {cell}" + (f" · {unreachable} портов закрыто" if unreachable else ""))
        else:
            self.ship_label.config(text="корабль не отмечен")

    def _draw_progress(self):
        c = self.progress
        c.delete("all")
        w, h = c.winfo_width(), 8
        done, total = self.progress_state
        if w < 10:
            return
        c.create_rectangle(0, 2, w, h - 2, fill=PAPER_LINE, outline="")
        if total:
            fw = w * done / total
            c.create_rectangle(0, 1, fw, h - 1, fill=BRASS if done < total else SEA, outline="")
            # rope twist marks
            for x in range(6, int(fw) - 2, 9):
                c.create_line(x, 1, x - 3, h - 1, fill=BRASS_DARK if done < total else SEA_LIGHT)

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
        cw = max(self.canvas.winfo_width() - 2 * MAP_MARGIN, 1)
        ch = max(self.canvas.winfo_height() - 2 * MAP_MARGIN, 1)
        bx0, by0, bx1, by1 = mapgeo.bounds()
        iw, ih = (bx1 - bx0) * VIEW_PX_PER_CELL, (by1 - by0) * VIEW_PX_PER_CELL
        s = min(cw / iw, ch / ih)
        return s, MAP_MARGIN + (cw - iw * s) / 2, MAP_MARGIN + (ch - ih * s) / 2

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
        self.tip_port = None
        cw, ch = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        if self.wood_key != (cw, ch):
            self.wood_tk = ImageTk.PhotoImage(wood_texture(cw, ch))
            self.wood_key = (cw, ch)
        c.create_image(0, 0, image=self.wood_tk, anchor="nw")

        s, ox, oy = self._transform()
        size = (max(1, int(self.view.width * s)), max(1, int(self.view.height * s)))
        if self.view_tk_key != size:
            self.view_tk = ImageTk.PhotoImage(self.view.resize(size, Image.LANCZOS))
            self.view_tk_key = size
        # the chart's shadow and brass-pinned edge
        c.create_rectangle(ox + 6, oy + 8, ox + size[0] + 6, oy + size[1] + 8, fill="#140d08", outline="")
        c.create_image(ox, oy, image=self.view_tk, anchor="nw")
        c.create_rectangle(ox - 1, oy - 1, ox + size[0], oy + size[1], outline=BRASS_DARK)
        for px, py in ((ox + 10, oy + 10), (ox + size[0] - 10, oy + 10), (ox + 10, oy + size[1] - 10),
                       (ox + size[0] - 10, oy + size[1] - 10)):
            c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=BRASS, outline=BRASS_DARK)

        self.draw_zones()
        if self.mode in ("edit", "shallows"):
            for name in self.store.all_names():
                xy = self.view_xy(name)
                if xy:
                    self.draw_edit_marker(name, self.to_canvas(xy))
            if self.mode == "shallows":
                self.draw_draft()
        elif self.mode == "collect":
            for name in self.store.all_names():
                xy = self.view_xy(name)
                if xy:
                    self.draw_scan_marker(name, self.to_canvas(xy), self.store.is_scanned(name))
        else:
            self.draw_routes()
        self.draw_ship()
        self.update_status()

    def _label(self, x, y, text, fg=INK, bg=PAPER, font=F_MAP, tags=(), stripe=None, anchor="n", pad=None):
        t = self.canvas.create_text(x, y, text=text, fill=fg, font=font, tags=tags, anchor=anchor)
        x0, y0, x1, y1 = self.canvas.bbox(t)
        pad = pad if pad is not None else (4 if stripe else 3)
        rect = self.canvas.create_rectangle(x0 - pad, y0 - 1, x1 + pad, y1 + 1, fill=bg, outline=PAPER_LINE, tags=tags)
        self.canvas.tag_lower(rect, t)
        if stripe:
            self.canvas.create_rectangle(x0 - pad, y0 - 1, x0 - pad + 3, y1 + 1, fill=stripe, outline="", tags=tags)

    def draw_zones(self):
        for z in self.zones:
            pts = [c for p in z.points for c in self.to_canvas(self.cell_to_view(p))]
            if len(pts) < 6:
                continue
            blocked = z.blocks(self.rank, bool(self.peace.get()))
            peace_zone = z.kind == navigation.PEACE
            color = WAX if blocked else (INK_FAINT if peace_zone else SEA)
            self.canvas.create_polygon(pts, fill=color, stipple="gray25" if blocked else "gray12",
                                       outline=color, width=1, dash=(5, 4))
            cx, cy = self.to_canvas(self.cell_to_view(z.centroid()))
            text = z.label()
            if self.mode == "shallows" and z.kind == navigation.SHALLOW:
                hint = navigation.suggest_rank(z, self.port_ranks_by_xy())
                if hint and hint != z.rank:
                    text += f"  порты: {navigation.roman(hint)}"
            self._label(cx, cy, text, fg=color, bg=PAPER_DIM, font=F_MAP_SMALL, anchor="center")

    def draw_draft(self):
        """The zone currently being drawn, with a rubber band to the cursor."""
        c = self.canvas
        c.delete("draft")
        if not self.draft:
            return
        pts = [self.to_canvas(self.cell_to_view(p)) for p in self.draft]
        band = pts + ([self.to_canvas(self.cell_to_view(self.cursor_cell))] if self.cursor_cell else [])
        if len(band) > 2:
            c.create_polygon([v for p in band for v in p], fill=BRASS, stipple="gray25", outline="", tags="draft")
        if len(band) > 1:
            c.create_line([v for p in band for v in p], fill=BRASS_DARK, width=2, dash=(6, 4), tags="draft")
        for i, (x, y) in enumerate(pts):
            r = 6 if i == 0 else 4
            c.create_oval(x - r, y - r, x + r, y + r, fill=PAPER if i == 0 else BRASS,
                          outline=BRASS_DARK, width=2, tags="draft")

    def draw_ship(self):
        if not self.ship_xy:
            return
        x, y = self.to_canvas(self.cell_to_view(self.ship_xy))
        c, tags = self.canvas, ("ship",)
        c.create_polygon(x - 12, y + 3, x + 12, y + 3, x + 7, y + 11, x - 7, y + 11,
                         fill=INK, outline=PAPER_HALO, width=2, tags=tags)
        c.create_line(x, y - 13, x, y + 3, fill=INK, width=2, tags=tags)
        c.create_polygon(x + 1, y - 13, x + 11, y - 7, x + 1, y - 1, fill=BRASS, outline=INK, tags=tags)
        self._label(x, y + 14, f"корабль · {navigation.roman(self.rank)}", bg=BRASS_LIGHT, tags=tags)

    def draw_scan_marker(self, name, xy, scanned):
        c, (x, y), r, tags = self.canvas, xy, MARKER_R, ("port", self.port_tag(name))
        if self.nav and not self.nav.reachable(name):  # too shallow for this ship
            c.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4, outline=WAX, width=1, dash=(3, 3), tags=tags)
        if name in self.rules.peace_ports:  # no entry under the peace flag
            self.draw_flag(x + r - 2, y - r, tags)
        if scanned:
            c.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill=BRASS_DARK, outline="", tags=tags)
            c.create_oval(x - r, y - r, x + r, y + r, fill=SEA, outline=BRASS_LIGHT, width=2, tags=tags)
            c.create_line(x - 5, y + 0.5, x - 1.5, y + 4, x + 5.5, y - 4.5, fill=PAPER, width=2.5,
                          capstyle=tk.ROUND, joinstyle=tk.ROUND, tags=tags)
            self._label(x, y + r + 5, short(name), tags=tags)
        else:
            c.create_oval(x - r, y - r, x + r, y + r, fill=PAPER, stipple="gray50", outline=INK_SOFT, width=2, tags=tags)
            c.create_text(x, y, text="?", fill=INK_SOFT, font=(F_MAP[0], 11, "bold"), tags=tags)
            self._label(x, y + r + 5, short(name), fg=INK_SOFT, bg=PAPER_DIM, font=F_MAP_SMALL, tags=tags)

    def draw_flag(self, x, y, tags=()):
        c = self.canvas
        c.create_line(x, y - 9, x, y + 5, fill=INK, width=2, tags=tags)
        c.create_polygon(x + 1, y - 9, x + 11, y - 5.5, x + 1, y - 2, fill=PAPER, outline=INK, tags=tags)

    def draw_edit_marker(self, name, xy):
        c, (x, y), r, tags = self.canvas, xy, 8, ("port", self.port_tag(name))
        if name in self.rules.peace_ports:
            self.draw_flag(x + r, y - r + 2, tags)
        c.create_line(x - r - 6, y, x + r + 6, y, fill=BRASS_LIGHT, width=2, tags=tags)
        c.create_line(x, y - r - 6, x, y + r + 6, fill=BRASS_LIGHT, width=2, tags=tags)
        c.create_oval(x - r, y - r, x + r, y + r, outline=WAX, width=2, tags=tags)
        self._label(x, y + r + 9, short(name), fg=WAX, tags=tags)

    def draw_routes(self):
        shown = list(enumerate(self.routes[:len(ROUTE_COLORS)]))
        if self.selected is not None:
            shown = [(self.selected, self.routes[self.selected])]
        elif self.reveal is not None:
            shown = shown[:self.reveal]
        # hovered course goes on top
        order = sorted(shown, key=lambda ir: (ir[0] == self.hover_route, -ir[0]))
        endpoints: dict[str, str] = {}
        for i, r in order:
            color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
            bold = self.selected is not None or i == self.hover_route
            self.draw_route(r, color, bold)
            endpoints.setdefault(r.src, color)
            endpoints.setdefault(r.dst, color)
        # every port stays hoverable (invisible hit areas), courses' ends get a marker
        for name in self.store.all_names():
            xy = self.view_xy(name)
            if xy and name not in endpoints:
                x, y = self.to_canvas(xy)
                self.canvas.create_oval(x - 10, y - 10, x + 10, y + 10, fill="", outline="",
                                        tags=("port", self.port_tag(name)))
        order = {}
        if self.selected is not None and len(self.routes[self.selected].legs) > 1:
            order = {name: i for i, name in enumerate(self.routes[self.selected].ports, 1)}
        for name, color in endpoints.items():
            xy = self.view_xy(name)
            if not xy:
                continue
            x, y = self.to_canvas(xy)
            r = MARKER_R - 3
            tags = ("port", self.port_tag(name))
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=PAPER, outline=color, width=3, tags=tags)
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline="", tags=tags)
            if name in self.rules.peace_ports:
                self.draw_flag(x + r - 1, y - r, tags)
            if name in order:  # the ports of one chain, in sailing order
                bx, by = x + r + 1, y - r - 1
                self.canvas.create_oval(bx - 7, by - 7, bx + 7, by + 7, fill=color, outline=PAPER_HALO, width=1, tags=tags)
                self.canvas.create_text(bx, by, text=str(order[name]), fill=PAPER, font=(F_MAP[0], 8, "bold"), tags=tags)
            self._label(x, y + r + 6, short(name), tags=tags)

    def course_points(self, src: str, dst: str) -> list[tuple[float, float]]:
        """Canvas points of the sailed course: around the shallows when a navigator knows them."""
        cells = self.nav.path(src, dst) if self.nav else []
        if len(cells) < 2:
            a, b = self.view_xy(src), self.view_xy(dst)
            return [self.to_canvas(a), self.to_canvas(b)] if a and b else []
        return [self.to_canvas(self.cell_to_view(p)) for p in cells]

    def draw_route(self, route: router.Route, color, bold=False):
        for n, leg in enumerate(route.legs, 1):
            self.draw_leg(leg, color, bold, n if len(route.legs) > 1 else 0)
        if bold and self.ship_xy and self.nav:
            self.draw_approach(route.src, color)

    def draw_leg(self, leg, color, bold=False, number=0):
        pts = self.course_points(leg.src, leg.dst)
        if len(pts) < 2:
            return
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        length = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        nx, ny = -uy * 5, ux * 5  # shift sideways so A->B and B->A don't overlap
        pad = MARKER_R + 2
        pts = [(x + nx, y + ny) for x, y in pts]
        pts[0] = (pts[0][0] + ux * pad, pts[0][1] + uy * pad)
        pts[-1] = (pts[-1][0] - ux * pad, pts[-1][1] - uy * pad)
        w, halo = (4 if bold else 3), 2
        flat = [v for p in pts for v in p]
        # the halo is the same course, thicker, its arrowhead a little past the tip
        halo_pts = list(flat)
        halo_pts[0] -= ux * halo
        halo_pts[1] -= uy * halo
        halo_pts[-2] += ux * halo
        halo_pts[-1] += uy * halo
        self.canvas.create_line(halo_pts, fill=PAPER_HALO, width=w + 2 * halo, arrow=tk.LAST,
                                arrowshape=(14 + halo, 18 + halo, 6), capstyle=tk.ROUND, joinstyle=tk.ROUND)
        self.canvas.create_line(flat, fill=color, width=w, arrow=tk.LAST, arrowshape=(14, 18, 6),
                                capstyle=tk.ROUND, joinstyle=tk.ROUND, dash=() if bold else (14, 6))
        # A small tag beside the course, not across it: short hops would disappear under a big label.
        on_screen = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        if on_screen < 55 and not bold:
            return
        mx, my = _midpoint(pts)
        text = f"+{router.money(leg.plan.profit)}"
        if number:
            text = f"{number}. {text}"
        self._label(mx + nx * 2.2, my + ny * 2.2, text, fg=INK, stripe=color, anchor="center",
                    font=F_MAP_SMALL, pad=2)

    def draw_approach(self, src: str, color):
        """Dotted leg from where the ship stands now to the port where the cargo is bought."""
        cells = self.nav.approach_path(src)
        if len(cells) < 2:
            return
        pts = [v for p in cells for v in self.to_canvas(self.cell_to_view(p))]
        self.canvas.create_line(pts, fill=PAPER_HALO, width=6, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        self.canvas.create_line(pts, fill=color, width=2, dash=(2, 5), capstyle=tk.ROUND, joinstyle=tk.ROUND)

    # ---------- manifest ----------
    def refresh_list(self):
        t = self.text
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        for tag in t.tag_names():
            if tag.startswith("blk"):
                t.tag_delete(tag)
        if self.mode in ("edit", "shallows"):
            t.insert(tk.END, "Пока карту правят, манифест закрыт.", "note")
        elif self.mode == "collect" or not self.routes:
            t.insert(tk.END, "Курсы появятся, когда все порты будут в описи. "
                             "Нетерпеливым — «Проложить курс» по тому, что уже записано." if self.mode == "collect"
                     else "Выгодных курсов не нашлось. Загляни в цены ещё раз.", "note")
        else:
            money = router.money
            for i, r in enumerate(self.routes[:30]):
                blk = f"blk{i}"
                t.tag_config(blk, lmargin1=0)
                start = t.index(tk.END + "-1c")
                color = ROUTE_COLORS[i] if i < len(ROUTE_COLORS) else INK_FAINT
                mark = "■ " if i < len(ROUTE_COLORS) else "□ "
                t.insert(tk.END, mark, ("head", blk, f"c{i}"))
                t.tag_config(f"c{i}", foreground=color)
                t.insert(tk.END, " → ".join(short(n) for n in r.ports) + "\n", ("head", blk))
                parts = []
                if r.approach is not None:
                    parts.append(f"подход {r.approach:.1f}")
                if r.distance is not None:
                    parts.append(f"путь {r.distance:.1f} кл.")
                parts = " + ".join(parts) + " · " if parts else ""
                t.insert(tk.END, f"{parts}прибыль +{money(r.profit)} · вложить {money(r.capital)}\n", ("sum", blk))
                for n, leg in enumerate(r.legs, 1):
                    if len(r.legs) > 1:
                        t.insert(tk.END, f"{n}. {short(leg.src)} → {short(leg.dst)}   +{money(leg.plan.profit)}\n",
                                 ("leg", blk))
                    for it in leg.plan.items:
                        batches = f"{it.batches} {plural(it.batches, 'партия', 'партии', 'партий')}"
                        t.insert(tk.END, f"{it.good:<12}{fmt_units(it.units):>8} шт  {batches:<9} +{money(it.profit)}\n",
                                 ("num", blk))
                        t.insert(tk.END, f"{'':<12}купить {it.first_buy:.3g}→{it.last_buy:.3g}   "
                                         f"продать {it.first_sell:.3g}→{it.last_sell:.3g}\n", ("dim", blk))
                if r.overload_better:
                    t.insert(tk.END, f"с перегрузом +{money(r.overload_profit)}, но идти вдвое дольше\n", ("warn", blk))
                t.insert(tk.END, "\n", (blk,))
                t.tag_bind(blk, "<Button-1>", lambda e, i=i: self.select_route(i))
                t.tag_bind(blk, "<Enter>", lambda e, i=i: self.set_hover(i))
                t.tag_bind(blk, "<Leave>", lambda e, i=i: self.set_hover(None))
                if i == self.selected:
                    t.tag_add("sel", start, tk.END + "-1c")
        t.config(state=tk.DISABLED)
        t.yview_moveto(0)

    def set_hover(self, i):
        if i == self.hover_route:
            return
        self.hover_route = i
        t = self.text
        t.tag_remove("hover", "1.0", tk.END)
        if i is not None:
            for a, b in zip(*[iter(t.tag_ranges(f"blk{i}"))] * 2):
                t.tag_add("hover", a, b)
        if self.mode == "routes" and self.selected is None and self.reveal is None:
            self.redraw()

    def select_route(self, i):
        if self.mode != "routes":
            return
        self.selected = None if self.selected == i else i
        self.reveal = None
        t = self.text
        t.tag_remove("sel", "1.0", tk.END)
        if self.selected is not None:
            for a, b in zip(*[iter(t.tag_ranges(f"blk{i}"))] * 2):
                t.tag_add("sel", a, b)
        self.redraw()

    # ---------- port tooltip on the chart ----------
    def on_motion(self, e):
        if self.mode == "shallows":
            self.cursor_cell = self.canvas_to_cells(e.x, e.y)
            if self.draft:
                self.draw_draft()
            return
        if self.drag or self.mode == "edit":
            return
        self.show_tip(self._port_at(e.x, e.y), e.x, e.y)

    def show_tip(self, name, mx=0, my=0):
        if name == self.tip_port:
            return
        self.canvas.delete("tip")
        self.tip_port = name
        if not name:
            return
        p = self.store.ports.get(name)
        c = self.canvas
        rows = [(name, F_UI_BOLD, INK)]
        if p:
            meta = []
            if p.get("tax") is not None:
                meta.append(f"налог {p['tax']:g}%")
            rank = navigation.min_rank(p.get("shallow"))
            if rank:
                meta.append(f"мелководье {navigation.zone_label(rank)}")
            meta.append(age_text(time.time() - p["updated"]))
            rows.append((" · ".join(meta), F_SMALL_ITALIC, INK_FAINT))
            rows.append((f"{'товар':<12}{'купить':>8}{'продать':>9}{'на складе':>11}", F_NUM_SMALL, INK_FAINT))
            for g, v in p["goods"].items():
                stock = f"{v['stock'] / 1000:.0f}k" if v.get("stock") else "—"
                rows.append((f"{g:<12}{v.get('buy') or '—':>8}{v.get('sell') or '—':>9}{stock:>11}", F_NUM_SMALL, INK))
        else:
            rows.append(("цен ещё нет — наведи курсор на порт в игре", F_SMALL_ITALIC, INK_FAINT))
        if name in self.rules.peace_ports:
            rows.insert(1, ("под мирным флагом вход закрыт", F_SMALL_ITALIC, INK_SOFT))
        if self.nav and not self.nav.reachable(name):
            rows.insert(1, (f"кораблю ранга {navigation.roman(self.rank)} сюда не зайти", F_SMALL_ITALIC, WAX))
        x, y = mx + 18, my + 18
        items, cy = [], y + 8
        for text, font, fg in rows:
            items.append(c.create_text(x + 10, cy, text=text, font=font, fill=fg, anchor="nw", tags="tip"))
            cy = c.bbox(items[-1])[3] + 3
        x0 = min(c.bbox(i)[0] for i in items) - 10
        x1 = max(c.bbox(i)[2] for i in items) + 10
        y0, y1 = y, cy + 6
        # keep the card inside the chart
        dx = min(0, c.winfo_width() - 6 - x1)
        dy = min(0, c.winfo_height() - 6 - y1)
        if dx or dy:
            for i in items:
                c.move(i, dx, dy)
            x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        shadow = c.create_rectangle(x0 + 3, y0 + 4, x1 + 3, y1 + 4, fill="#140d08", outline="", tags="tip")
        rect = c.create_rectangle(x0, y0, x1, y1, fill=PAPER, outline=BRASS_DARK, tags="tip")
        c.tag_lower(rect, items[0])
        c.tag_lower(shadow, rect)

    # ---------- animations ----------
    def pulse(self, name, step=0):
        """A ripple around a freshly recorded port."""
        xy = self.view_xy(name)
        if not xy or self.mode != "collect":
            return
        self.canvas.delete("pulse")
        if step >= 10:
            return
        x, y = self.to_canvas(xy)
        r = MARKER_R + 3 + step * 2.6
        self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=mix(BRASS_LIGHT, PAPER, step / 10),
                                width=max(1, 3 - step * 0.25), tags="pulse")
        self.after(38, lambda: self.pulse(name, step + 1))

    def _reveal_step(self):
        if self.mode != "routes" or self.reveal is None:
            return
        self.reveal += 1
        self.redraw()
        if self.reveal < min(len(self.routes), len(ROUTE_COLORS)):
            self.after(120, self._reveal_step)
        else:
            self.reveal = None

    # ---------- actions ----------
    def build_routes(self, animate=True):
        entering = self.mode != "routes"
        self.mode = "routes"
        self.rebuild_nav()
        self.routes = router.find_routes(self.store.ports, self.sort.get(), hold=self.hold.get(),
                                         hold_overload=self.hold_overload.get(), nav=self.nav,
                                         legs=int(self.legs.get()))
        self.selected = None
        self.refresh_list()
        if animate and entering and self.routes:
            self.reveal = 0
            self._reveal_step()
        else:
            self.reveal = None
            self.redraw()

    def save_rules(self):
        navigation.save_rules(self.rules)

    def on_peace_change(self):
        self.save_settings()
        self.rebuild_nav(force=True)
        self.build_routes(animate=False) if self.mode == "routes" else self.redraw()

    def on_plan_change(self):
        self.save_settings()
        self.build_routes(animate=False)

    def on_rank_change(self):
        self.save_settings()
        self.rebuild_nav()
        self.build_routes(animate=False) if self.mode == "routes" else self.redraw()

    def arm_ship(self):
        self.placing_ship = not self.placing_ship
        self.ship_btn.config(text="Отмена" if self.placing_ship else "Отметить корабль")
        self.canvas.config(cursor="crosshair" if self.placing_ship else "")
        self.update_status()

    def set_ship(self, cell):
        self.ship_xy = tuple(cell)
        self.save_settings()
        if self.nav:
            self.nav.set_ship(self.ship_xy)
        self.build_routes(animate=False) if self.mode == "routes" else self.redraw()

    def toggle_zones(self):
        if self.mode == "shallows":
            self.zone_btn.config(text="Мелководье")
            self.zone_panel.pack_forget()
            self.cancel_draft()
            self.mode = self.mode_before_edit
            self.build_routes(animate=False) if self.mode == "routes" else self.redraw()
        else:
            if self.mode == "edit":
                self.toggle_edit()
            self.mode_before_edit = self.mode
            self.mode = "shallows"
            self.zone_btn.config(text="Готово")
            self.zone_panel.pack(anchor="w", pady=(6, 0))
            self.refresh_list()
        self.redraw()

    def add_point(self, cell, canvas_xy):
        """Left click while drawing: a new corner, or close the ring by clicking the first one."""
        if self.draft and len(self.draft) >= 3:
            first = self.to_canvas(self.cell_to_view(self.draft[0]))
            if math.dist(first, canvas_xy) < 12:
                self.close_zone()
                return
        self.draft.append(tuple(cell))
        self.redraw()

    def undo_point(self):
        if self.mode == "shallows" and self.draft:
            self.draft.pop()
            self.redraw()

    def cancel_draft(self):
        if self.draft:
            self.draft = []
            self.redraw()

    def close_zone(self):
        if self.mode != "shallows" or len(self.draft) < 3:
            return
        kind = self.zone_rank.get()
        zone = (navigation.Zone(0, [list(p) for p in self.draft], navigation.PEACE)
                if kind == navigation.PEACE else navigation.Zone(int(kind), [list(p) for p in self.draft]))
        self.zones.append(zone)
        self.save_rules()
        self.draft = []
        self.save_settings()
        self.rebuild_nav(force=True)
        self.redraw()

    def port_ranks_by_xy(self) -> dict[tuple[float, float], int]:
        out = {}
        for name, port in self.store.ports.items():
            xy, rank = self.store.position(name), navigation.min_rank(port.get("shallow"))
            if xy and rank:
                out[tuple(xy)] = rank
        return out

    def ranks_from_ports(self):
        """Set every zone's rank from the ports inside it - the game already tells us their limits."""
        ranks = self.port_ranks_by_xy()
        changed = 0
        for z in self.zones:
            hint = navigation.suggest_rank(z, ranks)
            if hint and hint != z.rank:
                z.rank, changed = hint, changed + 1
        if changed:
            self.save_rules()
            self.rebuild_nav(force=True)
        empty = sum(1 for z in self.zones if navigation.suggest_rank(z, ranks) is None)
        messagebox.showinfo("Ранги по портам",
                            f"Поправлено зон: {changed}.\n"
                            f"Без портов внутри (ранг остался прежним): {empty}.")
        self.redraw()

    def zone_at(self, cell):
        for z in reversed(self.zones):
            if navigation.points_in_polygon(np.asarray([cell]), z.xy)[0]:
                return z
        return None

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
            self.edit_btn.config(text="Поправить карту")
            if self.mode_before_edit == "routes":
                self.build_routes(animate=False)  # distances may have changed
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
        if self.mode == "shallows" and (e.state & 0x0004):  # Ctrl: this port is closed to the peace flag
            name = self._port_at(e.x, e.y)
            if name:
                self.rules.peace_ports ^= {name}
                self.save_rules()
                self.rebuild_nav(force=True)
                self.redraw()
            return
        if self.mode == "shallows" and (e.state & 0x0001):  # Shift: re-rank the zone under the cursor
            zone = self.zone_at(self.canvas_to_cells(e.x, e.y))
            if zone and zone.kind == navigation.SHALLOW and self.zone_rank.get() != navigation.PEACE:
                zone.rank = int(self.zone_rank.get())
                self.save_rules()
                self.rebuild_nav(force=True)
                self.redraw()
            return
        if self.placing_ship:
            self.set_ship(self.canvas_to_cells(e.x, e.y))
            self.arm_ship()
            return
        if self.mode == "shallows":
            self.add_point(self.canvas_to_cells(e.x, e.y), (e.x, e.y))
            return
        if self.ship_xy and self.canvas.find_withtag("ship"):
            sx, sy = self.to_canvas(self.cell_to_view(self.ship_xy))
            if math.dist((sx, sy), (e.x, e.y)) < 16:  # drag the ship marker itself
                self.drag = {"ship": True, "dx": sx - e.x, "dy": sy - e.y}
                return
        if self.mode != "edit":
            return
        name = self._port_at(e.x, e.y)
        if name:
            px, py = self.to_canvas(self.view_xy(name))
            self.drag = {"name": name, "dx": px - e.x, "dy": py - e.y}

    def on_drag_move(self, e):
        if not self.drag:
            return
        if self.drag.get("ship"):
            self.ship_xy = self.canvas_to_cells(e.x + self.drag["dx"], e.y + self.drag["dy"])
            self.canvas.delete("ship")
            self.draw_ship()
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
        if d.get("ship"):
            self.set_ship(self.ship_xy)
            return
        if "pos" in d:
            self.store.set_position(d["name"], self.canvas_to_cells(*d["pos"]))
        self.redraw()

    def refresh(self):
        if self.mode == "edit":
            self.edit_btn.config(text="Поправить карту")
        self.mode = "collect"
        self.store.new_session()
        self.routes, self.selected, self.reveal = [], None, None
        self.refresh_list()
        self.redraw()

    @staticmethod
    def load_settings() -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            data = {"hold": int(self.hold.get()), "hold_overload": int(self.hold_overload.get()),
                    "ship_rank": self.rank, "zone_rank": int(self.zone_rank.get()),
                    "legs": int(self.legs.get()), "sort": self.sort.get(),
                    "peace": bool(self.peace.get()),
                    "ship_xy": list(self.ship_xy) if self.ship_xy else None}
        except (tk.TclError, ValueError):
            return False
        if self.load_settings() == data:
            return False
        SETTINGS.parent.mkdir(exist_ok=True)
        SETTINGS.write_text(json.dumps(data), encoding="utf-8")
        return True

    def on_hold_change(self):
        if self.save_settings() and self.mode == "routes":
            self.build_routes(animate=False)

    def on_right_click(self, e):
        if self.mode == "shallows":
            if self.draft:
                self.undo_point()
                return
            zone = self.zone_at(self.canvas_to_cells(e.x, e.y))
            if zone and messagebox.askyesno("Стереть зону",
                                            f"Убрать зону {navigation.zone_label(zone.rank)} с карты?"):
                self.zones.remove(zone)
                self.save_rules()
                self.rebuild_nav(force=True)
                self.redraw()
            return
        name = self._port_at(e.x, e.y)
        if name and messagebox.askyesno("Вычеркнуть порт", f"Убрать «{name}» с карты и из журнала?"):
            self.store.remove(name)
            self.build_routes(animate=False) if self.mode == "routes" else self.redraw()

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
                if ev[0] == "scan":
                    _, info, map_xy = ev
                    name = self.store.update(info, map_xy=map_xy)
                    print(f"scanned: {name} ({len(info.goods)} goods)")
                    self.rebuild_nav()
                    if self.mode in ("edit", "shallows"):
                        self.redraw()
                    elif self.mode == "routes":
                        self.build_routes(animate=False)  # prices changed: keep the manifest current
                    elif self.all_scanned():
                        self.build_routes()
                        self.bring_to_front()
                    else:
                        self.redraw()
                        self.pulse(name)
        except queue.Empty:
            pass
        self.after(50, self.poll)


def run():
    import ctypes
    import sys
    # Started with pythonw (no console): keep prints and tracebacks in a log file.
    if sys.stdout is None or sys.stderr is None:
        (HERE.parent / "data").mkdir(exist_ok=True)  # a fresh copy has no data folder yet
        log = open(HERE.parent / "data" / "supercargo.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    # One instance only: a second scanner would just double every scan.
    ctypes.windll.kernel32.CreateMutexW(None, False, "supercargo-wosb-single-instance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Суперкарго", "Суперкарго уже на борту — второй не нужен.")
        return
    app = App()
    app.report_callback_exception = lambda *exc: __import__("traceback").print_exception(*exc)
    app.mainloop()
