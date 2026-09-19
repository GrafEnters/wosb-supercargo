"""Fixed port positions on the map (grid cells, see mapgeo). Hand-tuned via drag & drop in the GUI.

Positions here take priority over whatever a scan measured; a scan only places ports missing from this file.
"""
import json
from pathlib import Path

LAYOUT_PATH = Path(__file__).resolve().parent / "ports_layout.json"


def load(path: Path = LAYOUT_PATH) -> dict[str, list[float]]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save(positions: dict[str, list[float]], path: Path = LAYOUT_PATH):
    data = {name: [round(x, 3), round(y, 3)] for name, (x, y) in sorted(positions.items())}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
