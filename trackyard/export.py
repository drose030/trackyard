"""Write a Clone Hero / Beat-Train song folder: notes.mid, song.ini, OGG stems, cover."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from .chart import TempoMap
from .notes import ChartNote

# PART DRUMS pitches: difficulty base + lane offset; tom markers apply to all levels.
MIDI_BASE = {"expert": 96, "hard": 84, "medium": 72, "easy": 60}
LANE_OFFSET = {"kick": 0, "red": 1, "yellow": 2, "blue": 3, "green": 4}
TOM_MARKER = {"yellow": 110, "blue": 111, "green": 112}


GENERIC_STEMS = {"song", "audio", "track", "mix", "guitar", "drums"}


@dataclass
class SongInfo:
    name: str
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: str = ""
    length_ms: int = 0
    extra: dict[str, str] = field(default_factory=dict)


def write_midi(path: Path, title: str, tempo_map: TempoMap, notes: list[ChartNote],
               beat_ticks: list[int], downbeat_ticks: list[int],
               lower: dict[str, list[ChartNote]] | None = None) -> None:
    """`notes` is Expert; `lower` optionally adds "hard", "medium", "easy" (subsets of Expert,
    so one set of tom markers fits every level)."""
    ppq = tempo_map.ppq
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)

    tempo_events = [(t, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))) for t, bpm in tempo_map.tempos]
    sig_events = [(t, mido.MetaMessage("time_signature", numerator=n, denominator=d, clocks_per_click=24,
                                       notated_32nd_notes_per_beat=8)) for t, n, d in tempo_map.signatures]
    mid.tracks.append(_track(title, sig_events + tempo_events))

    drum_events = []
    by_note: dict[int, list[int]] = {}
    for level, level_notes in {"expert": notes, **(lower or {})}.items():
        for n in level_notes:
            color = n.lane.split("-")[0]
            by_note.setdefault(MIDI_BASE[level] + LANE_OFFSET[color], []).append(n.tick)
            if n.lane.endswith("-tom"):
                by_note.setdefault(TOM_MARKER[color], []).append(n.tick)
    for pitch, ticks in by_note.items():
        ticks = sorted(set(ticks))
        for a, b in zip(ticks, ticks[1:] + [None]):
            length = ppq // 8 if b is None else max(1, min(ppq // 8, b - a - 1))
            drum_events.append((a, mido.Message("note_on", channel=9, note=pitch, velocity=100)))
            drum_events.append((a + length, mido.Message("note_off", channel=9, note=pitch, velocity=0)))
    mid.tracks.append(_track("PART DRUMS", drum_events))

    downs = set(downbeat_ticks)
    beat_events = []
    for t in beat_ticks:
        pitch = 12 if t in downs else 13
        beat_events.append((t, mido.Message("note_on", note=pitch, velocity=100)))
        beat_events.append((t + ppq // 4, mido.Message("note_off", note=pitch, velocity=0)))
    mid.tracks.append(_track("BEAT", beat_events))

    last = max([n.tick for n in notes] + [beat_ticks[-1] if beat_ticks else 0])
    start = downbeat_ticks[0] if downbeat_ticks else 0
    mid.tracks.append(_track("EVENTS", [
        (start, mido.MetaMessage("text", text="[music_start]")),
        (last + 2 * ppq, mido.MetaMessage("text", text="[music_end]")),
        (last + 4 * ppq, mido.MetaMessage("text", text="[end]")),
    ]))
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(path)


def _track(name: str, events: list[tuple[int, mido.Message]]) -> mido.MidiTrack:
    """Absolute-tick events → a track with delta times; note-offs sort before note-ons."""
    def order(e):
        tick, msg = e
        return tick, 0 if msg.type == "note_off" else 1
    track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    now = 0
    for tick, msg in sorted(events, key=order):
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def write_ini(path: Path, info: SongInfo, notes: list[ChartNote]) -> None:
    span = (notes[-1].time - notes[0].time) if len(notes) > 1 else 1.0
    nps = len(notes) / max(span, 1.0)
    diff = int(np.searchsorted([1.5, 2.5, 3.5, 4.5, 5.5, 7.0], nps))
    values = {
        "name": info.name, "artist": info.artist, "album": info.album, "genre": info.genre, "year": info.year,
        "charter": "Trackyard (auto)", "song_length": str(info.length_ms), "delay": "0",
        "pro_drums": "True", "diff_drums": str(diff), "diff_drums_real": str(diff),
        "preview_start_time": str(int(info.length_ms * 0.3)), **info.extra,
    }
    lines = ["[song]"] + [f"{k} = {v}" for k, v in values.items() if v != ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_ogg(path: Path, audio: np.ndarray, sr: int, quality: float = 0.6) -> None:
    """Vorbis via libsndfile (ffmpeg builds often lack libvorbis). `quality` 0..1, higher is better."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(path, "w", sr, audio.shape[1], format="OGG", subtype="VORBIS",
                      compression_level=1.0 - quality) as f:
        for i in range(0, len(audio), 1 << 16):  # big single writes crash some libsndfile builds
            f.write(audio[i : i + (1 << 16)])


