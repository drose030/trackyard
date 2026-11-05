"""Hand-made Clone Hero drum charts as reference data for the evaluation.

Every drum note of a difficulty becomes a `DrumHit` in one of the five ADTOF classes,
with its time on the *audio* timeline (offset applied). The chart's tempo map and time
signatures yield reference beats and downbeats. How lanes are read: `chartfile`.

Lane → class: kick → KD, red → SD, yellow cymbal → HH, blue/green/orange cymbal → CY,
any tom → TT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import chartfile
from .tempomap import TempoMap

CLASSES = ("KD", "SD", "HH", "TT", "CY")


@dataclass(frozen=True)
class DrumHit:
    time: float  # seconds on the audio timeline
    cls: str  # one of CLASSES
    lane: str  # chart lane, e.g. "yellow-cym"


@dataclass
class ReferenceChart:
    hits: list[DrumHit]
    tempo_map: TempoMap
    offset: float  # seconds added to chart time to get audio time
    pro: bool
    five_lane: bool
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)


def load_chart(song_dir: Path, ini: dict[str, str], difficulty: str = "expert") -> ReferenceChart:
    c = chartfile.read(song_dir, ini)
    notes, tempo_map, offset = c.levels[difficulty], c.tempo_map, c.offset
    pro, five_lane = c.pro, c.five_lane

    seen: set[tuple[int, str]] = set()
    hits: list[DrumHit] = []
    for tick, lane in sorted(notes):
        cls = _LANE_CLASS[lane]
        if (tick, cls) in seen:  # e.g. blue + green cymbal on one tick → one CY onset
            continue
        seen.add((tick, cls))
        hits.append(DrumHit(tempo_map.seconds(tick) + offset, cls, lane))

    last_tick = max((t for t, _ in notes), default=0) + 4 * tempo_map.ppq
    beat_ticks, downs = tempo_map.beats(last_tick)
    beats = [tempo_map.seconds(t) + offset for t in beat_ticks]
    downbeats = [b for b, d in zip(beats, downs) if d]
    return ReferenceChart(hits, tempo_map, offset, pro, five_lane, beats, downbeats)


_LANE_CLASS = {
    "kick": "KD",
    "red": "SD",
    "yellow-cym": "HH",
    "yellow-tom": "TT",
    "blue-cym": "CY",
    "blue-tom": "TT",
    "green-cym": "CY",
    "green-tom": "TT",
    "orange-cym": "CY",
}
