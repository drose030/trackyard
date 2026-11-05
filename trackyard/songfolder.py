"""A Clone Hero song folder: song.ini, a chart (notes.mid or notes.chart), audio stems,
and Trackyard's sidecar (trackyard.json: per-note confidence and level settings, which
Clone Hero ignores). Also where a song folder goes on an SD card for Beat-Train.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = (".ogg", ".mp3", ".wav", ".opus", ".flac")
# Stems that make up the finished song. `crowd` is game ambience, `preview` a snippet.
MIX_STEMS = re.compile(r"^(song|drums(_\d)?|guitar|bass|rhythm|vocals(_\d)?|keys)$")
DRUM_STEMS = re.compile(r"^drums(_\d)?$")
SIDECAR = "trackyard.json"


def stem_role(name: str) -> str:
    """"drums", "crowd" (game ambience, starts muted), "preview" (a snippet) or "mix"."""
    stem = Path(name).stem.lower()
    return "drums" if stem.startswith("drums") else stem if stem in ("crowd", "preview") else "mix"


def is_song(folder: Path) -> bool:
    return (folder / "song.ini").exists() and ((folder / "notes.mid").exists() or (folder / "notes.chart").exists())


def songs_dir(dest: Path, sd_card: bool = False) -> Path:
    """Where song folders go in `dest`: BeatTrain/Songs, where Beat-Train on the MPC looks,
    if it exists (or `sd_card` says to create it), else `dest` itself."""
    songs = dest / "BeatTrain" / "Songs"
    if sd_card:
        songs.mkdir(parents=True, exist_ok=True)
    return songs if songs.is_dir() else dest


@dataclass
class SongFolder:
    path: Path
    ini: dict[str, str]

    @classmethod
    def open(cls, folder: Path) -> "SongFolder":
        folder = folder.resolve()
        if not (folder / "song.ini").exists():
            raise FileNotFoundError(f"no song.ini in {folder}")
        if not is_song(folder):
            raise FileNotFoundError(f"no notes.mid or notes.chart in {folder}")
        return cls(folder, read_ini(folder / "song.ini"))

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

    def audio(self) -> dict[str, str]:
        """Every audio file to play along, file name → stem_role (previews left out)."""
        return {f.name: stem_role(f.name) for f in sorted(self.path.iterdir())
                if f.suffix.lower() in AUDIO_EXTENSIONS and stem_role(f.name) != "preview"}

    def sidecar(self) -> dict:
        file = self.path / SIDECAR
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}

    def confidence(self) -> dict[tuple[int, str], float]:
        """Per-note confidence from the conversion, (tick, lane) → 0–1."""
        return {(n["tick"], n["lane"]): n["confidence"] for n in self.sidecar().get("notes", [])}

    def save_level_settings(self, settings: dict[str, dict]) -> None:
        data = {"trackyard": 1, **self.sidecar()}
        data["levels"] = {**data.get("levels", {}), **settings}
        (self.path / SIDECAR).write_text(json.dumps(data, indent=1), encoding="utf-8")


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
            if is_song(ini.parent):
                songs.append(SongFolder(ini.parent, read_ini(ini)))
    return songs


def write_sidecar(folder: Path, notes, settings: dict, levels: dict | None = None) -> None:
    """A new sidecar after a conversion: `notes` are its ChartNotes (tick, lane,
    confidence, onset time), `settings` how it ran, `levels` the level settings."""
    data = {
        "trackyard": 1,
        "settings": settings,
        "levels": levels or {},
        "notes": [{"tick": n.tick, "lane": n.lane, "confidence": round(n.confidence, 3),
                   "onset": round(n.time, 4)} for n in notes],
    }
    (folder / SIDECAR).write_text(json.dumps(data, indent=1), encoding="utf-8")
