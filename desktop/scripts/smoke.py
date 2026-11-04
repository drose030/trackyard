"""Convert a generated drum loop with the bundled Python, to catch missing packages
before an installer ships. Like in the app, stdin is a pipe that a thread blocks on
(smoke.js keeps it open). Run it with desktop/build's Python:

    build/python/bin/python3 scripts/smoke.py      (Windows: build\\python\\python.exe)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from trackyard import analysis
from trackyard.editor.server import exit_with_parent
from trackyard.pipeline import convert


def drum_loop(seconds: float = 20.0, bpm: float = 100.0, sr: int = 44100) -> np.ndarray:
    """Kick on 1 and 3, snare on 2 and 4, eighth-note hats, over a quiet bass tone."""
    t = np.arange(int(seconds * sr)) / sr
    out = 0.05 * np.sin(2 * np.pi * 55 * t)
    rng = np.random.default_rng(0)
    beat = 60 / bpm
    for i in range(int(seconds / beat * 2)):
        start = int(i * beat / 2 * sr)
        n = min(len(t) - start, int(0.2 * sr))
        env = np.exp(-np.arange(n) / sr * 30)
        if i % 4 == 0:
            out[start:start + n] += 0.8 * np.sin(2 * np.pi * 60 * np.arange(n) / sr) * env
        elif i % 4 == 2:
            out[start:start + n] += 0.5 * rng.normal(size=n) * env
        out[start:start + n] += 0.15 * rng.normal(size=n) * np.exp(-np.arange(n) / sr * 120)
    return np.stack([out, out], axis=1).astype(np.float32) * 0.8


def main() -> None:
    exit_with_parent()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        song = tmp / "Smoke - Loop.wav"
        sf.write(song, drum_loop(), 44100)
        folder = convert(song, tmp / "out", tmp / "cache", analysis.best_device())
        notes = (folder / "notes.mid").stat().st_size
        print(f"smoke test ok: {folder.name}, notes.mid {notes} bytes, librosa stand-in: "
              f"{'librosa' not in sys.modules or type(sys.modules['librosa']).__name__ != 'module'}")


if __name__ == "__main__":
    main()
