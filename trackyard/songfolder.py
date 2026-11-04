"""Find Clone Hero song folders and their audio stems, read song.ini."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = (".ogg", ".mp3", ".wav", ".opus", ".flac")
# Stems that make up the finished song. `crowd` is game ambience, `preview` a snippet.
MIX_STEMS = re.compile(r"^(song|drums(_\d)?|guitar|bass|rhythm|vocals(_\d)?|keys)$")
DRUM_STEMS = re.compile(r"^drums(_\d)?$")


@dataclass
class SongFolder:
    path: Path
    ini: dict[str, str]

    @property
    def title(self) -> str:
        artist = self.ini.get("artist", "").strip()
        name = self.ini.get("name", "").strip() or self.path.name
        return f"{artist} - {name}" if artist else name

    @property
    def cache_key(self) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", self.path.name).strip("-")[:48]
        digest = hashlib.sha1(str(self.path.resolve()).encode()).hexdigest()[:8]
        return f"{slug}-{digest}"

    def stems(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for f in sorted(self.path.iterdir()):
            if f.suffix.lower() in AUDIO_EXTENSIONS and MIX_STEMS.match(f.stem.lower()):
                found.setdefault(f.stem.lower(), f)  # one file per stem, first extension wins
        return found

    def drum_stems(self) -> list[Path]:
        return [p for name, p in self.stems().items() if DRUM_STEMS.match(name)]


def read_ini(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_bytes().decode("utf-8-sig", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith((";", "#", "[")):
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip()
    return values


def find_songs(roots: list[Path]) -> list[SongFolder]:
    """Every folder under `roots` (or a root itself) holding song.ini plus a chart."""
    songs: list[SongFolder] = []
    for root in roots:
        candidates = [root / "song.ini"] if (root / "song.ini").exists() else sorted(root.rglob("song.ini"))
        for ini in candidates:
            folder = ini.parent
            if (folder / "notes.mid").exists() or (folder / "notes.chart").exists():
                songs.append(SongFolder(folder, read_ini(ini)))
    return songs
