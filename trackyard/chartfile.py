"""The drum chart of a song folder: notes.mid or notes.chart, plus song.ini's delay.

`read` gives every difficulty's drum notes, the tempo map and the offset; `save`
replaces drum notes, tempo events and/or the offset and keeps everything else (other
instruments, star power, fills, text events), so editing a downloaded Clone Hero song
never loses its guitar or vocal charts; `create` writes a new notes.mid. Notes are
(tick, lane) pairs, so a new tempo map moves them with the grid.

Lanes: kick, red, and yellow/blue/green as -cym or -tom (4-lane pro drums). A tom or
cymbal is a property of a (tick, colour) on every difficulty at once: MIDI tom markers
(110–112) and .chart cymbal markers (66–68) are not per difficulty.

Reading follows Clone Hero:

* MIDI drums are pro: yellow/blue/green are cymbals unless a tom marker covers them.
* .chart: toms unless a cymbal marker sits on the same tick; without pro_drums in
  song.ini and without any markers, yellow is a hi-hat.
* 5-lane charts (MIDI offset 5 / .chart lane 5) read as yellow-cym, blue-tom,
  orange-cym, green-tom; they can't be saved.
* The offset (seconds added to chart time to get audio time) is song.ini's `delay`;
  a .chart's `Offset` is the fallback, since charts often carry both with one value.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import mido

from .songfolder import read_ini
from .tempomap import TempoMap

DIFFICULTIES = ("expert", "hard", "medium", "easy")
COLORS = ("yellow", "blue", "green")

Notes = list[tuple[int, str]]  # (tick, lane), sorted
Levels = dict[str, Notes]  # difficulty → notes
Tempos = list[tuple[int, float]]  # (tick, BPM), the first at tick 0


@dataclass
class Chart:
    levels: Levels  # every difficulty, empty when the chart has none
    tempo_map: TempoMap
    offset: float  # seconds added to chart time to get audio time
    pro: bool
    five_lane: bool


def path(folder: Path) -> Path:
    """The chart file: notes.mid, else notes.chart."""
    mid = folder / "notes.mid"
    return mid if mid.exists() or not (folder / "notes.chart").exists() else folder / "notes.chart"


def read(folder: Path, ini: dict[str, str] | None = None) -> Chart:
    """`ini`: song.ini's values when the caller has them (default: read song.ini)."""
    if ini is None:
        ini = read_ini(folder / "song.ini") if (folder / "song.ini").exists() else {}
    file = path(folder)
    if not file.exists():
        raise FileNotFoundError(f"no notes.mid or notes.chart in {folder}")
    pro_flag = ini.get("pro_drums", "").strip().lower() in ("true", "1")
    reader = _read_midi if file.suffix == ".mid" else _read_chart
    levels, tempo_map, five_lane, pro, chart_offset = reader(file, pro_flag)
    delay = _float(ini.get("delay")) / 1000.0
    return Chart({lvl: sorted(set(levels.get(lvl, []))) for lvl in DIFFICULTIES}, tempo_map,
                 delay if delay else chart_offset, pro, five_lane)


def save(folder: Path, levels: Levels | None = None, tempos: Tempos | None = None, offset: float | None = None,
         keep_original: bool = True) -> Path:
    """Replace the given difficulties' drum notes, the tempo events and/or the offset.
    `keep_original` backs the chart up once as <name>.orig. Returns the chart file."""
    file = path(folder)
    if not file.exists():
        raise FileNotFoundError(f"no notes.mid or notes.chart in {folder}")
    if levels is not None:
        check(levels)
    if keep_original and (levels is not None or tempos is not None):
        _backup(file)
    midi = file.suffix == ".mid"
    if levels is not None:
        (_replace_drums_midi if midi else _replace_drums_chart)(file, levels)
    if tempos is not None:
        (_replace_tempo_midi if midi else _replace_tempo_chart)(file, tempos)
    if offset is not None:
        _set_ini_delay(folder / "song.ini", offset)
        if not midi:
            _set_chart_offset(file, offset)  # read() falls back to it when delay is 0
    return file


