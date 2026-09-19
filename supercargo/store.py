"""Persistent market snapshot: latest known prices per port."""
import difflib
import json
import time
from pathlib import Path

from . import layout
from .tooltip import PortInfo

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "market.json"


class Store:
    def __init__(self, path: Path = DEFAULT_PATH, layout_path: Path = layout.LAYOUT_PATH):
        self.path = path
        self.layout_path = layout_path
        self.positions = layout.load(layout_path)  # fixed port positions, win over scanned ones
        self.ports: dict[str, dict] = {}
        # Ports updated after this moment count as scanned in the current "collect prices" round.
        self.session_start = 0.0
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.ports = data.get("ports", {})
            self.session_start = data.get("session_start", 0.0)
        for name, p in self.ports.items():
            if name in self.positions:
                p["map_xy"] = self.positions[name]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"session_start": self.session_start, "ports": self.ports}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def known_goods(self) -> list[str]:
        return sorted({g for p in self.ports.values() for g in p["goods"]})

    def all_names(self) -> list[str]:
        """Every known port: with prices and/or with a fixed position."""
        return list(dict.fromkeys([*self.positions, *self.ports]))

    def position(self, name: str):
        return self.positions.get(name) or self.ports.get(name, {}).get("map_xy")

    def set_position(self, name: str, xy):
        self.positions[name] = list(xy)
        layout.save(self.positions, self.layout_path)
        if name in self.ports:
            self.ports[name]["map_xy"] = list(xy)
            self.save()

    def is_scanned(self, name: str) -> bool:
        return name in self.ports and self.ports[name]["updated"] >= self.session_start

    def new_session(self):
        self.session_start = time.time()
        self.save()

    def remove(self, name: str):
        self.ports.pop(name, None)
        self.save()
        if self.positions.pop(name, None) is not None:
            layout.save(self.positions, self.layout_path)

    def resolve_name(self, name: str) -> str:
        """Map a slightly misread port name onto an already known one."""
        names = self.all_names()
        if name in names:
            return name
        match = difflib.get_close_matches(name, names, n=1, cutoff=0.85)
        return match[0] if match else name

    def update(self, info: PortInfo, map_xy: tuple[float, float] | None = None) -> str:
        """map_xy: where the scan saw the port (grid cells, see mapgeo). Used only for ports
        without a fixed position; such a port gets its position fixed right away."""
        info.name = self.resolve_name(info.name)
        if info.name not in self.positions and map_xy:
            self.positions[info.name] = list(map_xy)
            layout.save(self.positions, self.layout_path)
        goods = {
            g.name: {"buy": g.buy, "sell": g.sell, "stock": g.stock}
            for g in info.goods
            if g.buy is not None or g.sell is not None
        }
        self.ports[info.name] = {
            "updated": time.time(),
            "tax": info.tax,
            "shallow": info.shallow,
            "map_xy": self.position(info.name),
            "goods": goods,
        }
        self.save()
        return info.name
