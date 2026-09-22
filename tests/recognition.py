"""Recognition regression bench: read every tooltip screenshot, compare with a reference and with truth.

    # record what a reference version of the code reads (e.g. from a git worktree of an older commit)
    python -m tests.recognition --update
    # check the current code: accuracy against hand-checked answers, differences from the reference, timings
    python -m tests.recognition

Screenshots come from data/frames (the admin toggle "сохранять кадры для тестов" fills it) and from any
extra --frames folders. They are the player's own screenshots and stay out of the repository;
so do data/golden.json (the reference run) and data/truth.json (answers checked by eye).
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from supercargo import bench, paths


def report(results: dict, times: list[dict]):
    finds = [t["find"] * 1000 for t in times]
    totals = [t["total"] * 1000 for t in times]
    ok = [r for r in results.values() if "error" not in r]
    print(f"кадров {len(results)}, прочитано {len(ok)}, товаров {sum(len(r['goods']) for r in ok)}")
    print(f"поиск подсказки: медиана {statistics.median(finds):.0f} мс, макс {max(finds):.0f} мс")
    print(f"разбор целиком:  медиана {statistics.median(totals):.0f} мс, макс {max(totals):.0f} мс")


def check(results: dict, golden_path: Path, truth_path: Path) -> bool:
    if truth_path.exists():
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        golden = json.loads(golden_path.read_text(encoding="utf-8")) if golden_path.exists() else {}
        for label, data in (("эталон", golden), ("сейчас", results)):
            total, errors = bench.score(truth, data)
            if total:
                print(f"точность ({label}): {total - len(errors)}/{total} полей верно "
                      f"({(total - len(errors)) / total:.1%})")
                for e in errors:
                    print("      ", e)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    total, diffs = bench.score(golden, results)
    missing = [k for k in results if k not in golden]
    print(f"сверено с эталоном полей: {total}, расхождений: {len(diffs)}"
          + (f", кадров без эталона: {len(missing)}" if missing else ""))
    for d in diffs:
        print("   ", d)
    return not diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=Path, action="append", default=[])
    ap.add_argument("--golden", type=Path, default=paths.DATA / "golden.json")
    ap.add_argument("--truth", type=Path, default=paths.DATA / "truth.json")
    ap.add_argument("--update", action="store_true", help="record the current code's output as the reference")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    files = bench.frames_in([paths.DATA / "frames", *args.frames])
    if not files:
        sys.exit("нет кадров: включи в админке «сохранять кадры для тестов» и пройдись по портам")
    results, times = bench.read_all(files)
    report(results, times)
    if args.update:
        args.golden.parent.mkdir(parents=True, exist_ok=True)
        args.golden.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"эталон записан: {args.golden}")
        return
    sys.exit(0 if check(results, args.golden, args.truth) else 1)


if __name__ == "__main__":
    main()