def create(folder: Path, title: str, tempo_map: TempoMap, levels: Levels,
           beat_ticks: list[int], downbeat_ticks: list[int]) -> Path:
    """A new notes.mid: tempo map, PART DRUMS, a BEAT track and the song's start and end."""
    check(levels)
    ppq = tempo_map.ppq
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    tempo_events = [(t, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))) for t, bpm in tempo_map.tempos]
    sig_events = [(t, mido.MetaMessage("time_signature", numerator=n, denominator=d, clocks_per_click=24,
                                       notated_32nd_notes_per_beat=8)) for t, n, d in tempo_map.signatures]
    mid.tracks.append(_track(title, sig_events + tempo_events))
    mid.tracks.append(_track("PART DRUMS", _midi_gems(levels, ppq)))

    downs = set(downbeat_ticks)
    beat_events = []
    for t in beat_ticks:
        pitch = 12 if t in downs else 13
        beat_events.append((t, mido.Message("note_on", note=pitch, velocity=100)))
        beat_events.append((t + ppq // 4, mido.Message("note_off", note=pitch, velocity=0)))
    mid.tracks.append(_track("BEAT", beat_events))

    last = max([t for t, _ in levels.get("expert", [])] + [beat_ticks[-1] if beat_ticks else 0])
    start = downbeat_ticks[0] if downbeat_ticks else 0
    mid.tracks.append(_track("EVENTS", [
        (start, mido.MetaMessage("text", text="[music_start]")),
        (last + 2 * ppq, mido.MetaMessage("text", text="[music_end]")),
        (last + 4 * ppq, mido.MetaMessage("text", text="[end]")),
    ]))
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "notes.mid"
    mid.save(out)
    return out


def check(levels: Levels) -> None:
    """What a chart file can hold: known difficulties and lanes, one gem per colour and
    tick, and each (tick, colour) the same cymbal or tom on every difficulty."""
    kinds: dict[tuple[int, str], str] = {}
    for level, notes in levels.items():
        if level not in DIFFICULTIES:
            raise ValueError(f"unknown difficulty {level!r}")
        seen: set[tuple[int, str]] = set()
        for tick, lane in notes:
            color, _, kind = lane.partition("-")
            if tick < 0 or not (lane in ("kick", "red") or (color in COLORS and kind in ("cym", "tom"))):
                raise ValueError(f"bad note {tick} {lane!r}")
            if (tick, color) in seen:
                raise ValueError(f"two {color} notes at tick {tick} on {level}")
            seen.add((tick, color))
            if kind and kinds.setdefault((tick, color), kind) != kind:
                raise ValueError(f"{color} at tick {tick} is a cymbal on one difficulty and a tom on another")


def _float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0


def _backup(file: Path) -> None:
    orig = file.with_name(file.name + ".orig")
    if not orig.exists():
        shutil.copy2(file, orig)


def _set_ini_delay(file: Path, offset: float) -> None:
    """Set `delay` (ms) in song.ini, keeping every other line."""
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines() if file.exists() else ["[song]"]
    value = f"delay = {round(offset * 1000)}"
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip().lower() == "delay" and "=" in line:
            lines[i] = value
            break
    else:
        at = next((i + 1 for i, line in enumerate(lines) if line.strip().lower() == "[song]"), None)
        if at is None:
            lines[:0], at = ["[song]"], 1
        lines.insert(at, value)
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


# MARK: - MIDI adapter

MIDI_BASE = {"expert": 96, "hard": 84, "medium": 72, "easy": 60}  # + lane offset
MIDI_LANE = {"kick": 0, "red": 1, "yellow": 2, "blue": 3, "green": 4}
MIDI_TOM = {"yellow": 110, "blue": 111, "green": 112}
MIDI_2X_KICK = 95  # Expert only, one below the base


def _read_midi(file: Path, pro_flag: bool):
    mid = mido.MidiFile(file, clip=True)
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
        raise ValueError(f"{file}: no PART DRUMS track")
    tempo_map = TempoMap(mid.ticks_per_beat, sorted(tempos), sorted(set(signatures)))

    raw: dict[str, list[tuple[int, int]]] = {lvl: [] for lvl in DIFFICULTIES}  # (tick, note − base)
    open_markers: dict[int, int] = {}
    tom_ranges: dict[str, list[tuple[int, int]]] = {c: [] for c in COLORS}
    color_of_marker = {v: k for k, v in MIDI_TOM.items()}
    tick = 0
    for msg in drum_track:
        tick += msg.time
        if msg.type not in ("note_on", "note_off"):
            continue
        on = msg.type == "note_on" and msg.velocity > 0
        if msg.note in color_of_marker:
            if on:
                open_markers[msg.note] = tick
            elif msg.note in open_markers:
                tom_ranges[color_of_marker[msg.note]].append((open_markers.pop(msg.note), tick))
        elif on:
            for level, base in MIDI_BASE.items():
                if base - (level == "expert") <= msg.note <= base + 5:
                    raw[level].append((tick, msg.note - base))

    five_lane = any(n == 5 for notes in raw.values() for _, n in notes)
    lane_of = {v: k for k, v in MIDI_LANE.items()} | {-1: "kick"}
    levels: Levels = {}
    for level, notes in raw.items():
        out: Notes = []
        for tick, n in notes:
            if five_lane:
                lane = {-1: "kick", 0: "kick", 1: "red", 2: "yellow-cym", 3: "blue-tom", 4: "orange-cym", 5: "green-tom"}[n]
            else:
                lane = lane_of[n]
                if lane in COLORS:
                    lane += "-tom" if any(a <= tick < b for a, b in tom_ranges[lane]) else "-cym"
            out.append((tick, lane))
        levels[level] = out
    return levels, tempo_map, five_lane, True, 0.0


def _midi_gems(levels: Levels, ppq: int) -> list[tuple[int, mido.Message]]:
    """Note on/off events for the drum gems and tom markers of `levels`."""
    by_note: dict[int, set[int]] = {}
    for level, notes in levels.items():
        for tick, lane in notes:
            color = lane.split("-")[0]
            by_note.setdefault(MIDI_BASE[level] + MIDI_LANE[color], set()).add(tick)
            if lane.endswith("-tom"):
                by_note.setdefault(MIDI_TOM[color], set()).add(tick)
    events = []
    for pitch, ticks in by_note.items():
        ordered = sorted(ticks)
        for a, b in zip(ordered, ordered[1:] + [None]):
            length = ppq // 8 if b is None else max(1, min(ppq // 8, b - a - 1))
            events.append((a, mido.Message("note_on", channel=9, note=pitch, velocity=100)))
            events.append((a + length, mido.Message("note_off", channel=9, note=pitch, velocity=0)))
    return events


def _is_drum_gem(note: int) -> bool:
    if note in MIDI_TOM.values() or note == MIDI_2X_KICK:
        return True
    return any(base <= note <= base + 4 for base in MIDI_BASE.values())


def _replace_drums_midi(file: Path, levels: Levels) -> None:
    mid = mido.MidiFile(file, clip=True)
    index = next((i for i, t in enumerate(mid.tracks)
                  if any(m.type == "track_name" and m.name == "PART DRUMS" for m in t)), None)
    kept: list[tuple[int, mido.Message]] = []
    if index is not None:
        tick = 0
        for msg in mid.tracks[index]:
            tick += msg.time
            if msg.type in ("note_on", "note_off") and _is_drum_gem(msg.note):
                continue
            if msg.type in ("track_name", "end_of_track"):
                continue
            kept.append((tick, msg))
    if index is not None:
        # Difficulties not being saved keep their gems: read them back and write them again.
        current, *_ = _read_midi(file, True)
        levels = {**{lvl: current[lvl] for lvl in DIFFICULTIES if current[lvl]}, **levels}
        check(levels)
    track = _track("PART DRUMS", kept + _midi_gems(levels, mid.ticks_per_beat))
    if index is None:
        mid.tracks.append(track)
    else:
        mid.tracks[index] = track
    mid.save(file)


def _replace_tempo_midi(file: Path, tempos: Tempos) -> None:
    mid = mido.MidiFile(file, clip=True)
    for i, track in enumerate(mid.tracks):
        if i != 0 and not any(m.type == "set_tempo" for m in track):
            continue
        events: list[tuple[int, mido.Message]] = []
        end = tick = 0
        name = None
        for msg in track:
            tick += msg.time
            if msg.type == "end_of_track":
                end = tick
            elif msg.type == "track_name" and name is None and tick == 0:
                name = msg.name
            elif msg.type != "set_tempo":
                events.append((tick, msg))
        if i == 0:  # the tempo map lives in the first track
            events += [(t, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))) for t, bpm in tempos]
        mid.tracks[i] = _track(name, events, end, stable=True)
    mid.save(file)


