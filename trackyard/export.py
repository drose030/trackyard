"""Write a Clone Hero / Beat-Train song folder: notes.mid, song.ini, OGG stems, cover."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .notes import ChartNote

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
