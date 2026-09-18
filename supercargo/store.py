"""Persistent market snapshot: latest known prices per port."""
import json
import time
from pathlib import Path

from .tooltip import PortInfo

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "market.json"


class Store:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self.ports: dict[str, dict] = {}
        if path.exists():
            self.ports = json.loads(path.read_text(encoding="utf-8")).get("ports", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ports": self.ports}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def known_goods(self) -> list[str]:
        return sorted({g for p in self.ports.values() for g in p["goods"]})

    def update(self, info: PortInfo, map_pos: tuple[int, int] | None = None):
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
            "map_pos": list(map_pos) if map_pos else prev.get("map_pos"),
            "goods": goods,
        }
        self.save()