def _track(name: str | None, events: list[tuple[int, mido.Message]], end: int = 0,
           stable: bool = False) -> mido.MidiTrack:
    """Absolute-tick events → a track with delta times. Note-offs sort before note-ons on
    one tick; `stable` keeps the given order on a tick instead (existing tracks)."""
    def order(e):
        return (e[0],) if stable else (e[0], 0 if e[1].type == "note_off" else 1)
    track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)] if name is not None else [])
    now = 0
    for tick, msg in sorted(events, key=order):
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, end - now)))
    return track


# MARK: - .chart adapter

CHART_SECTION = {"expert": "ExpertDrums", "hard": "HardDrums", "medium": "MediumDrums", "easy": "EasyDrums"}
CHART_LANE = {"kick": 0, "red": 1, "yellow": 2, "blue": 3, "green": 4}
CHART_CYMBAL = {"yellow": 66, "blue": 67, "green": 68}
CHART_2X_KICK = 32
CHART_GEMS = {*CHART_LANE.values(), 5, CHART_2X_KICK, *CHART_CYMBAL.values()}


def _chart_text(file: Path) -> str:
    return file.read_bytes().decode("utf-8-sig", errors="replace")


def _read_chart(file: Path, pro_flag: bool):
    section = None
    resolution = 192
    chart_offset = 0.0
    tempos: list[tuple[int, float]] = []
    signatures: list[tuple[int, int, int]] = []
    events: dict[str, list[tuple[int, int]]] = {lvl: [] for lvl in DIFFICULTIES}
    level_of = {v: k for k, v in CHART_SECTION.items()}
    for line in _chart_text(file).splitlines():
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
        elif section in level_of and key.isdigit() and parts[0] == "N":
            events[level_of[section]].append((int(key), int(parts[1])))
    tempo_map = TempoMap(resolution, sorted(tempos), sorted(set(signatures)))

    cymbal_lane = {v: CHART_LANE[k] for k, v in CHART_CYMBAL.items()}
    five_lane = any(n == 5 for notes in events.values() for _, n in notes)
    any_markers = any(n in cymbal_lane for notes in events.values() for _, n in notes)
    pro = (pro_flag or any_markers) and not five_lane
    color_of = {v: k for k, v in CHART_LANE.items()}
    levels: Levels = {}
    for level, notes in events.items():
        markers = {(t, cymbal_lane[n]) for t, n in notes if n in cymbal_lane}
        out: Notes = []
        for tick, n in notes:
            if n in (CHART_LANE["kick"], CHART_2X_KICK):
                lane = "kick"
            elif n == CHART_LANE["red"]:
                lane = "red"
            elif five_lane and n in (2, 3, 4, 5):
                lane = {2: "yellow-cym", 3: "blue-tom", 4: "orange-cym", 5: "green-tom"}[n]
            elif n in (2, 3, 4):
                cymbal = (tick, n) in markers or (n == CHART_LANE["yellow"] and not pro_flag)
                lane = color_of[n] + ("-cym" if cymbal else "-tom")
            else:
                continue  # accents, ghosts, markers, star power
            out.append((tick, lane))
        levels[level] = out
    return levels, tempo_map, five_lane, pro, chart_offset


