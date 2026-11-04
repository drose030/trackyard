"""Fit a chart tempo map (tempo changes + time signatures) to detected beats.

Beat This! beats sit on a 20 ms frame grid and occasionally miss or add a beat. The
fit cleans those up, then splits the song into the fewest segments of constant tempo
whose straight-line fit keeps the beats within a tolerance. A programmed track becomes
one tempo; a live drummer gets a tempo change wherever the drift exceeds the tolerance,
much like a hand-made chart.

Ticks: beat *i* (counted from the first downbeat) lands on tick ``lead_in + i * ppq``.
The lead-in before the first downbeat is a whole number of beats with its own tempo,
so tick 0 stays at audio time 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .chart import TempoMap

PPQ = 480


@dataclass
class TempoFit:
    tempo_map: TempoMap
    beat_ticks: list[int]  # tick of every fitted beat
    downbeat_ticks: list[int]
    factor: float  # metrical level applied to the detected beats (0.5, 1 or 2)
    bpm: float  # median tempo after scaling


def fit_tempo_map(
    beats: list[float],
    downbeats: list[float],
    scale: str = "auto",
    ppq: int = PPQ,
    tolerance: float = 0.012,
    anchors: list[float] | None = None,
    hats: list[float] | None = None,
) -> TempoFit:
    """`scale`: "auto", "half", "normal" or "double" relative to the detected beats.

    "auto" picks the level a charter would (see `auto_factor`); `hats` are the detected
    hi-hat onsets it looks at.

    `anchors`: confident drum onsets (kick/snare). Beat This! puts beats ~10 ms after
    the transients; the whole grid shifts by the median onset-to-beat offset.
    """
    b = clean_beats(np.asarray(beats, float))
    if len(b) < 4:
        raise ValueError("too few beats to fit a tempo map")
    period = float(np.median(np.diff(b)))
    factor = {"half": 0.5, "normal": 1.0, "double": 2.0}.get(scale)
    if factor is None:
        factor = auto_factor(b, hats or [])
    downs = np.asarray(downbeats, float)
    b = rescale(b, factor, downs)
    period = float(np.median(np.diff(b)))

    down_idx = _downbeat_indices(b, downs, period) or [0]
    # The map starts at the first downbeat at least half a beat into the song, so the
    # lead-in before it gets a sane tempo; an earlier first bar becomes part of the lead-in.
    first = next((i for i in down_idx if b[i] >= 0.5 * period + 0.02), down_idx[-1])
    beats_from = b[first:]
    bar_starts = [i - first for i in down_idx if i >= first]

    beats_from = snap_to_anchors(beats_from, anchors or [], period)
    bounds, times = segment(beats_from, tolerance)
    shift = transient_offset(np.asarray(times_at(bounds, times, len(beats_from))), anchors or [])
    times = [t + shift for t in times]
    lead_beats = max(1, int(round(times[0] / period)))
    lead_ticks = lead_beats * ppq

    tempos = [(0, 60.0 * lead_beats / times[0])]
    for (s0, t0), (s1, t1) in zip(zip(bounds, times), zip(bounds[1:], times[1:])):
        tempos.append((lead_ticks + s0 * ppq, 60.0 * (s1 - s0) / (t1 - t0)))
    tempos = _merge_equal(tempos)

    bar_lengths = _bar_lengths(bar_starts, len(beats_from))
    signatures = _signatures(bar_lengths, lead_beats, ppq)

    n = len(beats_from)
    beat_ticks = [lead_ticks + i * ppq for i in range(n)]
    downbeat_ticks, pos = [], 0
    for length in bar_lengths:
        downbeat_ticks.append(lead_ticks + pos * ppq)
        pos += length
    tm = TempoMap(ppq, tempos, signatures)
    return TempoFit(tm, beat_ticks, downbeat_ticks, factor, 60.0 / period)


def auto_factor(b: np.ndarray, hats: list[float]) -> float:
    """Metrical level a charter would pick, relative to the detected beats.

    Charters chart at the double-time pulse when the hi-hat runs in 16ths, so the
    16ths become 8ths on the highway, unless that would push the tempo past ~170 BPM.
    Very slow tempos are always doubled, very fast ones halved. On the 35 test charts
    this matches the charter's choice 32 times (docs/evaluation.md).
    """
    period = float(np.median(np.diff(b)))
    bpm = 60.0 / period
    if bpm < 70:
        return 2.0
    if bpm > 190:
        return 0.5
    if 2 * bpm <= 170 and sixteenth_hats(b, hats) >= 0.4 and hats_per_beat(b, hats) >= 1.5:
        return 2.0
    return 1.0


def sixteenth_hats(b: np.ndarray, hats: list[float]) -> float:
    """Share of hi-hat onsets on the "e" or "a" of a beat (the 2nd and 4th sixteenth)."""
    t = np.asarray([x for x in hats if b[0] <= x < b[-1]], float)
    if len(t) < 20:
        return 0.0
    i = (np.searchsorted(b, t, side="right") - 1).clip(0, len(b) - 2)
    slot = np.round((t - b[i]) / (b[i + 1] - b[i]) * 4) % 4
    return float(np.mean((slot == 1) | (slot == 3)))


def hats_per_beat(b: np.ndarray, hats: list[float]) -> float:
    """Hi-hat onsets per beat, over the beats that have any (intros and breaks don't count)."""
    t = np.asarray([x for x in hats if b[0] <= x < b[-1]], float)
    if len(t) == 0:
        return 0.0
    beats_with_hats = np.unique(np.searchsorted(b, t, side="right") - 1)
    return len(t) / len(beats_with_hats)


def clean_beats(b: np.ndarray) -> np.ndarray:
    """Fill missed beats (gap ≈ k periods) and drop doubled ones (gap < 0.4 period).

    The local period comes from the raw beats around each gap, so one bad region
    cannot spread through the song.
    """
    if len(b) < 3:
        return b
    diffs = np.diff(b)
    out = [b[0]]
    for i, t in enumerate(b[1:]):
        local = float(np.median(diffs[max(0, i - 8) : i + 9]))
        gap = t - out[-1]
        if gap < 0.4 * local:
            continue
        k = int(round(gap / local))
        if k >= 2 and abs(gap / k - local) < 0.2 * local:
            out += [out[-1] + gap * j / k for j in range(1, k)]
        out.append(t)
    return np.asarray(out)


def rescale(b: np.ndarray, factor: float, downbeats: np.ndarray) -> np.ndarray:
    if factor == 2.0:
        mids = (b[:-1] + b[1:]) / 2
        out = np.empty(len(b) + len(mids))
        out[0::2], out[1::2] = b, mids
        return out
    if factor == 0.5:
        # Keep the phase that holds more downbeats.
        period = float(np.median(np.diff(b)))
        hits = [sum(np.min(np.abs(b[k::2] - d)) < 0.25 * period for d in downbeats) for k in (0, 1)]
        return b[int(np.argmax(hits))::2]
    return b


def _downbeat_indices(b: np.ndarray, downs: np.ndarray, period: float) -> list[int]:
    idx = []
    for d in downs:
        i = int(np.argmin(np.abs(b - d)))
        if abs(b[i] - d) < 0.3 * period and (not idx or i > idx[-1]):
            idx.append(i)
    return idx


def segment(b: np.ndarray, tol: float) -> tuple[list[int], list[float]]:
    """Greedy constant-tempo segments → (boundary beat indices, fitted boundary times).

    A segment grows while a straight line through its beats keeps 90 % of them within
    `tol` and the newest two within 2 × `tol` (so a real tempo change ends it fast).
    Adjacent segments share their boundary beat.
    """
    n = len(b)
    idx = np.arange(n, dtype=float)
    bounds, times = [0], []
    s = 0
    while s < n - 1:
        e = s + 1
        fit = np.polyfit(idx[s : e + 1], b[s : e + 1], 1)
        while e + 1 < n:
            x, y = idx[s : e + 2], b[s : e + 2]
            cand = np.polyfit(x, y, 1)
            r = np.abs(np.polyval(cand, x) - y)
            if len(r) > 3 and (np.quantile(r, 0.9) > tol or r[-2:].max() > 2 * tol):
                break
            fit, e = cand, e + 1
        times.append(float(np.polyval(fit, s)))
        if e == n - 1:
            times.append(float(np.polyval(fit, e)))
        bounds.append(e)
        s = e
    if len(times) < len(bounds):  # single-beat tail
        times.append(float(b[-1]))
    return bounds, times


def snap_to_anchors(b: np.ndarray, anchors: list[float], period: float, window: float = 0.025) -> np.ndarray:
    """Move each beat onto the nearest anchor onset within `window` (beats stay in order)."""
    if not anchors:
        return b
    o = np.asarray(anchors, float)
    j = np.searchsorted(o, b).clip(1, len(o) - 1)
    nearest = np.where(np.abs(o[j] - b) < np.abs(o[j - 1] - b), o[j], o[j - 1])
    out = np.where(np.abs(nearest - b) <= min(window, 0.2 * period), nearest, b)
    return out if np.all(np.diff(out) > 0) else b


def times_at(bounds: list[int], times: list[float], n: int) -> list[float]:
    """Every beat's time, interpolated between the segment boundaries."""
    return list(np.interp(np.arange(n), bounds, times))


def transient_offset(grid: np.ndarray, onsets: list[float], window: float = 0.04) -> float:
    """Median (onset − nearest grid beat) over onsets within `window` of a beat."""
    if len(grid) < 2 or not onsets:
        return 0.0
    o = np.asarray(onsets, float)
    j = np.searchsorted(grid, o).clip(1, len(grid) - 1)
    nearest = np.where(np.abs(grid[j] - o) < np.abs(grid[j - 1] - o), grid[j], grid[j - 1])
    d = o - nearest
    d = d[np.abs(d) < window]
    return float(np.median(d)) if len(d) >= 8 else 0.0


def _merge_equal(tempos: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """Drop repeats of an identical tempo (any rounding would drift the grid)."""
    out = [tempos[0]]
    for t, bpm in tempos[1:]:
        if bpm != out[-1][1]:
            out.append((t, bpm))
    return out


def _bar_lengths(bar_starts: list[int], n_beats: int) -> list[int]:
    """Beats per bar from downbeat positions; implausible bars fall back to the usual length."""
    lengths = [b - a for a, b in zip(bar_starts, bar_starts[1:])]
    if not lengths:
        return [4] * (n_beats // 4 + 1)
    mode = int(np.bincount(lengths).argmax())
    fixed: list[int] = []
    i = 0
    while i < len(lengths):
        length = lengths[i]
        if length < mode and i + 1 < len(lengths) and length + lengths[i + 1] == mode:
            fixed.append(mode)  # spurious downbeat inside a bar
            i += 2
            continue
        if length > mode:
            q, r = divmod(length, mode)  # missed downbeats
            fixed += [mode] * q
            if r == 1:
                fixed[-1] += 1
            elif r:
                fixed.append(r)
        elif length < 2 and fixed:
            fixed[-1] += length
        else:
            fixed.append(length)
        i += 1
    covered = sum(fixed)
    tail = fixed[-1] if fixed else mode
    while covered < n_beats:
        fixed.append(tail)
        covered += tail
    return fixed


def _signatures(bar_lengths: list[int], lead_beats: int, ppq: int) -> list[tuple[int, int, int]]:
    sigs: list[tuple[int, int, int]] = []
    first = bar_lengths[0]
    pickup, full = lead_beats % first, lead_beats // first
    if pickup:
        sigs.append((0, pickup, 4))
    if full or not pickup:
        sigs.append((pickup * ppq, first, 4))
    tick = lead_beats * ppq
    for length in bar_lengths:
        if not sigs or sigs[-1][1] != length:
            sigs.append((tick, length, 4))
        tick += length * ppq
    return sigs
