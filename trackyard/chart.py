"""Read hand-made Clone Hero drum charts (notes.mid / notes.chart) as reference data.

Every Expert drum note becomes a `DrumHit` in one of the five ADTOF classes, with its
time on the *audio* timeline (chart delay applied). The chart's tempo map and time
signatures yield reference beats and downbeats.

Lane → class rules (Clone Hero / Rock Band conventions):

* kick (incl. 2x kick) → KD, red → SD
* 4-lane pro drums: yellow/blue/green are cymbals unless a tom marker covers them
  (MIDI 110/111/112) or, in .chart, toms unless a cymbal marker sits on the same
  tick (66/67/68). Yellow cymbal → HH, blue/green cymbal → CY, any tom → TT.
* 5-lane: yellow → HH, blue → TT, orange → CY, green → TT.
* Non-pro .chart (song.ini pro_drums off): yellow is taken as hi-hat; blue/green
  still honour cymbal markers where the charter set them, otherwise tom.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import mido

CLASSES = ("KD", "SD", "HH", "TT", "CY")
DIFFICULTIES = ("expert", "hard", "medium", "easy")
MIDI_BASE = {"expert": 96, "hard": 84, "medium": 72, "easy": 60}


@dataclass(frozen=True)
class DrumHit:
    time: float  # seconds on the audio timeline
    cls: str  # one of CLASSES
    lane: str  # chart lane, e.g. "yellow-cym"


@dataclass
class TempoMap:
    ppq: int
    tempos: list[tuple[int, float]]  # (tick, bpm), sorted, first at tick 0
    signatures: list[tuple[int, int, int]]  # (tick, numerator, denominator)
    _starts: list[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.tempos or self.tempos[0][0] != 0:
            self.tempos.insert(0, (0, self.tempos[0][1] if self.tempos else 120.0))
        if not self.signatures or self.signatures[0][0] != 0:
            self.signatures.insert(0, (0, 4, 4))
        self._starts = [0.0]
        for (t0, bpm0), (t1, _) in zip(self.tempos, self.tempos[1:]):
            self._starts.append(self._starts[-1] + (t1 - t0) / self.ppq * 60.0 / bpm0)

    def seconds(self, tick: float) -> float:
        i = max(0, bisect.bisect_right([t for t, _ in self.tempos], tick) - 1)
        t0, bpm = self.tempos[i]
        return self._starts[i] + (tick - t0) / self.ppq * 60.0 / bpm

    def tick_at(self, seconds: float) -> float:
        """Inverse of `seconds`: fractional tick at a chart time."""
        i = max(0, bisect.bisect_right(self._starts, seconds) - 1)
        t0, bpm = self.tempos[i]
        return t0 + (seconds - self._starts[i]) * bpm / 60.0 * self.ppq

    def beats(self, end_tick: int) -> tuple[list[int], list[bool]]:
        """Beat ticks up to `end_tick` and whether each is a downbeat.

        One beat = one note of the signature's denominator (quarter in x/4).
        """
        ticks: list[int] = []
        downs: list[bool] = []
        sigs = self.signatures + [(end_tick + 1, 0, 0)]
        for (start, num, den), (nxt, _, _) in zip(sigs, sigs[1:]):
            step = self.ppq * 4 / den
            k = 0
            while start + k * step < min(nxt, end_tick + 1):
                ticks.append(round(start + k * step))
                downs.append(k % num == 0)
                k += 1
        return ticks, downs


@dataclass
class ReferenceChart:
    hits: list[DrumHit]
    tempo_map: TempoMap
    offset: float  # seconds added to chart time to get audio time
    pro: bool
    five_lane: bool
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)


@dataclass
class ChartNotes:
    """A chart's notes exactly as written (no merging), for editing."""

    notes: list[tuple[int, str]]  # (tick, lane), sorted
    tempo_map: TempoMap
    offset: float  # seconds added to chart time to get audio time
    pro: bool
    five_lane: bool


def read_notes(song_dir: Path, ini: dict[str, str], difficulty: str = "expert") -> ChartNotes:
    delay = _float(ini.get("delay")) / 1000.0
    pro_flag = ini.get("pro_drums", "").strip().lower() in ("true", "1")
    if (song_dir / "notes.mid").exists():
        notes, tempo_map, five_lane, pro, chart_offset = _read_midi(song_dir / "notes.mid", pro_flag, difficulty)
    elif (song_dir / "notes.chart").exists():
        notes, tempo_map, five_lane, pro, chart_offset = _read_chart(song_dir / "notes.chart", pro_flag, difficulty)
    else:
        raise FileNotFoundError(f"no notes.mid or notes.chart in {song_dir}")
    # song.ini `delay` and .chart `Offset` describe the same shift; charts often carry
    # both with the same value. The ini wins, the chart's Offset is the fallback.
    offset = delay if delay else chart_offset
    return ChartNotes(sorted(set(notes)), tempo_map, offset, pro, five_lane)