def _replace_drums_chart(file: Path, levels: Levels) -> None:
    """Rewrite the [<Level>Drums] sections. Star power and other events stay; accents and
    ghost flags stay only where their note still exists."""
    lines = _chart_text(file).splitlines()
    out: list[str] = []
    i = 0
    done: set[str] = set()
    level_of = {v: k for k, v in CHART_SECTION.items()}
    while i < len(lines):
        line = lines[i]
        name = line.strip()[1:-1] if line.strip().startswith("[") and line.strip().endswith("]") else None
        level = level_of.get(name)
        if level is None or level not in levels:
            out.append(line)
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() != "}":
            if lines[i].strip() != "{":
                body.append(lines[i])
            i += 1
        i += 1  # skip "}"
        out += [line, "{"] + _chart_section(body, levels[level]) + ["}"]
        done.add(level)
    for level, notes in levels.items():
        if level not in done and notes:
            out += [f"[{CHART_SECTION[level]}]", "{"] + _chart_section([], notes) + ["}"]
    file.write_text("\n".join(out) + "\n", encoding="utf-8")


def _chart_section(body: list[str], notes: Notes) -> list[str]:
    lanes_at: dict[int, set[int]] = {}
    for tick, lane in notes:
        lanes_at.setdefault(tick, set()).add(CHART_LANE[lane.split("-")[0]])
    entries: list[tuple[int, int, str]] = []  # (tick, order, line)
    for raw in body:
        key, _, value = raw.partition("=")
        parts = value.split()
        if not key.strip().isdigit() or not parts:
            continue
        tick = int(key)
        if parts[0] == "N":
            n = int(parts[1])
            if n in CHART_GEMS:
                continue  # rewritten below
            if 34 <= n <= 38 or 40 <= n <= 44:  # accent / ghost flag for lane n - 33 / n - 39
                lane = n - 33 if n < 40 else n - 39
                if lane not in lanes_at.get(tick, set()):
                    continue
        entries.append((tick, 2, raw.strip()))
    for tick, lane in notes:
        color = lane.split("-")[0]
        entries.append((tick, 0, f"{tick} = N {CHART_LANE[color]} 0"))
        if lane.endswith("-cym") and color in CHART_CYMBAL:
            entries.append((tick, 1, f"{tick} = N {CHART_CYMBAL[color]} 0"))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    return ["  " + line for _, _, line in entries]


