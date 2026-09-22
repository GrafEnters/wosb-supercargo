"""Build the stand-alone Supercargo (no Python needed on the player's PC) and zip it for a release.

    .venv\\Scripts\\python -m pip install pyinstaller
    .venv\\Scripts\\python build.py

Result: dist/Supercargo/Supercargo.exe and dist/Supercargo-<version>.zip
"""
import shutil
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

import supercargo

ROOT = Path(__file__).resolve().parent
SITE = Path(sys.prefix) / "Lib" / "site-packages"
# winrt ships an old msvcp140.dll (VS 2019). Loaded first, it crashes onnxruntime, which wants a newer
# one - both inside PyInstaller's analysis and in the built app. With it out of the way the bundle
# gets the system's current Visual C++ runtime instead, which is backward compatible.
WINRT_MSVCP = SITE / "winrt" / "msvcp140.dll"

ASSETS = ["background_map.png", "header_tpl.png", "icon.ico", "goods.json", "ports_layout.json", "shallows.json"]


def build():
    args = [
        str(ROOT / "launcher.py"),
        "--noconfirm", "--clean", "--windowed",
        "--name", "Supercargo",
        "--icon", str(ROOT / "supercargo" / "icon.ico"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--collect-all", "rapidocr_onnxruntime",  # models and config.yaml live inside the package
        "--collect-all", "winrt",  # Windows OCR bindings are namespace packages PyInstaller can't see
        "--collect-binaries", "onnxruntime",
    ]
    for name in ASSETS:
        args += ["--add-data", f"{ROOT / 'supercargo' / name};supercargo"]
    aside = WINRT_MSVCP.with_suffix(".dll.aside")
    if WINRT_MSVCP.exists():
        WINRT_MSVCP.rename(aside)
    try:
        PyInstaller.__main__.run(args)
    finally:
        if aside.exists():
            aside.rename(WINRT_MSVCP)


def package() -> Path:
    app = ROOT / "dist" / "Supercargo"
    for name in ("README.md", "LICENSE"):
        shutil.copy(ROOT / name, app / name)
    out = ROOT / "dist" / f"Supercargo-{supercargo.__version__}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, Path("Supercargo") / f.relative_to(app))
    return out


if __name__ == "__main__":
    build()
    zip_path = package()
    size = sum(f.stat().st_size for f in (ROOT / "dist" / "Supercargo").rglob("*") if f.is_file())
    print(f"\nГотово: {zip_path.name}  ({zip_path.stat().st_size / 2**20:.0f} МБ в архиве, "
          f"{size / 2**20:.0f} МБ распакованной)")
