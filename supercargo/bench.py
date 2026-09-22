"""Reading tooltip screenshots in bulk and comparing the results - shared by the test bench
(tests/recognition.py) and the stand-alone build's self-test (`Supercargo.exe selftest DIR`)."""
import json
import time
from pathlib import Path

from PIL import Image

from . import tooltip

FIELDS = ("name", "tax", "shallow")
GOOD_FIELDS = ("buy", "sell", "stock")


def frames_in(folders) -> list[Path]:
    files = []
    for folder in folders:
        folder = Path(folder)
        if folder.is_file():
            files.append(folder)
        elif folder.is_dir():
            files += sorted(folder.glob("*.png"))
    return files


def read(path: Path) -> tuple[dict, dict]:
    """Everything the parser reads off one screenshot, plus how long the two stages took."""
    img = Image.open(path).convert("RGB")
    t0 = time.perf_counter()
    found = tooltip.find_header(img)
    t1 = time.perf_counter()
    try:
        info, _, _ = tooltip.read_from_screenshot(img, (), found)
    except tooltip.TooltipNotFound as e:
        return {"error": str(e)}, {"find": t1 - t0, "total": time.perf_counter() - t0}
    t2 = time.perf_counter()
    result = {
        "header": [round(found[0]), round(found[1])] if found else None,
        "name": info.name, "tax": info.tax, "shallow": info.shallow,
        "goods": [{"name": g.name, "buy": g.buy, "sell": g.sell, "stock": g.stock} for g in info.goods],
    }
    return result, {"find": t1 - t0, "total": t2 - t0}


def read_all(files: list[Path]) -> tuple[dict, list[dict]]:
    if files:  # warm up the models so the first frame's timing is honest
        read(files[0])
    results, times = {}, []
    for f in files:
        res, t = read(f)
        results[f.name] = res
        times.append(t)
    return results, times


def compare(key: str, want: dict, got: dict) -> list[str]:
    diffs = []
    if "error" in want or "error" in got:
        if want.get("error") != got.get("error"):
            diffs.append(f"{key}: ошибка было={want.get('error')!r} стало={got.get('error')!r}")
        return diffs
    if want.get("header") != got.get("header"):
        diffs.append(f"{key}: заголовок {want.get('header')} -> {got.get('header')}")
    for f in FIELDS:
        if want.get(f) != got.get(f):
            diffs.append(f"{key}: {f} {want.get(f)!r} -> {got.get(f)!r}")
    wg = {g["name"]: g for g in want.get("goods", [])}
    gg = {g["name"]: g for g in got.get("goods", [])}
    for name in sorted(set(wg) | set(gg)):
        if name not in gg or name not in wg:
            diffs.append(f"{key}: товар {name} {'пропал' if name not in gg else 'появился'}")
            continue
        for f in GOOD_FIELDS:
            if wg[name][f] != gg[name][f]:
                diffs.append(f"{key}: {name}.{f} {wg[name][f]} -> {gg[name][f]}")
    return diffs


def field_count(result: dict) -> int:
    return len(FIELDS) + 1 + len(GOOD_FIELDS) * len(result.get("goods", []))


def score(reference: dict, results: dict) -> tuple[int, list[str]]:
    """(fields compared, differences) of results against a reference, over the frames both have."""
    keys = [k for k in reference if k in results]
    diffs = [d for k in keys for d in compare(k, reference[k], results[k])]
    return sum(field_count(reference[k]) for k in keys), diffs


def selftest(folders, out: Path):
    """What the .exe runs on `selftest DIR`: read every frame and save the results for comparison."""
    results, times = read_all(frames_in(folders))
    out.write_text(json.dumps({"results": results, "times": times}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
