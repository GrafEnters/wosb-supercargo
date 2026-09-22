"""Build the stand-alone Supercargo (no Python needed on the player's PC), check it, zip it for a release.

    .venv\\Scripts\\python -m pip install pyinstaller onnx
    .venv\\Scripts\\python build.py

Result: dist/Supercargo/Supercargo.exe and dist/Supercargo-<version>.zip.
If there are test screenshots (data/frames, data/bench), the built .exe reads them all and every field is
compared with what the source code reads - the build fails on any difference.
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import PyInstaller.__main__
from PIL import Image

import supercargo
from supercargo import bench, numocr, textrec

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "Supercargo"
SITE = Path(sys.prefix) / "Lib" / "site-packages"
# winrt ships an old msvcp140.dll (VS 2019). Loaded first, it crashes onnxruntime, which wants a newer
# one - both inside PyInstaller's analysis and in the built app. With it out of the way the bundle
# gets the system's current Visual C++ runtime instead, which is backward compatible.
WINRT_MSVCP = SITE / "winrt" / "msvcp140.dll"

ASSETS = ["header_tpl.png", "icon.ico", "goods.json", "ports_layout.json", "shallows.json"]
PREPARED = ROOT / "build" / "assets"  # assets made lighter for the build (see prepare_assets)
MODEL = PREPARED / textrec.MODEL_NAME
# Big libraries the source version uses but the player's build does without:
#   cv2 - only places brand-new ports on the map (all known ports have fixed spots); 110 MB
#   rapidocr_onnxruntime (+ shapely, pyclipper, yaml) - we run its recognition model ourselves (textrec)
#   _ssl/_hashlib - no network, no OpenSSL (5 MB); PIL._avif - no AVIF images (7.5 MB)
EXCLUDE = ["cv2", "rapidocr_onnxruntime", "shapely", "pyclipper", "yaml", "_ssl", "ssl", "_hashlib",
           "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL._imagingcms",
           "PIL.ImageCms", "PIL._imagingmath", "PIL.ImageMath", "unittest", "pydoc", "numpy.f2py",
           "onnxruntime.transformers", "onnxruntime.tools", "onnxruntime.quantization"]
# Tcl data the window never touches: time zones (only for Tcl's own `clock`, which we don't use) and the
# multi-byte East Asian code pages (only loaded when Windows itself runs in Chinese, Japanese or Korean).
TCL_DROP = ["tzdata", "encoding/big5.enc", "encoding/cns11643.enc", "encoding/cp932.enc", "encoding/cp936.enc",
            "encoding/cp949.enc", "encoding/cp950.enc", "encoding/euc-cn.enc", "encoding/euc-jp.enc",
            "encoding/euc-kr.enc", "encoding/gb12345.enc", "encoding/gb1988.enc", "encoding/gb2312.enc",
            "encoding/gb2312-raw.enc", "encoding/iso2022.enc", "encoding/iso2022-jp.enc",
            "encoding/iso2022-kr.enc", "encoding/jis0201.enc", "encoding/jis0208.enc", "encoding/jis0212.enc",
            "encoding/ksc5601.enc", "encoding/macJapan.enc", "encoding/shiftjis.enc"]


def prepare_assets():
    """Lighter copies of the two biggest assets.
    - The digit model with its weights stored as float16 and cast back to float32 when loaded: half the size,
      the same maths at run time. On 168 shifted/brightened variants of the test frames (4128 fields) it reads
      exactly what the original does (tests/robustness.py).
    - The background map as a high-quality JPEG instead of PNG."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    PREPARED.mkdir(parents=True, exist_ok=True)
    model = onnx.load(str(textrec.model_path()))
    nodes = []
    for node in model.graph.node:  # this export keeps its weights in Constant nodes
        if node.op_type == "Constant" and node.attribute[0].name == "value":
            weights = numpy_helper.to_array(node.attribute[0].t)
            if weights.dtype == np.float32 and weights.size >= 64:
                half = node.output[0] + "__fp16"
                nodes.append(helper.make_node("Constant", [], [half],
                                              value=numpy_helper.from_array(weights.astype(np.float16), half)))
                nodes.append(helper.make_node("Cast", [half], [node.output[0]], to=TensorProto.FLOAT))
                continue
        nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    onnx.save(model, str(MODEL))
    Image.open(ROOT / "supercargo" / "background_map.png").convert("RGB").save(
        PREPARED / "background_map.jpg", quality=92, optimize=True, progressive=True)


