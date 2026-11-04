"""Decode audio with ffmpeg and mix stems into one file."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100


def ffmpeg() -> str:
    """ffmpeg on the PATH, else the copy bundled with imageio-ffmpeg (the desktop app)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def decode(path: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Any ffmpeg-readable file → float32 array of shape (samples, 2)."""
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def mix(paths: list[Path]) -> np.ndarray:
    """Sum stems (padding to the longest); attenuate only if the sum would clip."""
    tracks = [decode(p) for p in paths]
    out = np.zeros((max(len(t) for t in tracks), 2), dtype=np.float32)
    for t in tracks:
        out[: len(t)] += t
    peak = float(np.abs(out).max())
    if peak > 0.99:
        out *= 0.99 / peak
    return out


def write(path: Path, audio: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sr, subtype="PCM_16")


def read(path: Path) -> np.ndarray:
    audio, _ = sf.read(path, dtype="float32", always_2d=True)
    return audio
