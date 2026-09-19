"""Persistent market snapshot: latest known prices per port."""
import difflib
import json
import time
from pathlib import Path

from .tooltip import PortInfo

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "market.json"


class Store:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.ports: dict[str, dict] = {}
        # Ports updated after this moment count as scanned in the current "collect prices" round.
        self.session_start = 0.0
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.ports = data.get("ports", {})
            self.session_start = data.get("session_start", 0.0)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"session_start": self.session_start, "ports": self.ports}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def known_goods(self) -> list[str]:
        return sorted({g for p in self.ports.values() for g in p["goods"]})

    def is_scanned(self, name: str) -> bool:
        return self.ports[name]["updated"] >= self.session_start

    def new_session(self):
        self.session_start = time.time()
        self.save()

    def remove(self, name: str):
        self.ports.pop(name, None)
        self.save()

    def resolve_name(self, name: str) -> str:
        """Map a slightly misread port name onto an already known one."""
        if name in self.ports:
            return name
        match = difflib.get_close_matches(name, list(self.ports), n=1, cutoff=0.85)
        return match[0] if match else name

    def update(self, info: PortInfo, map_xy: tuple[float, float] | None = None) -> str:
        """map_xy: port position on the flat map in grid cells (see mapgeo)."""
        info.name = self.resolve_name(info.name)
        prev = self.ports.get(info.name, {})
        goods = {
            g.name: {"buy": g.buy, "sell": g.sell, "stock": g.stock}
            for g in info.goods
            if g.buy is not None or g.sell is not None
        }
        self.ports[info.name] = {
            "updated": time.time(),
            "tax": info.tax,
            "shallow": info.shallow,
            # The cursor sits on the port icon when the hotkey is pressed: free map coordinates.
            "map_xy": list(map_xy) if map_xy else prev.get("map_xy"),
            "goods": goods,
        }
        self.save()
        return info.name
