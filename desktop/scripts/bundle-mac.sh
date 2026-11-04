#!/usr/bin/env bash
# Builds desktop/build/python: a self-contained Python with Trackyard and only the
# packages a conversion loads. electron-builder copies it into the app. Model weights
# aren't bundled; the app downloads them on the first conversion.
set -euo pipefail

desktop="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$desktop/.." && pwd)"
build="$desktop/build"
version=3.11

rm -rf "$build"
mkdir -p "$build"

# uv's managed Pythons are python-build-standalone builds, which can be moved anywhere.
uv python install "$version"
base="$(ls -d "$(uv python dir)"/cpython-$version.*-macos-aarch64-none | sort -V | tail -1)"
cp -R "$base" "$build/python"
py="$build/python/bin/python3"
lib="$build/python/lib/python$version"
rm -f "$lib/EXTERNALLY-MANAGED"

# --refresh-package: never reuse a cached build of the local code.
uv pip install --python "$py" --break-system-packages --no-compile --refresh-package trackyard "$repo"

# Never imported when converting or editing (checked by listing sys.modules after a
# conversion): librosa and its numba/llvmlite/scikit-learn stack (trackyard.librosa_lite
# stands in), and PyTorch's symbolic-maths and graph dependencies.
uv pip uninstall --python "$py" --break-system-packages \
  librosa numba llvmlite scikit-learn scipy joblib threadpoolctl pooch platformdirs msgpack \
  audioread lazy-loader decorator sympy mpmath networkx narwhals pip setuptools

site="$lib/site-packages"
rm -rf "$site/torch/include" "$site/torch/_inductor" "$site/torch/onnx" "$site/torch/_dynamo" \
  "$build/python/include" "$build/python/share" "$lib/test" "$lib/idlelib" "$lib/tkinter" "$lib/turtledemo" \
  "$lib/ensurepip" "$lib/lib2to3" "$build/python/lib/"{tcl8,tcl8.6,tk8.6,thread2.8.9} "$build/python/bin/idle3"*
# The app writes bytecode to its own data folder (PYTHONPYCACHEPREFIX), not into itself.
find "$build/python" -name __pycache__ -type d -prune -exec rm -rf {} +

du -sh "$build/python"