def load_chart(song_dir: Path, ini: dict[str, str], difficulty: str = "expert") -> ReferenceChart:
    c = read_notes(song_dir, ini, difficulty)
    notes, tempo_map, offset = c.notes, c.tempo_map, c.offset
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


def _float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0


# MARK: - MIDI


_MIDI_LANES = {-1: "kick", 0: "kick", 1: "red", 2: "yellow", 3: "blue", 4: "green"}  # offset from the base note
_MIDI_TOM_MARKER = {110: "yellow", 111: "blue", 112: "green"}


def _read_midi(path: Path, pro_flag: bool, difficulty: str = "expert"):
    mid = mido.MidiFile(path, clip=True)
    tempos: list[tuple[int, float]] = []
    signatures: list[tuple[int, int, int]] = []
    drum_track = None
    for track in mid.tracks:
        tick = 0
        name = None
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempos.append((tick, mido.tempo2bpm(msg.tempo)))
            elif msg.type == "time_signature":
                signatures.append((tick, msg.numerator, msg.denominator))
            elif msg.type == "track_name" and name is None:
                name = msg.name
        if name == "PART DRUMS":
            drum_track = track
    if drum_track is None:
        raise ValueError(f"{path}: no PART DRUMS track")
    tempo_map = TempoMap(mid.ticks_per_beat, sorted(tempos), sorted(set(signatures)))

    base = MIDI_BASE[difficulty]
    raw: list[tuple[int, int]] = []  # (tick, note − base)
    open_markers: dict[int, int] = {}
    tom_ranges: dict[str, list[tuple[int, int]]] = {c: [] for c in _MIDI_TOM_MARKER.values()}
    tick = 0
    for msg in drum_track:
        tick += msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        on = msg.type == "note_on" and msg.velocity > 0
        if msg.note in _MIDI_TOM_MARKER:
            if on:
                open_markers[msg.note] = tick
            elif msg.note in open_markers:
                tom_ranges[_MIDI_TOM_MARKER[msg.note]].append((open_markers.pop(msg.note), tick))
        elif on and base - (difficulty == "expert") <= msg.note <= base + 5:  # 95 = Expert 2x kick
            raw.append((tick, msg.note - base))

    five_lane = any(n == 5 for _, n in raw)
    notes: list[tuple[int, str]] = []
    for tick, note in raw:
        if five_lane:
            lane = {-1: "kick", 0: "kick", 1: "red", 2: "yellow-cym", 3: "blue-tom",
                    4: "orange-cym", 5: "green-tom"}[note]
        else:
            lane = _MIDI_LANES[note]
            if lane in ("yellow", "blue", "green"):
                is_tom = any(a <= tick < b for a, b in tom_ranges[lane])
                lane += "-tom" if is_tom else "-cym"
        notes.append((tick, lane))
    # MIDI drums are pro by construction (cymbal unless tom-marked).
    return notes, tempo_map, five_lane, True, 0.0


# MARK: - .chart


def _read_chart(path: Path, pro_flag: bool, difficulty: str = "expert"):
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    section = None
    resolution = 192
    chart_offset = 0.0
    tempos: list[tuple[int, float]] = []
    signatures: list[tuple[int, int, int]] = []
    drum_events: list[tuple[int, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        parts = value.split()
        if section == "Song":
            if key == "Resolution":
                resolution = int(float(value))
            elif key == "Offset":
                chart_offset = _float(value)
        elif section == "SyncTrack" and key.isdigit():
            if parts[0] == "B":
                tempos.append((int(key), int(parts[1]) / 1000.0))
            elif parts[0] == "TS":
                den = 2 ** int(parts[2]) if len(parts) > 2 else 4
                signatures.append((int(key), int(parts[1]), den))
        elif section == f"{difficulty.title()}Drums" and key.isdigit() and parts[0] == "N":
            drum_events.append((int(key), int(parts[1])))
    tempo_map = TempoMap(resolution, sorted(tempos), sorted(set(signatures)))

    cym_markers = {(t, n - 64) for t, n in drum_events if n in (66, 67, 68)}  # → 2/3/4
    has_markers = bool(cym_markers)
    five_lane = any(n == 5 for _, n in drum_events)
    pro = (pro_flag or has_markers) and not five_lane
    notes: list[tuple[int, str]] = []
    for tick, n in drum_events:
        if n in (0, 32):
            lane = "kick"
        elif n == 1:
            lane = "red"
        elif five_lane and n in (2, 3, 4, 5):
            lane = {2: "yellow-cym", 3: "blue-tom", 4: "orange-cym", 5: "green-tom"}[n]
        elif n in (2, 3, 4):
            color = {2: "yellow", 3: "blue", 4: "green"}[n]
            if (tick, n) in cym_markers or (n == 2 and not pro_flag):
                lane = color + "-cym"
            else:
                lane = color + "-tom"
        else:
            continue  # accents, ghosts, markers, star power
        notes.append((tick, lane))
    return notes, tempo_map, five_lane, pro, chart_offset