def write_sidecar(path: Path, notes: list[ChartNote], settings: dict, levels: dict | None = None) -> None:
    """Per-note confidence and the level settings for the editor; Clone Hero ignores the file."""
    data = {
        "trackyard": 1,
        "settings": settings,
        "levels": levels or {},
        "notes": [{"tick": n.tick, "lane": n.lane, "confidence": round(n.confidence, 3),
                   "onset": round(n.time, 4)} for n in notes],
    }
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def read_tags(path: Path) -> tuple[SongInfo, tuple[bytes, str] | None]:
    """Title/artist/album/genre/year and the embedded cover (bytes, extension) via mutagen."""
    import mutagen

    # Clone Hero folders name their audio song.ogg etc.; the folder name is the useful part then.
    stem = path.parent.name if path.stem.lower() in GENERIC_STEMS else path.stem
    artist, _, title = stem.partition(" - ") if " - " in stem else ("", "", stem)
    info = SongInfo(name=title.strip(), artist=artist.strip())
    cover = None
    try:
        easy = mutagen.File(path, easy=True)
        full = mutagen.File(path)
    except Exception:
        return info, None
    if easy is not None and easy.tags:
        get = lambda k: (easy.tags.get(k) or [""])[0].strip()  # noqa: E731
        info.name = get("title") or info.name
        info.artist = get("artist") or info.artist
        info.album = get("album")
        info.genre = get("genre")
        info.year = (get("date") or "")[:4]
    if full is not None:
        cover = _cover(full)
    return info, cover


def _cover(f) -> tuple[bytes, str] | None:
    pics = []
    if getattr(f, "pictures", None):  # FLAC
        pics = [(p.data, p.mime) for p in f.pictures]
    elif f.tags is not None and hasattr(f.tags, "getall"):  # ID3
        pics = [(p.data, p.mime) for p in f.tags.getall("APIC")]
    elif f.tags is not None and "covr" in f.tags:  # MP4
        pics = [(bytes(c), "image/png" if c.imageformat == 14 else "image/jpeg") for c in f.tags["covr"]]
    elif f.tags is not None and "metadata_block_picture" in f.tags:  # Ogg
        import base64

        from mutagen.flac import Picture

        p = Picture(base64.b64decode(f.tags["metadata_block_picture"][0]))
        pics = [(p.data, p.mime)]
    if not pics:
        return None
    data, mime = pics[0]
    return data, ".png" if "png" in (mime or "") else ".jpg"


def folder_name(info: SongInfo) -> str:
    name = f"{info.artist} - {info.name}" if info.artist else info.name
    return re.sub(r'[\\/:*?"<>|]+', "", name).strip() or "Untitled"
