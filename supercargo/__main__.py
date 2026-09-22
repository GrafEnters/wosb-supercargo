"""World of Sea Battle trade helper.

  python -m supercargo              the logbook: ports are scanned automatically while you hover them
  python -m supercargo deals        print the best routes from saved prices
  python -m supercargo parse FILE   read a port tooltip off a screenshot file (debugging)
"""
import argparse
import sys

from . import paths, router, tooltip
from .store import Store


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


def main():
    ap = argparse.ArgumentParser(prog="supercargo")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "deals", "parse"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--sort", default="distance", choices=list(router.SORT_KEYS))
    ap.add_argument("--legs", type=int, default=2, choices=[1, 2])
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    paths.redirect_output_to_log()  # the .exe has no console: `Supercargo.exe parse shot.png` goes to the log
    sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "deals":
        print(router.format_routes(router.find_routes(Store().ports, args.sort, legs=args.legs), args.top))
    elif args.command == "parse":
        from PIL import Image
        info, _, _ = tooltip.read_from_screenshot(Image.open(args.file))
        print_port(info)
    else:
        from . import gui
        gui.run()


if __name__ == "__main__":
    main()
