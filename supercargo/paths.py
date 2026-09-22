"""Where things live: bundled assets next to the code, the player's data next to the program.

In the stand-alone build (PyInstaller) the code sits in _internal/, so the data folder goes next to
Supercargo.exe instead - unpacking a newer version over the old one then keeps the logbook.
"""
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent  # images, templates, ports_layout.json, shallows.json
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else PACKAGE.parent
DATA = ROOT / "data"  # prices, settings, log


def data_file(name: str) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    return DATA / name


def redirect_output_to_log():
    """Without a console (pythonw, the .exe) print() and tracebacks would vanish: keep them in a log."""
    if sys.stdout is None or sys.stderr is None:
        log = open(data_file("supercargo.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
