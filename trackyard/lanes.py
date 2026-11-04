"""Which lane a detected cymbal or tom goes on, learned from hand-made charts.

ADTOF only says "cymbal" or "tom". A chart needs more:
- ride (blue) or crash (green) for a cymbal, maybe even a hi-hat (yellow),
- which of the three toms.

Hand rules got this wrong most of the time, so both decisions are made by small
models trained on Rock Band charts (`scripts/train_lanes.py`).

Features describe:
- the sound right after the hit: frequency bands, brightness, decay, pitch for toms,
- its place in the song: downbeat, kick or snare at the same time, how busy cymbals
  and hats are around it, where it sits in a tom fill.

The models are gradient-boosted trees, exported to JSON so the converter needs only
numpy at runtime.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .analysis import ADTOF_CLASSES, ADTOF_FPS

MODEL_PATH = Path(__file__).parent / "models" / "lanes.json"
BANDS = ((60, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000))
CYMBAL_LANES = ("yellow-cym", "blue-cym", "green-cym")
TOM_LANES = ("yellow-tom", "blue-tom", "green-tom")


@dataclass
class Context:
    mono: np.ndarray  # Demucs drum stem, mono
    sr: int
    acts: np.ndarray  # ADTOF activations (frames, 5)
    onsets: dict[str, np.ndarray]  # class → sorted onset times
    beats: np.ndarray
    downbeats: np.ndarray


def _segment(ctx: Context, t: float, a: float, b: float) -> np.ndarray:
    i, j = int((t + a) * ctx.sr), int((t + b) * ctx.sr)
    return ctx.mono[max(0, i) : max(0, j)]


def _spectrum(seg: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    if len(seg) < 64:
        return np.zeros(1), np.zeros(1)
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=max(4096, len(seg)))) ** 2
    return spec, np.fft.rfftfreq(max(4096, len(seg)), 1 / sr)


def _bands(spec: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    e = np.array([spec[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in BANDS]) + 1e-12
    return e


def _count(times: np.ndarray, t: float, radius: float) -> int:
    return int(np.searchsorted(times, t + radius) - np.searchsorted(times, t - radius))


def _near(times: np.ndarray, t: float, radius: float = 0.03) -> float:
    return float(_count(times, t, radius) > 0)


def _gap(times: np.ndarray, t: float, before: bool, cap: float = 4.0) -> float:
    i = np.searchsorted(times, t - 1e-4) if before else np.searchsorted(times, t + 1e-4)
    if before:
        return min(cap, t - times[i - 1]) if i > 0 else cap
    return min(cap, times[i] - t) if i < len(times) else cap


def _acts(ctx: Context, t: float) -> np.ndarray:
    f = int(round(t * ADTOF_FPS))
    return ctx.acts[max(0, f - 2) : f + 3].max(axis=0) if len(ctx.acts) else np.zeros(len(ADTOF_CLASSES))


def _metric(ctx: Context, t: float) -> list[float]:
    down = float(len(ctx.downbeats) and np.min(np.abs(ctx.downbeats - t)) < 0.04)
    if len(ctx.beats) < 2:
        return [down, 0.0, 0.0]
    i = int(np.clip(np.searchsorted(ctx.beats, t) - 1, 0, len(ctx.beats) - 2))
    phase = (t - ctx.beats[i]) / (ctx.beats[i + 1] - ctx.beats[i])
    dist = min(phase % 1, 1 - phase % 1)  # 0 on the beat, 0.5 on the "and"
    return [down, float(dist < 0.08), dist]


def cymbal_features(ctx: Context, times: list[float]) -> np.ndarray:
    rows = []
    cy, hh = ctx.onsets["CY"], ctx.onsets["HH"]
    for t in times:
        spec, freqs = _spectrum(_segment(ctx, t, 0, 0.06), ctx.sr)
        e = _bands(spec, freqs)
        centroid = np.log((spec * freqs).sum() / (spec.sum() + 1e-12) + 1)
        late_spec, late_freqs = _spectrum(_segment(ctx, t, 0.15, 0.4), ctx.sr)
        late = _bands(late_spec, late_freqs) if len(late_freqs) > 1 else e * 0
        decay = np.log((late[5:].sum() + 1e-12) / e[5:].sum())
        rows.append([
            *(np.log(e / e.sum())), centroid, decay, np.log(e.sum()),
            *_acts(ctx, t), *_metric(ctx, t),
            _count(cy, t, 0.5), _count(cy, t, 1.5), _count(hh, t, 1.0),
            _near(ctx.onsets["KD"], t), _near(ctx.onsets["SD"], t),
            _gap(cy, t, True), _gap(cy, t, False),
        ])
    x = np.asarray(rows, float)
    if len(x):  # loudness and brightness relative to this song's cymbals
        x = np.column_stack([x, x[:, 9] - np.median(x[:, 9]), x[:, 7] - np.median(x[:, 7])])
    return x


def tom_pitch(ctx: Context, t: float, start: float = 0.0, end: float = 0.08, hi: float = 500) -> float:
    spec, freqs = _spectrum(_segment(ctx, t, start, end), ctx.sr)
    band = (freqs >= 60) & (freqs <= hi)
    return float(freqs[band][np.argmax(spec[band])]) if band.any() and spec[band].any() else 100.0


def tom_features(ctx: Context, times: list[float]) -> np.ndarray:
    times = list(times)
    if not times:
        return np.zeros((0, 0))
    pitch = np.log([tom_pitch(ctx, t) for t in times])
    # The ring after the attack, where kick and bass bleed in less.
    ring = np.log([tom_pitch(ctx, t, 0.02, 0.15, 300) for t in times])
    order = np.argsort(np.argsort(pitch))
    pct = order / max(1, len(times) - 1)
    tarr = np.asarray(times)
    local = np.array([np.mean(ring[np.abs(tarr - t) <= 8] < ring[i]) for i, t in enumerate(times)])
    # Fills: toms chained with gaps under 0.35 s.
    runs, cur = [], [0]
    for i in range(1, len(times)):
        if times[i] - times[i - 1] < 0.35:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    run_of = {}
    for r in runs:
        ranks = np.argsort(np.argsort(-pitch[r]))  # 0 = highest in the fill
        slope = np.polyfit(range(len(r)), pitch[r], 1)[0] if len(r) > 2 else 0.0
        for k, i in enumerate(r):
            prev = ring[i] - ring[r[k - 1]] if k > 0 else 0.0
            nxt = ring[r[k + 1]] - ring[i] if k + 1 < len(r) else 0.0
            run_of[i] = (len(r), k / max(1, len(r) - 1), ranks[k] / max(1, len(r) - 1), slope, prev, nxt)
    rows = []
    for i, t in enumerate(times):
        spec, freqs = _spectrum(_segment(ctx, t, 0, 0.08), ctx.sr)
        e = _bands(spec, freqs)
        centroid = np.log((spec * freqs).sum() / (spec.sum() + 1e-12) + 1)
        rows.append([
            pitch[i], pct[i], pitch[i] - np.median(pitch), ring[i], ring[i] - np.median(ring), local[i],
            *(np.log(e / e.sum())), centroid,
            *_acts(ctx, t), *run_of[i],
            _near(ctx.onsets["KD"], t), _near(ctx.onsets["SD"], t), _near(ctx.onsets["CY"], t),
            *_metric(ctx, t),
        ])
    return np.asarray(rows, float)


# MARK: - Runtime model (exported trees, numpy only)


class TreeEnsemble:
    """Gradient-boosted trees exported from scikit-learn's GradientBoostingClassifier."""

    def __init__(self, spec: dict):
        self.classes = spec["classes"]
        self.init = np.asarray(spec["init"], float)
        self.rate = spec["learning_rate"]
        self.trees = [[{k: np.asarray(v) for k, v in tree.items()} for tree in stage] for stage in spec["trees"]]

    def _tree(self, tree: dict, x: np.ndarray) -> np.ndarray:
        node = np.zeros(len(x), int)
        while True:
            left = tree["left"][node]
            leaf = left < 0
            if leaf.all():
                return tree["value"][node]
            f, th = tree["feature"][node], tree["threshold"][node]
            go_left = x[np.arange(len(x)), np.where(leaf, 0, f)] <= th
            node = np.where(leaf, node, np.where(go_left, left, tree["right"][node]))

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        raw = np.tile(self.init, (len(x), 1))
        for stage in self.trees:
            for k, tree in enumerate(stage):
                raw[:, k] += self.rate * self._tree(tree, x)
        if raw.shape[1] == 1:  # binary: one log-odds column
            p = 1 / (1 + np.exp(-raw[:, 0]))
            return np.column_stack([1 - p, p])
        e = np.exp(raw - raw.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)

    def predict(self, x: np.ndarray) -> list[str]:
        return [self.classes[i] for i in self.predict_proba(x).argmax(axis=1)] if len(x) else []


def load_models(path: Path = MODEL_PATH) -> dict[str, TreeEnsemble] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return {name: TreeEnsemble(spec) for name, spec in data["models"].items()}


@functools.cache
def models() -> dict[str, TreeEnsemble] | None:
    """The shipped models, loaded once."""
    return load_models()


def export_gbc(model) -> dict:
    """scikit-learn GradientBoostingClassifier → JSON-able dict for `TreeEnsemble`."""
    init = model._raw_predict_init(np.zeros((1, model.n_features_in_)))[0]
    stages = []
    for stage in model.estimators_:
        trees = []
        for est in stage:
            t = est.tree_
            trees.append({"left": t.children_left.tolist(), "right": t.children_right.tolist(),
                          "feature": t.feature.tolist(), "threshold": t.threshold.tolist(),
                          "value": t.value[:, 0, 0].tolist()})
        stages.append(trees)
    return {"classes": [str(c) for c in model.classes_], "init": init.tolist(),
            "learning_rate": model.learning_rate, "trees": stages}