def _replace_tempo_chart(file: Path, tempos: Tempos) -> None:
    """Swap the `B` lines of [SyncTrack]; time signatures (`TS`) and anchors stay."""
    lines = _chart_text(file).splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if lines[i].strip() != "[SyncTrack]":
            i += 1
            continue
        i += 1
        entries: list[tuple[int, int, str]] = []
        while i < len(lines) and lines[i].strip() != "}":
            line = lines[i].strip()
            i += 1
            if line == "{" or "=" not in line:
                continue
            key, value = (x.strip() for x in line.split("=", 1))
            if value.split()[0] != "B" and key.isdigit():
                entries.append((int(key), 0, value))
        entries += [(t, 1, f"B {round(bpm * 1000)}") for t, bpm in tempos]
        entries.sort(key=lambda e: (e[0], e[1]))
        out += ["{"] + [f"  {t} = {v}" for t, _, v in entries] + ["}"]
        i += 1  # the closing brace
    file.write_text("\n".join(out) + "\n", encoding="utf-8")


def _set_chart_offset(file: Path, offset: float) -> None:
    text = _chart_text(file)
    value = f"{offset:.3f}".rstrip("0").rstrip(".") or "0"
    if re.search(r"(?m)^\s*Offset\s*=", text):
        text = re.sub(r"(?m)^(\s*Offset\s*=).*$", lambda m: f"{m.group(1)} {value}", text)
    else:
        text = re.sub(r"(?m)^(\s*Resolution\s*=.*)$", lambda m: f"{m.group(1)}\n  Offset = {value}", text, count=1)
    file.write_text(text, encoding="utf-8")
