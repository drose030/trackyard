"""Model wrappers: Demucs drum separation, ADTOF drum transcription, Beat This! beats.

Models load lazily and stay loaded for the whole run.
"""

from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import torch

from . import audio as audio_io

ADTOF_FPS = 100
# ADTOF output order (General MIDI pitches 35, 38, 47, 42, 49) in our class names.
ADTOF_CLASSES = ("KD", "SD", "TT", "HH", "CY")


def best_device() -> str:
    return "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")


# MARK: - Demucs


@functools.cache
def _demucs(name: str):
    from demucs.pretrained import get_model

    model = get_model(name)
    model.eval()
    return model


def separate_drums(mix: np.ndarray, device: str, model_name: str = "htdemucs") -> np.ndarray:
    """(samples, 2) mix at 44.1 kHz → (samples, 2) drum stem."""
    from demucs.apply import apply_model

    model = _demucs(model_name)
    wav = torch.from_numpy(np.ascontiguousarray(mix.T))
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std
    try:
        sources = apply_model(model, wav[None], device=device, split=True, overlap=0.25, progress=False)[0]
    except (RuntimeError, NotImplementedError):  # some ops are missing on MPS
        sources = apply_model(model, wav[None], device="cpu", split=True, overlap=0.25, progress=False)[0]
    drums = sources[model.sources.index("drums")] * std + mean
    return drums.T.numpy().astype(np.float32)


# MARK: - ADTOF


@functools.cache
def _adtof():
    from adtof_pytorch import calculate_n_bins, create_frame_rnn_model, get_default_weights_path, load_pytorch_weights

    model = create_frame_rnn_model(calculate_n_bins())
    model = load_pytorch_weights(model, get_default_weights_path(), strict=False)
    model.eval()
    return model


def adtof_activations(path: Path, chunk: int = 6000, overlap: int = 200) -> np.ndarray:
    """Frame-wise class activations (frames, 5) at 100 fps, columns in ADTOF_CLASSES order.

    Long songs run in overlapping chunks; each chunk keeps only its centre so the
    bidirectional GRUs always see context.
    """
    from adtof_pytorch import load_audio_for_model

    model = _adtof()
    # Accelerate's matmul raises spurious FP warnings on Apple Silicon; the output is finite.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        x = load_audio_for_model(str(path))  # (1, frames, bins, 1)
    n = x.shape[1]
    out = np.zeros((n, 5), dtype=np.float32)
    with torch.no_grad():
        start = 0
        while start < n:
            lo = max(0, start - overlap)
            hi = min(n, start + chunk + overlap)
            pred = model(x[:, lo:hi]).numpy()[0]
            keep_hi = min(n, start + chunk)
            out[start:keep_hi] = pred[start - lo : keep_hi - lo]
            start = keep_hi
    return out


def pick_onsets(activations: np.ndarray, thresholds: dict[str, float]) -> dict[str, list[tuple[float, float]]]:
    """ADTOF's peak picking per class → {class: [(time, confidence)]}.

    Confidence is the activation at the picked frame (0..1).
    """
    from adtof_pytorch.post_processing import NotePeakPickingProcessor

    result: dict[str, list[tuple[float, float]]] = {}
    for i, cls in enumerate(ADTOF_CLASSES):
        proc = NotePeakPickingProcessor(
            threshold=thresholds[cls], pre_avg=0.1, post_avg=0.01, pre_max=0.02, post_max=0.01, combine=0.02, fps=ADTOF_FPS
        )
        act = activations[:, i]
        onsets = []
        for t, _ in proc.process(act):
            f = int(round(t * ADTOF_FPS))
            onsets.append((float(t), float(act[max(0, f - 1) : f + 2].max())))
        result[cls] = onsets
    return result


def default_thresholds() -> dict[str, float]:
    from adtof_pytorch import FRAME_RNN_THRESHOLDS

    return dict(zip(ADTOF_CLASSES, map(float, FRAME_RNN_THRESHOLDS)))


# MARK: - Beat This!


@functools.cache
def _beat_this(device: str):
    from beat_this.inference import Audio2Frames

    return Audio2Frames(checkpoint_path="final0", device=device)


BEAT_FPS = 50


def track_beats(mix: np.ndarray, device: str) -> tuple[list[float], list[float]]:
    """(samples, 2) mix at 44.1 kHz → (beats, downbeats) in seconds.

    Beat This!'s minimal peak picking, then each peak refined below its 20 ms frame by
    a parabola through the neighbouring logits.
    """
    from beat_this.model.postprocessor import Postprocessor

    try:
        beat_logits, down_logits = _beat_this(device)(mix.mean(1), audio_io.SAMPLE_RATE)
    except (RuntimeError, NotImplementedError):
        beat_logits, down_logits = _beat_this("cpu")(mix.mean(1), audio_io.SAMPLE_RATE)
    beats, downs = Postprocessor(type="minimal", fps=BEAT_FPS)(beat_logits, down_logits)
    return (_refine(beats, beat_logits.float().cpu().numpy()),
            _refine(downs, down_logits.float().cpu().numpy()))


def _refine(times, logits: np.ndarray) -> list[float]:
    out = []
    for t in times:
        f = int(round(float(t) * BEAT_FPS))
        delta = 0.0
        if 0 < f < len(logits) - 1:
            a, b, c = logits[f - 1], logits[f], logits[f + 1]
            denom = a - 2 * b + c
            if denom < 0:
                delta = float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))
        out.append((f + delta) / BEAT_FPS)
    return out
