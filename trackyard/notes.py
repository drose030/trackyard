"""Turn transcribed drum onsets into chart notes: snap to the tempo grid, pick lanes.

Classes → lanes (4-lane pro drums, the Rock Band / Clone Hero layout). CY and TT lanes
come from the trained models in `lanes` when available; these are the fallback rules:

* KD → kick, SD → red, HH → yellow cymbal
* CY → blue cymbal (ride) when another cymbal hit is within a beat, else green (crash)
* TT → yellow / blue / green tom by pitch: the song's toms are split into high, mid and
  low thirds by the dominant frequency of the drum stem right after each hit

Then per tick: one note per lane (a tom moves to a free tom lane when a cymbal has its
lane), and at most two hand notes plus kick; extra notes with the lowest confidence go.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .tempomap import TempoMap

LANES = ("kick", "red", "yellow-cym", "yellow-tom", "blue-cym", "blue-tom", "green-cym", "green-tom")
TOM_LANES = ("yellow-tom", "blue-tom", "green-tom")


@dataclass
class ChartNote:
    tick: int
    lane: str
    confidence: float
    time: float  # detected onset time (before snapping), seconds
    cls: str


def snap_ticks(times: list[float], tempo_map: TempoMap, triplets: bool = True, finest: int = 32,
               tolerance: float = 0.02) -> list[int]:
    """Snap onset times to the coarsest grid that fits, chosen per beat.

    Grids from coarse to fine: 1/16, 1/24 (sixteenth triplets, includes 1/12), 1/32.
    A beat takes the first grid that puts every onset in it within `tolerance`
    seconds; failing that, the one that fits the most onsets (coarser wins ties).
    So a beat is straight or triplet, never a mix.
    """
    ppq = tempo_map.ppq
    frac = np.array([tempo_map.tick_at(t) for t in times])
    if len(frac) == 0:
        return []
    steps = [ppq / 4] + ([ppq / 6] if triplets else []) + ([ppq / 8] if finest >= 32 else [])
    snapped = [np.round(frac / s) * s for s in steps]
    tol_ticks = np.array([tempo_map.tick_at(t + tolerance) for t in times]) - frac
    fits = [np.abs(frac - s) <= tol_ticks for s in snapped]
    beat = np.floor(frac / ppq + 1 / 16).astype(int)  # onsets just before a beat belong to it
    out = snapped[0].copy()
    for b in np.unique(beat):
        m = beat == b
        counts = [int(f[m].sum()) for f in fits]
        choice = next((g for g, c in enumerate(counts) if c == m.sum()), int(np.argmax(counts)))
        out[m] = snapped[choice][m]
    return [int(round(x)) for x in out]


def tom_pitch(drums: np.ndarray, sr: int, t: float) -> float:
    """Dominant frequency (60–500 Hz) in the 80 ms after a hit, in Hz."""
    mono = drums.mean(1) if drums.ndim == 2 else drums
    a = int(t * sr)
    seg = mono[a : a + int(0.08 * sr)]
    if len(seg) < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=8192))
    freqs = np.fft.rfftfreq(8192, 1 / sr)
    band = (freqs >= 60) & (freqs <= 500)
    return float(freqs[band][np.argmax(spec[band])])


def build_notes(
    onsets: dict[str, list[tuple[float, float]]],
    tempo_map: TempoMap,
    drums: np.ndarray | None,
    sr: int,
    triplets: bool = True,
    finest: int = 32,
    lane_ctx=None,
) -> list[ChartNote]:
    """{class: [(time, confidence)]} → chart notes on lanes, sorted by tick.

    With `lane_ctx` (a `trackyard.lanes.Context`) and trained models, cymbal and tom
    lanes come from the models; otherwise from the simple rules below.
    """
    raw: list[ChartNote] = []
    for cls, items in onsets.items():
        if not items:
            continue
        ticks = snap_ticks([t for t, _ in items], tempo_map, triplets, finest)
        best: dict[int, tuple[float, float]] = {}
        for tick, (t, c) in zip(ticks, items):
            if tick >= 0 and (tick not in best or c > best[tick][1]):
                best[tick] = (t, c)
        raw += [ChartNote(tick, "", c, t, cls) for tick, (t, c) in best.items()]
    raw.sort(key=lambda n: (n.tick, n.cls))

    ppq = tempo_map.ppq
    cym_ticks = np.array(sorted(n.tick for n in raw if n.cls == "CY"))
    toms = [n for n in raw if n.cls == "TT"]
    tom_lane = _tom_lanes(toms, drums, sr)
    learned = _learned_lanes(lane_ctx) if lane_ctx is not None else {}

    for n in raw:
        if (n.cls, n.time) in learned:
            n.lane = learned[(n.cls, n.time)]
            continue
        if n.cls == "KD":
            n.lane = "kick"
        elif n.cls == "SD":
            n.lane = "red"
        elif n.cls == "HH":
            n.lane = "yellow-cym"
        elif n.cls == "CY":
            near = cym_ticks[(np.abs(cym_ticks - n.tick) <= ppq) & (cym_ticks != n.tick)]
            n.lane = "blue-cym" if len(near) else "green-cym"
        else:
            n.lane = tom_lane[id(n)]
    return _resolve(raw)


def _learned_lanes(ctx) -> dict[tuple[str, float], str]:
    """(class, onset time) → lane from the trained models, for every CY and TT onset."""
    from .lanes import cymbal_features, models, tom_features

    m = models()
    if m is None:
        return {}
    out: dict[tuple[str, float], str] = {}
    for cls, name, features in (("CY", "cymbal", cymbal_features), ("TT", "tom", tom_features)):
        times = list(ctx.onsets[cls])
        if times:
            out.update({(cls, float(t)): lane for t, lane in zip(times, m[name].predict(features(ctx, times)))})
    return out


def _tom_lanes(toms: list[ChartNote], drums: np.ndarray | None, sr: int) -> dict[int, str]:
    if len(toms) < 6 or drums is None:
        return {id(n): "blue-tom" for n in toms}
    pitch = np.array([tom_pitch(drums, sr, n.time) for n in toms])
    lo, hi = np.quantile(pitch, [1 / 3, 2 / 3])
    if hi - lo < 10:  # one tom sound throughout
        return {id(n): "blue-tom" for n in toms}
    return {id(n): ("yellow-tom" if p > hi else "green-tom" if p < lo else "blue-tom") for n, p in zip(toms, pitch)}


def _resolve(notes: list[ChartNote]) -> list[ChartNote]:
    out: list[ChartNote] = []
    i = 0
    while i < len(notes):
        j = i
        while j < len(notes) and notes[j].tick == notes[i].tick:
            j += 1
        group = notes[i:j]
        taken: dict[str, ChartNote] = {}
        # Cymbals and drums with fixed lanes first, then toms into what is left.
        for n in sorted(group, key=lambda n: (n.lane in TOM_LANES, -n.confidence)):
            color = n.lane.split("-")[0]
            if n.lane in TOM_LANES:
                prefs = [n.lane] + [lane for lane in TOM_LANES if lane != n.lane]
                lane = next((lane for lane in prefs if lane.split("-")[0] not in taken), None)
                if lane is None:
                    continue
                n.lane, color = lane, lane.split("-")[0]
            if color in taken:
                continue
            taken[color] = n
        hands = sorted((n for c, n in taken.items() if c != "kick"), key=lambda n: -n.confidence)
        out += ([taken["kick"]] if "kick" in taken else []) + hands[:2]
        i = j
    out.sort(key=lambda n: (n.tick, LANES.index(n.lane)))
    return out
