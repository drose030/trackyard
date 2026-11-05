"""Derive Hard, Medium and Easy from an Expert chart by dropping notes.

The rules follow what hand-made Rock Band / Clone Hero charts do (see
docs/evaluation.md): the snare backbeat survives every level; hi-hats and kicks thin
out first; fills slow down; Easy is a quarter-note groove with one hand at a time.

Per level:

* allowed grid: where each lane group may still have notes
* minimum spacing per group (in beats): kick, each cymbal lane, and the drums
  (snare + toms together, since fills move between them)
* at most N hand notes per tick, and at most M notes including kick
* optionally a minimum gap in seconds, so double-tempo charts don't stay too dense

Notes are only ever dropped, never moved or recoloured, so the pro-drums tom markers
written for Expert stay valid for every level.

Two ways to pick the notes, chosen per level by its settings (`preset`):

* "clone-hero": the fixed rules above (`LEVELS`), tuned to match hand-made charts.
* anything else: adjustable controls (`reduce_with`): a density with a weight per
  instrument, the finest grid, how many notes at once and the shortest gap. The "mpc"
  preset fills them in for even steps on the MPC pads; edited values become "custom".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .tempomap import TempoMap
from .notes import ChartNote

CYMBALS = ("yellow-cym", "blue-cym", "green-cym")
DRUMS = ("red", "yellow-tom", "blue-tom", "green-tom")


@dataclass(frozen=True)
class Level:
    grid: dict[str, float]  # group → allowed grid in beats
    spacing: dict[str, float]  # group → minimum gap in beats
    max_hands: int
    max_notes: int  # including kick
    # group → minimum gap in seconds, on top of `spacing`: keeps a level playable when
    # the chart runs at double tempo, where a half-beat gap is only a sixteenth.
    min_seconds: dict[str, float] = field(default_factory=dict)


LEVELS = {
    "hard": Level(
        grid={"kick": 1 / 2, "cym": 1 / 2, "drums": 1 / 4},
        spacing={"kick": 1 / 2, "cym": 1 / 2, "drums": 1 / 2},
        max_hands=2, max_notes=3,
    ),
    "medium": Level(
        grid={"kick": 1 / 2, "cym": 1 / 2, "drums": 1 / 2},
        spacing={"kick": 1, "cym": 1 / 2, "drums": 1},
        max_hands=2, max_notes=3,
        min_seconds={"cym": 0.25},  # hats no faster than eighths at 120 BPM
    ),
    "easy": Level(
        grid={"kick": 1 / 2, "cym": 1 / 2, "drums": 1 / 2},
        spacing={"kick": 4, "cym": 1, "drums": 1},
        max_hands=1, max_notes=2,
    ),
}
# Values chosen by grid searches against the hand-made lower difficulties of both test
# sets (scripts/evaluate_difficulty.py); Medium was cross-checked between the sets.

# Which note survives a crowded tick: snare/toms, then kick, then cymbals.
KEEP_ORDER = {"red": 0, "yellow-tom": 1, "blue-tom": 1, "green-tom": 1, "kick": 2,
              "green-cym": 3, "blue-cym": 4, "yellow-cym": 5}


def group(lane: str) -> str:
    return "kick" if lane == "kick" else "cym" if lane in CYMBALS else "drums"


def strength(tick: int, ppq: int, downbeats: set[int], triplets: bool = False) -> int:
    """0 = downbeat, 1 = beat, 2 = eighth, 3 = sixteenth (or eighth triplet), 4 = finer."""
    if tick in downbeats:
        return 0
    pos = tick % ppq
    if pos == 0:
        return 1
    if pos * 2 == ppq:
        return 2
    return 3 if (pos * 4) % ppq == 0 or (triplets and (pos * 3) % ppq == 0) else 4


def limit_chords(notes: list[ChartNote], max_hands: int, max_notes: int) -> list[ChartNote]:
    """Per tick at most `max_hands` hand notes and `max_notes` in all, by KEEP_ORDER."""
    by_tick: dict[int, list[ChartNote]] = {}
    for n in notes:
        by_tick.setdefault(n.tick, []).append(n)
    out: list[ChartNote] = []
    for tick in sorted(by_tick):
        chord = sorted(by_tick[tick], key=lambda n: KEEP_ORDER[n.lane])
        hands = [n for n in chord if n.lane != "kick"][:max_hands]
        kick = [n for n in chord if n.lane == "kick"]
        out += sorted(hands + kick, key=lambda n: KEEP_ORDER[n.lane])[:max_notes]
    return out


def reduce(notes: list[ChartNote], level: str, ppq: int, downbeats: set[int] | None = None) -> list[ChartNote]:
    rules = LEVELS[level]
    downbeats = downbeats or set()

    def on_grid(n: ChartNote) -> bool:
        step = rules.grid[group(n.lane)] * ppq
        return abs(n.tick / step - round(n.tick / step)) < 1e-6

    candidates = [n for n in notes if on_grid(n)]

    # Thin each group (each cymbal lane on its own) to its minimum spacing, keeping
    # metrically strong notes first, then confident ones.
    kept: list[ChartNote] = []
    taken: dict[str, list[ChartNote]] = {}
    for n in sorted(candidates, key=lambda n: (strength(n.tick, ppq, downbeats), -n.confidence, n.tick)):
        key = n.lane if n.lane in CYMBALS else group(n.lane)
        gap = rules.spacing[group(n.lane)] * ppq
        gap_s = rules.min_seconds.get(group(n.lane), 0.0)
        if all(abs(n.tick - t.tick) >= gap and abs(n.time - t.time) >= gap_s for t in taken.get(key, [])):
            taken.setdefault(key, []).append(n)
            kept.append(n)
    return sorted(limit_chords(kept, rules.max_hands, rules.max_notes), key=lambda n: (n.tick, n.lane))


def all_levels(expert: list[ChartNote], ppq: int, downbeats: set[int] | None = None,
               settings: dict[str, dict] | None = None) -> dict[str, list[ChartNote]]:
    """Expert plus every lower level; `settings` per level (default: the default preset)."""
    settings = settings or preset_settings(DEFAULT_PRESET)
    return {"expert": expert, **{lvl: reduce_level(expert, lvl, ppq, downbeats, settings.get(lvl))
                                 for lvl in LEVELS}}


def lower_levels(expert: list[tuple[int, str]], tempo_map: TempoMap, settings: dict[str, dict] | None = None,
                 confidence: dict[tuple[int, str], float] | None = None) -> dict[str, list[tuple[int, str]]]:
    """Hard, Medium and Easy for Expert notes of a chart, timed and barred by `tempo_map`
    (the one being edited, not necessarily the saved one)."""
    confidence = confidence or {}
    notes = [ChartNote(t, lane, confidence.get((t, lane), 1.0), tempo_map.seconds(t), "") for t, lane in expert]
    beat_ticks, downs = tempo_map.beats(max([t for t, _ in expert] + [0]) + tempo_map.ppq)
    levels = all_levels(notes, tempo_map.ppq, {t for t, d in zip(beat_ticks, downs) if d}, settings)
    return {lvl: [(n.tick, n.lane) for n in levels[lvl]] for lvl in LEVELS}


# MARK: - Adjustable controls

INSTRUMENTS = ("kick", "snare", "hihat", "toms", "cymbals")
DEFAULT_PRESET = "mpc"


def instrument(lane: str) -> str:
    if lane == "kick":
        return "kick"
    if lane == "red":
        return "snare"
    if lane == "yellow-cym":
        return "hihat"
    return "toms" if lane.endswith("-tom") else "cymbals"


@dataclass(frozen=True)
class Controls:
    density: float  # 0–1: share of Expert kept, before the weights
    weights: dict[str, float]  # instrument → factor on the density; 0 = off
    finest: int = 4  # finest straight grid in notes per beat: 1, 2, 4 or 8
    triplets: bool = True  # triplet positions down to the matching finest grid
    max_hands: int = 2  # hand notes at the same time
    max_notes: int = 3  # notes at the same time, kick included
    min_gap_ms: float = 0  # shortest gap between kick hits, and between hand hits

    def share(self, inst: str) -> float:
        return min(1.0, max(0.0, self.density * self.weights.get(inst, 1.0)))

    def to_dict(self) -> dict:
        return {"density": self.density, "weights": dict(self.weights), "finest": self.finest,
                "triplets": self.triplets, "max_hands": self.max_hands, "max_notes": self.max_notes,
                "min_gap_ms": self.min_gap_ms}

    @classmethod
    def from_dict(cls, d: dict) -> "Controls":
        weights = {i: float(d.get("weights", {}).get(i, 1.0)) for i in INSTRUMENTS}
        finest = int(d.get("finest", 4))
        if finest not in (1, 2, 4, 8):
            raise ValueError(f"finest must be 1, 2, 4 or 8, not {finest}")
        return cls(density=float(d.get("density", 1.0)), weights=weights, finest=finest,
                   triplets=bool(d.get("triplets", True)), max_hands=max(1, min(2, int(d.get("max_hands", 2)))),
                   max_notes=max(1, min(3, int(d.get("max_notes", 3)))),
                   min_gap_ms=max(0.0, float(d.get("min_gap_ms", 0))))


# Snare and crashes carry the song, so they keep more than the density; hi-hats and
# toms thin out first.
MPC = {
    "hard": Controls(0.8, {"kick": 1.0, "snare": 4.0, "hihat": 0.9, "toms": 0.9, "cymbals": 1.5},
                     finest=4, triplets=True, max_hands=2, max_notes=3, min_gap_ms=110),
    "medium": Controls(0.55, {"kick": 1.0, "snare": 4.0, "hihat": 0.9, "toms": 0.9, "cymbals": 1.6},
                       finest=2, triplets=False, max_hands=1, max_notes=2, min_gap_ms=200),
    "easy": Controls(0.3, {"kick": 1.0, "snare": 4.0, "hihat": 0.9, "toms": 0.8, "cymbals": 1.6},
                     finest=2, triplets=False, max_hands=1, max_notes=2, min_gap_ms=350),
}
PRESETS = ("mpc", "clone-hero")


def preset_settings(preset: str) -> dict[str, dict]:
    """Per-level settings of a preset, as stored in trackyard.json."""
    if preset == "clone-hero":
        return {lvl: {"preset": "clone-hero"} for lvl in LEVELS}
    if preset == "mpc":
        return {lvl: {"preset": "mpc", **MPC[lvl].to_dict()} for lvl in LEVELS}
    raise ValueError(f"unknown preset {preset!r}")


def clean_settings(settings: dict) -> dict[str, dict]:
    """Validate level settings from the editor; values are clamped, unknown levels refused."""
    out = {}
    for lvl, s in settings.items():
        if lvl not in LEVELS:
            raise ValueError(f"unknown level {lvl!r}")
        preset = s.get("preset", "custom")
        if preset == "clone-hero":
            out[lvl] = {"preset": "clone-hero"}
        else:
            out[lvl] = {"preset": preset if preset in PRESETS else "custom", **Controls.from_dict(s).to_dict()}
    return out


def reduce_level(notes: list[ChartNote], level: str, ppq: int, downbeats: set[int] | None = None,
                 settings: dict | None = None) -> list[ChartNote]:
    settings = settings or preset_settings(DEFAULT_PRESET)[level]
    if settings.get("preset") == "clone-hero":
        return reduce(notes, level, ppq, downbeats)
    return reduce_with(notes, Controls.from_dict(settings), ppq, downbeats)


def reduce_with(notes: list[ChartNote], c: Controls, ppq: int, downbeats: set[int] | None = None) -> list[ChartNote]:
    downbeats = downbeats or set()
    triplet_grid = {2: 3, 4: 6, 8: 12}.get(c.finest) if c.triplets else None

    def allowed(n: ChartNote) -> bool:
        pos = n.tick % ppq
        return (pos * c.finest) % ppq == 0 or (triplet_grid is not None and (pos * triplet_grid) % ppq == 0)

    def priority(n: ChartNote) -> tuple:
        return (strength(n.tick, ppq, downbeats, triplets=True), KEEP_ORDER[n.lane], -n.confidence, n.tick)

    # 1. Each instrument keeps its share of Expert: strong positions first. Down to
    #    eighths, a class that only partly fits is thinned evenly across the song;
    #    finer classes stay whole or go (a few scattered 16ths are harder, not easier).
    kept: list[ChartNote] = []
    for inst in INSTRUMENTS:
        mine = [n for n in notes if instrument(n.lane) == inst]
        budget = round(c.share(inst) * len(mine))
        classes: dict[int, list[ChartNote]] = {}
        for n in mine:
            if allowed(n):
                classes.setdefault(strength(n.tick, ppq, downbeats, triplets=True), []).append(n)
        for s in sorted(classes):
            group_notes = sorted(classes[s], key=lambda n: n.tick)
            if budget <= 0:
                break
            if len(group_notes) <= budget:
                kept += group_notes
                budget -= len(group_notes)
            elif s >= 3:
                break
            else:
                step = len(group_notes) / budget
                kept += [group_notes[math.floor((i + 0.5) * step)] for i in range(budget)]
                budget = 0

    # 2. Chords: at most max_hands hand notes and max_notes in all per tick.
    chords = limit_chords(kept, c.max_hands, c.max_notes)

    # 3. Shortest gap, separately for the kick and for the hands, strongest notes first.
    gap = c.min_gap_ms / 1000
    out: list[ChartNote] = []
    for feet in (True, False):
        stream = [n for n in chords if (n.lane == "kick") == feet]
        times: list[float] = []
        for n in sorted(stream, key=priority):
            if all(abs(n.time - t) >= gap - 1e-9 or abs(n.time - t) < 1e-9 for t in times):
                times.append(n.time)
                out.append(n)
    return sorted(out, key=lambda n: (n.tick, n.lane))
