"""Build desktop/build/python: a self-contained Python with Trackyard and only the
packages a conversion loads. electron-builder copies it into the app. Model weights
aren't bundled; the app downloads them on the first conversion.

Runs on macOS (Apple Silicon) and Windows (x64):

    uv run --no-project --python 3.11 scripts/bundle.py
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "3.11"
DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent
BUILD = DESKTOP / "build"
WINDOWS = sys.platform == "win32"

# Never imported when converting or editing (checked by listing sys.modules after a
# conversion): librosa and its numba/llvmlite/scikit-learn stack (trackyard.librosa_lite
# stands in), and PyTorch's symbolic-maths and graph dependencies.
UNUSED = """librosa numba llvmlite scikit-learn scipy joblib threadpoolctl pooch platformdirs msgpack
audioread lazy-loader decorator sympy mpmath networkx narwhals pip setuptools""".split()


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def standalone_python() -> Path:
    """uv's managed Pythons are python-build-standalone builds, which can be moved anywhere."""
    run("uv", "python", "install", VERSION)
    root = subprocess.run(["uv", "python", "dir"], check=True, capture_output=True, text=True).stdout.strip()
    arch = "windows-x86_64-none" if WINDOWS else "macos-aarch64-none"
    found = sorted(glob.glob(os.path.join(root, f"cpython-{VERSION}.*-{arch}")),
                   key=lambda p: [int(x) for x in Path(p).name.split("-")[1].split(".")])
    if not found:
        sys.exit(f"no managed Python {VERSION} for {arch} in {root}")
    return Path(found[-1])


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    python = BUILD / "python"
    shutil.copytree(standalone_python(), python, symlinks=True)
    lib = python / "Lib" if WINDOWS else python / "lib" / f"python{VERSION}"
    exe = python / "python.exe" if WINDOWS else python / "bin" / "python3"
    (lib / "EXTERNALLY-MANAGED").unlink(missing_ok=True)

    # --refresh-package: never reuse a cached build of the local code.
    run("uv", "pip", "install", "--python", str(exe), "--break-system-packages", "--no-compile",
        "--refresh-package", "trackyard", str(REPO))
    run("uv", "pip", "uninstall", "--python", str(exe), "--break-system-packages", *UNUSED)

    site = lib / "site-packages"
    remove = [site / "torch" / name for name in ("include", "_inductor", "onnx", "_dynamo")]
    remove += [lib / name for name in ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3")]
    remove += [python / "include", python / "share", python / "tcl"]
    remove += [Path(p) for p in glob.glob(str(python / "lib" / "t[ck]*8*")) + glob.glob(str(python / "lib" / "thread2*"))]
    remove += [Path(p) for p in glob.glob(str(python / "DLLs" / "_tkinter*")) + glob.glob(str(python / "DLLs" / "t[ck]*86t.dll"))]
    remove += [Path(p) for p in glob.glob(str(python / "bin" / "idle3*"))]
    for path in remove:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    # The app writes bytecode to its own data folder (PYTHONPYCACHEPREFIX), not into itself.
    for cache in list(python.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)

    size = sum(f.stat().st_size for f in python.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"{python}: {size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
