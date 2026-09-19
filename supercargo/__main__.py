"""World of Sea Battle trade helper.

  python -m supercargo              map window: ports are scanned automatically while you hover them
  python -m supercargo console      hotkey mode (F8), text output in the console
  python -m supercargo deals        print best deals from saved prices
  python -m supercargo parse FILE   parse a screenshot file (debug)
"""
import argparse
import re
import ctypes
import ctypes.wintypes as wt
import sys
import time
import traceback
import winsound
from pathlib import Path

from PIL import Image

from . import capture, mapgeo, router, tooltip
from .store import Store

DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"

VK = {f"F{i}": 0x6F + i for i in range(1, 13)}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def print_port(info: tooltip.PortInfo):
    extra = []
    if info.tax is not None:
        extra.append(f"налог {info.tax:g}%")
    if info.shallow:
        extra.append(f"мелководье {info.shallow}")
    print(f"\n== {info.name}" + (f" ({', '.join(extra)})" if extra else ""))
    for g in info.goods:
        stock = f"{g.stock / 1000:g}k" if g.stock else "?"
        warn = f"  [!] {'; '.join(g.warnings)}" if g.warnings else ""
        print(f"   {g.name:<14}{stock:>8}   купить {g.buy}   продать {g.sell}{warn}")
    for w in info.warnings:
        print(f"   [!] {w}")


def capture_once(store: Store, args) -> bool:
    t0 = time.time()
    img, _, cursor = capture.grab_game()
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        info, crop, _ = tooltip.read_from_screenshot(img, store.known_goods())
    except tooltip.TooltipNotFound as e:
        path = DEBUG_DIR / f"fail_{int(time.time())}.png"
        img.save(path)
        print(f"\n[x] Подсказка порта не найдена ({e}). Скриншот: {path}")
        return False
    crop.save(DEBUG_DIR / "last_tooltip.png")
    (DEBUG_DIR / "tooltips").mkdir(exist_ok=True)
    crop.save(DEBUG_DIR / "tooltips" / f"{re.sub(r'[^\w\- ]', '_', info.name)}.png")
    if not info.goods:
        print_port(info)
        return False
    geo = mapgeo.locate(img)
    store.update(info, map_xy=geo.to_map(*cursor) if geo else None)
    print_port(info)
    print(f"   ({time.time() - t0:.1f}s, портов в базе: {len(store.ports)})")
    print()
    print(router.format_routes(router.find_routes(store.ports, args.sort), args.top))
    return True


def run_hotkey(args):
    store = Store()
    vk = VK[args.key.upper()]
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, MOD_NOREPEAT, vk):
        sys.exit(f"Не удалось зарегистрировать {args.key} - клавиша занята другой программой.")
    print(f"Готово. Открой карту, наведи курсор на порт и нажми {args.key}. Ctrl+C - выход.")
    print(f"Портов в базе: {len(store.ports)}")
    msg = wt.MSG()
    try:
        while True:
            # PeekMessage instead of GetMessage so Ctrl+C still works.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    try:
                        ok = capture_once(store, args)
                    except Exception:
                        traceback.print_exc()
                        ok = False
                    winsound.Beep(1200 if ok else 400, 120)
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnregisterHotKey(None, 1)


def main():
    ap = argparse.ArgumentParser(prog="supercargo")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "console", "deals", "parse"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--key", default="F8", help="hotkey (F1..F12)")
    ap.add_argument("--sort", default="trip", choices=list(router.SORT_KEYS))
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if sys.stdout is not None:  # None under pythonw (no console)
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "deals":
        print(router.format_routes(router.find_routes(Store().ports, args.sort), args.top))
    elif args.command == "parse":
        info, _, _ = tooltip.read_from_screenshot(Image.open(args.file))
        print_port(info)
    elif args.command == "console":
        run_hotkey(args)
    else:
        from . import gui
        gui.run()


if __name__ == "__main__":
    main()
