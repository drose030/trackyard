"""The two librosa functions adtof_pytorch uses, without librosa's numba/llvmlite stack.

The desktop app leaves librosa out (about 200 MB installed); `install()` then registers
this module as `librosa` before adtof_pytorch is imported. Where librosa is installed
it is used as is. tests/test_librosa_lite.py checks both give the same numbers.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np


def load(path, sr: int = 22050, mono: bool = True):
    """Like librosa.load for the files Trackyard passes: float32, resampled by ffmpeg."""
    import soundfile as sf

    from .audio import decode

    path = Path(path)
    if path.suffix.lower() in (".wav", ".flac") and sf.info(str(path)).samplerate == sr:
        data, _ = sf.read(str(path), dtype="float32", always_2d=True)
    else:
        data = decode(path, sr)
    data = data.T  # librosa's (channels, samples)
    if mono:
        return np.ascontiguousarray(data.mean(axis=0, dtype=np.float32)), sr
    return np.ascontiguousarray(data), sr


def stft(y: np.ndarray, n_fft: int = 2048, hop_length: int | None = None, window=None,
         center: bool = True, pad_mode: str = "constant", block: int = 4096) -> np.ndarray:
    """librosa.stft for a 1-D signal and an explicit window array: (1 + n_fft // 2, frames)."""
    hop = hop_length or n_fft // 4
    win = np.hanning(n_fft) if window is None else np.asarray(window)
    y = np.asarray(y, dtype=np.float32)
    if center:
        if pad_mode != "constant":
            raise ValueError("only pad_mode='constant' is supported")
        y = np.pad(y, n_fft // 2)
    n_frames = 1 + (len(y) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(y, (n_frames, n_fft), (y.strides[0] * hop, y.strides[0]), writeable=False)
    out = np.empty((1 + n_fft // 2, n_frames), dtype=np.complex64)
    for start in range(0, n_frames, block):  # bounded memory for long songs
        out[:, start:start + block] = np.fft.rfft(frames[start:start + block] * win, axis=1).T
    return out


def install() -> None:
    """Use the real librosa if present, else stand in for it."""
    if "librosa" not in sys.modules and importlib.util.find_spec("librosa") is None:
        sys.modules["librosa"] = types.SimpleNamespace(load=load, stft=stft, __name__="librosa")
