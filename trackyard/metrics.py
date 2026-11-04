"""Onset and beat metrics against a reference chart."""

from __future__ import annotations

from dataclasses import dataclass

import mir_eval
import numpy as np


@dataclass
class OnsetScore:
    n_ref: int
    n_est: int
    tp: int
    offsets: list[float]  # est − ref of matched pairs, seconds

    @property
    def precision(self) -> float:
        return self.tp / self.n_est if self.n_est else float("nan")

    @property
    def recall(self) -> float:
        return self.tp / self.n_ref if self.n_ref else float("nan")

    @property
    def f(self) -> float:
        p, r = self.precision, self.recall
        if not self.n_ref and not self.n_est:
            return float("nan")
        return 2 * p * r / (p + r) if p + r > 0 else 0.0

    @property
    def median_offset_ms(self) -> float:
        return float(np.median(self.offsets) * 1000) if self.offsets else float("nan")

    def __add__(self, other: "OnsetScore") -> "OnsetScore":
        return OnsetScore(self.n_ref + other.n_ref, self.n_est + other.n_est, self.tp + other.tp, self.offsets + other.offsets)


EMPTY = OnsetScore(0, 0, 0, [])


def match_onsets(ref: list[float], est: list[float], window: float) -> OnsetScore:
    """One-to-one matching within ±window (mir_eval's maximum bipartite matching)."""
    r, e = np.sort(np.asarray(ref, float)), np.sort(np.asarray(est, float))
    if len(r) == 0 or len(e) == 0:
        return OnsetScore(len(r), len(e), 0, [])
    pairs = mir_eval.util.match_events(r, e, window)
    return OnsetScore(len(r), len(e), len(pairs), [float(e[j] - r[i]) for i, j in pairs])


def merge_close(times: list[float], tolerance: float = 0.02) -> list[float]:
    """Collapse onsets closer than `tolerance` (chords → one onset)."""
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] > tolerance:
            out.append(t)
    return out


def clip(times: list[float], lo: float, hi: float) -> list[float]:
    return [t for t in times if lo <= t <= hi]


@dataclass
class BeatScore:
    f70: float  # beat F-measure, ±70 ms (MIREX standard)
    f50: float
    cmlt: float  # correct metrical level, total
    amlt: float  # any metrical level (double/half tempo and off-beat allowed)
    downbeat_f70: float
    median_offset_ms: float
    ref_bpm: float
    est_bpm: float


def score_beats(ref: list[float], est: list[float], ref_down: list[float], est_down: list[float]) -> BeatScore:
    r, e = np.asarray(ref, float), np.asarray(est, float)
    rd, ed = np.asarray(ref_down, float), np.asarray(est_down, float)
    if len(r) < 2 or len(e) < 2:
        nan = float("nan")
        return BeatScore(0.0, 0.0, 0.0, 0.0, 0.0, nan, nan, nan)
    cmlc, cmlt, amlc, amlt = mir_eval.beat.continuity(r, e)
    m = match_onsets(ref, est, 0.07)
    return BeatScore(
        f70=mir_eval.beat.f_measure(r, e, 0.07),
        f50=mir_eval.beat.f_measure(r, e, 0.05),
        cmlt=cmlt,
        amlt=amlt,
        downbeat_f70=mir_eval.beat.f_measure(rd, ed, 0.07) if len(rd) and len(ed) else 0.0,
        median_offset_ms=m.median_offset_ms,
        ref_bpm=60.0 / float(np.median(np.diff(r))),
        est_bpm=60.0 / float(np.median(np.diff(e))),
    )