def build():
    args = [
        str(ROOT / "launcher.py"),
        "--noconfirm", "--clean", "--windowed",
        "--name", "Supercargo",
        "--icon", str(ROOT / "supercargo" / "icon.ico"),
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--collect-all", "winrt",  # Windows OCR bindings are namespace packages PyInstaller can't see
        "--collect-binaries", "onnxruntime",
        "--add-data", f"{MODEL};supercargo/models",
        "--add-data", f"{PREPARED / 'background_map.jpg'};supercargo",
    ]
    for name in ASSETS:
        args += ["--add-data", f"{ROOT / 'supercargo' / name};supercargo"]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    aside = WINRT_MSVCP.with_suffix(".dll.aside")
    if WINRT_MSVCP.exists():
        WINRT_MSVCP.rename(aside)
    try:
        PyInstaller.__main__.run(args)
    finally:
        if aside.exists():
            aside.rename(WINRT_MSVCP)
    tcl = APP / "_internal" / "_tcl_data"
    for rel in TCL_DROP:
        target = tcl / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def check() -> bool:
    """The built .exe must read every test screenshot exactly as the source code does: same port, tax,
    shallows, goods, prices and stock, field by field. Accuracy against hand-checked answers is shown too."""
    folders = [p for p in (ROOT / "data" / "frames", ROOT / "data" / "bench") if p.is_dir()]
    if not folders:
        print("самопроверка пропущена: нет тестовых кадров (data/frames, data/bench)")
        return True
    numocr._engine = textrec.Recognizer(MODEL)  # the source side reads with the very model the build carries
    source, _ = bench.read_all(bench.frames_in(folders))
    built, times = {}, []
    out = APP / "data" / "selftest.json"
    for folder in folders:
        subprocess.run([str(APP / "Supercargo.exe"), "selftest", str(folder)], check=True, timeout=900)
        part = json.loads(out.read_text(encoding="utf-8"))
        built.update(part["results"])
        times += part["times"]
    total, diffs = bench.score(source, built)
    truth_path = ROOT / "data" / "truth.json"
    if truth_path.exists():
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        t_total, errors = bench.score(truth, built)
        print(f"точность .exe по выверенным ответам: {t_total - len(errors)}/{t_total} полей")
    finds = sorted(t["find"] * 1000 for t in times)
    totals = sorted(t["total"] * 1000 for t in times)
    print(f"самопроверка .exe: кадров {len(built)}, сверено с исходниками полей {total}, "
          f"расхождений {len(diffs)}; поиск подсказки {finds[len(finds) // 2]:.0f} мс, "
          f"разбор {totals[len(totals) // 2]:.0f} мс (медианы)")
    for d in diffs:
        print("   ", d)
    shutil.rmtree(APP / "data", ignore_errors=True)  # the player's archive starts with an empty logbook
    return not diffs




README_TXT = """СУПЕРКАРГО {version} — судовой журнал торговца для World of Sea Battle

Как пользоваться
  1. Запусти игру и Supercargo.exe.
  2. Выбери ранг своего корабля и отметь, где он стоит.
  3. В игре открой карту мира и задерживай курсор на портах —
     каждый записанный порт получает зелёную галочку.
  4. Когда все порты в описи, Суперкарго сам проложит лучшие курсы.

Это не чит: программа только делает скриншот окна и читает с него текст,
как OBS. В память игры, её файлы и сетевой трафик она не лезет.

Если Windows пишет «Система Windows защитила ваш компьютер» —
нажми «Подробнее» → «Выполнить в любом случае».

Журнал хранится в папке data рядом с программой. Новую версию можно
распаковать поверх старой — журнал сохранится.

Вопросы, идеи, баги: Telegram https://t.me/Graf_Enters
Друзья в игре: ник GrafEnters, гильдия [ZGS]
Код и новые версии: https://github.com/GrafEnters/wosb-supercargo

Попутного ветра и полных трюмов!
"""


def package() -> Path:
    shutil.copy(ROOT / "LICENSE", APP / "LICENSE.txt")
    # Notepad-friendly: README.md is GitHub markup with pictures that are not in the archive
    (APP / "README.txt").write_text(README_TXT.format(version=supercargo.__version__), encoding="utf-8-sig")
    out = DIST / f"Supercargo-{supercargo.__version__}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            if f.is_file():
                z.write(f, Path("Supercargo") / f.relative_to(APP))
    return out


def sizes() -> str:
    unpacked = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
    return f"{unpacked / 2**20:.0f} МБ распакованной"


if __name__ == "__main__":
    if "--package-only" not in sys.argv:
        prepare_assets()
        build()
    ok = check()
    zip_path = package()
    print(f"\nГотово: {zip_path.name}  ({zip_path.stat().st_size / 2**20:.0f} МБ в архиве, {sizes()})")
    sys.exit(0 if ok else 1)
