"""Replace the drum notes of an existing chart, keeping everything else.

The editor only changes drum gems on the four difficulties and their pro-drums markers
(tom markers in MIDI, cymbal markers in .chart), plus the tempo events and the offset
when those were edited. Other instruments, time signatures, star power, fills, solos,
text events and so on stay exactly as they were, so editing a downloaded Clone Hero
song never loses its guitar or vocal charts. Notes are stored in ticks, so a new tempo
map moves them with the grid.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import mido

from .chart import MIDI_BASE

LANE_OFFSET = {"kick": 0, "red": 1, "yellow": 2, "blue": 3, "green": 4}
TOM_MARKER = {"yellow": 110, "blue": 111, "green": 112}
CHART_LANE = {"kick": 0, "red": 1, "yellow": 2, "blue": 3, "green": 4}
CHART_CYMBAL = {"yellow": 66, "blue": 67, "green": 68}
SECTION = {"expert": "ExpertDrums", "hard": "HardDrums", "medium": "MediumDrums", "easy": "EasyDrums"}

Levels = dict[str, list[tuple[int, str]]]  # difficulty → [(tick, lane)]


def backup(path: Path) -> Path | None:
    """Keep the original once as `<name>.orig` before the first save."""
    orig = path.with_name(path.name + ".orig")
    if path.exists() and not orig.exists():
        shutil.copy2(path, orig)
        return orig
    return None


def save_drums(song_dir: Path, levels: Levels, keep_original: bool = True) -> Path:
    """Write `levels` into the song's notes.mid (preferred) or notes.chart; returns the file."""
    mid, chart = song_dir / "notes.mid", song_dir / "notes.chart"
    if mid.exists():
        if keep_original:
            backup(mid)
        replace_drums_midi(mid, levels)
        return mid
    if keep_original:
        backup(chart)
    replace_drums_chart(chart, levels)
    return chart


def _is_drum_gem(note: int) -> bool:
    if note in TOM_MARKER.values() or note == 95:  # tom markers, Expert 2x kick
        return True
    return any(base <= note <= base + 4 for base in MIDI_BASE.values())


def replace_drums_midi(path: Path, levels: Levels) -> None:
    mid = mido.MidiFile(path, clip=True)
    ppq = mid.ticks_per_beat
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

    by_note: dict[int, set[int]] = {}
    toms: dict[str, set[int]] = {c: set() for c in TOM_MARKER}
    for level, notes in levels.items():
        for tick, lane in notes:
            color = lane.split("-")[0]
            by_note.setdefault(MIDI_BASE[level] + LANE_OFFSET[color], set()).add(tick)
            if lane.endswith("-tom"):
                toms[color].add(tick)
    for color, ticks in toms.items():
        if ticks:
            by_note[TOM_MARKER[color]] = ticks

    events = list(kept)
    for pitch, ticks in by_note.items():
        ordered = sorted(ticks)
        for a, b in zip(ordered, ordered[1:] + [None]):
            length = ppq // 8 if b is None else max(1, min(ppq // 8, b - a - 1))
            events.append((a, mido.Message("note_on", channel=9, note=pitch, velocity=100)))
            events.append((a + length, mido.Message("note_off", channel=9, note=pitch, velocity=0)))

    events.sort(key=lambda e: (e[0], 0 if e[1].type == "note_off" else 1))
    track = mido.MidiTrack([mido.MetaMessage("track_name", name="PART DRUMS", time=0)])
    now = 0
    for tick, msg in events:
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    if index is None:
        mid.tracks.append(track)
    else:
        mid.tracks[index] = track
    mid.save(path)


def replace_drums_chart(path: Path, levels: Levels) -> None:
    """Rewrite the [<Level>Drums] sections. Star power and other events stay; accents and
    ghost flags stay only where their note still exists."""
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    done: set[str] = set()
    while i < len(lines):
        line = lines[i]
        name = line.strip()[1:-1] if line.strip().startswith("[") and line.strip().endswith("]") else None
        level = next((lvl for lvl, sec in SECTION.items() if sec == name), None)
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
            out += [f"[{SECTION[level]}]", "{"] + _chart_section([], notes) + ["}"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _chart_section(body: list[str], notes: list[tuple[int, str]]) -> list[str]:
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
            if n in (0, 1, 2, 3, 4, 5, 32, 66, 67, 68):
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


Tempos = list[tuple[int, float]]  # (tick, BPM), the first at tick 0


def save_tempo(song_dir: Path, tempos: Tempos | None, offset: float | None, keep_original: bool = True) -> None:
    """Replace the tempo events and/or set the offset (seconds added to chart time)."""
    mid, chart = song_dir / "notes.mid", song_dir / "notes.chart"
    if tempos is not None:
        target = mid if mid.exists() else chart
        if keep_original:
            backup(target)
        if target == mid:
            replace_tempo_midi(mid, tempos)
        else:
            replace_tempo_chart(chart, tempos)
    if offset is not None:
        set_ini_delay(song_dir / "song.ini", offset)
        if not mid.exists() and chart.exists():
            set_chart_offset(chart, offset)  # read_notes falls back to it when delay is 0


def replace_tempo_midi(path: Path, tempos: Tempos) -> None:
    mid = mido.MidiFile(path, clip=True)
    for i, track in enumerate(mid.tracks):
        if i != 0 and not any(m.type == "set_tempo" for m in track):
            continue
        events: list[tuple[int, mido.Message]] = []
        end = tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "end_of_track":
                end = tick
            elif msg.type != "set_tempo":
                events.append((tick, msg))
        if i == 0:  # the tempo map lives in the first track
            events += [(t, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))) for t, bpm in tempos]
        events.sort(key=lambda e: e[0])  # stable: existing events keep their order
        rebuilt = mido.MidiTrack()
        now = 0
        for t, msg in events:
            rebuilt.append(msg.copy(time=t - now))
            now = t
        rebuilt.append(mido.MetaMessage("end_of_track", time=max(0, end - now)))
        mid.tracks[i] = rebuilt
    mid.save(path)


def replace_tempo_chart(path: Path, tempos: Tempos) -> None:
    """Swap the `B` lines of [SyncTrack]; time signatures (`TS`) and anchors stay."""
    lines = path.read_bytes().decode("utf-8-sig", errors="replace").splitlines()
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
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def set_chart_offset(path: Path, offset: float) -> None:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    value = f"{offset:.3f}".rstrip("0").rstrip(".") or "0"
    if re.search(r"(?m)^\s*Offset\s*=", text):
        text = re.sub(r"(?m)^(\s*Offset\s*=).*$", lambda m: f"{m.group(1)} {value}", text)
    else:
        text = re.sub(r"(?m)^(\s*Resolution\s*=.*)$", lambda m: f"{m.group(1)}\n  Offset = {value}", text, count=1)
    path.write_text(text, encoding="utf-8")


def set_ini_delay(path: Path, offset: float) -> None:
    """Set `delay` (ms) in song.ini, keeping every other line."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else ["[song]"]
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
