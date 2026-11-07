"""One drum sound per Beat-Train voice, cut from a song's drum stem. Beat-Train can mute
the drum stem and play these on each pad strike instead.

Files: sounds/<voice>.wav in the song folder, 16-bit PCM, where the voice is the row
colour: kick (Bass drum), red (Snare), yellow (Hi-hat / Tom), blue (Tom / Ride), orange
(Cymbal), green (Floor tom). The drum stem is one track, so with pad sounds on Beat-Train
mutes it as a whole: a voice without a file stays silent then. Which hit each file was
cut from is kept in trackyard.json ("sounds").

A sound starts 1 ms before the hit's attack in the drum stem (found to the sample: the
chart and the stem are often a few ms apart, and players feel that), fades in over 2 ms
so the previous hit's tail doesn't click, and lasts as long as it rings (until it is 40 dB
down), stopping 5 ms before the next hit of any row and at most MAX_SECONDS, with a
short fade-out. Candidates are all of the voice's notes, ranked: fewest notes of other
rows at the same time first (in many songs every kick sounds with a hi-hat), then long
free ringing, a level in the upper middle of the voice's hits (no ghost notes or
accents) and some room before the hit (no flams).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio as audio_io
from .songfolder import SongFolder

VOICES = ("red", "yellow", "blue", "orange", "green", "kick")
MAX_SECONDS = {"kick": 0.6, "red": 0.6, "yellow": 1.0, "blue": 1.2, "orange": 1.5, "green": 0.8}
PRE_ROLL = 0.001
FADE_IN = 0.002
FADE = 0.03
TOGETHER = 0.02  # notes closer than this sound as one hit
DIR = "sounds"
EXTENSIONS = (".wav", ".ogg", ".mp3")


def voice(lane: str) -> str:
    """The Beat-Train voice (row) of a chart lane: green-cym is the orange pad."""
    return "orange" if lane == "green-cym" else "green" if lane == "green-tom" else lane.split("-")[0]


@dataclass(frozen=True)
class Cut:
    start: float  # seconds on the audio timeline
    length: float
    at: float  # the note's time
    free: float  # seconds until the next other note


class DrumStem:
    """A song's drum stems mixed into one, decoded once."""

    def __init__(self, folder: Path):
        stems = SongFolder.open(folder).drum_stems()
        self.sr = audio_io.SAMPLE_RATE
        self.audio = audio_io.mix(stems) if stems else np.zeros((0, 2), np.float32)
        mono = np.abs(self.audio).mean(axis=1) if len(stems) else np.zeros(0, np.float32)
        hop = self.sr // 1000  # about 1 ms
        self.fps = self.sr / hop
        n = len(mono) // hop
        self.env = mono[: n * hop].reshape(n, hop).max(axis=1) if n else np.zeros(0, np.float32)

    @property
    def available(self) -> bool:
        return len(self.audio) > 0

    def candidates(self, notes: list[tuple[float, str]], wanted: str, limit: int = 12) -> list[Cut]:
        """Cuts for `wanted`'s notes, best first. `notes`: (seconds, lane) of every Expert
        note on the audio timeline."""
        times = np.array(sorted(t for t, _ in notes))
        found = []  # (others at the same time, free, room before, level, cut)
        for at, lane in notes:
            if voice(lane) != wanted:
                continue
            d = times - at
            others = int(np.sum(np.abs(d) < TOGETHER)) - 1
            after, before = d[d >= TOGETHER], -d[d <= -TOGETHER]
            free = float(after.min()) if len(after) else MAX_SECONDS[wanted]
            room = float(before.min()) if len(before) else 1.0
            cut = self.cut(at, wanted, free)
            if cut:
                found.append((others, free, room, self.level(cut.start + PRE_ROLL), cut))
        if not found:
            return []
        levels = np.array([f[3] for f in found])
        rank = np.argsort(np.argsort(levels)) / max(1, len(found) - 1)  # 0 = quietest, 1 = loudest

        def score(i: int) -> float:
            _, free, room, _, _ = found[i]
            return (0.5 * min(free, MAX_SECONDS[wanted]) / MAX_SECONDS[wanted]
                    + 0.35 * (1 - min(1.0, abs(rank[i] - 0.7) / 0.5))
                    + 0.15 * min(room, 0.15) / 0.15)

        order = sorted(range(len(found)), key=lambda i: (found[i][0], -score(i)))
        return [found[i][4] for i in order[:limit]]

    def cut(self, at: float, wanted: str, free: float) -> Cut | None:
        onset = self.onset(at)
        if onset is None:
            return None
        start = max(0.0, onset - PRE_ROLL)
        following = self.onset(at + free) if free < MAX_SECONDS[wanted] else None  # charts sit a few ms off
        stop = min(at + free, following if following and following > onset else at + free) - 0.005
        limit = min(MAX_SECONDS[wanted], stop - start)
        return Cut(round(start, 4), round(max(0.05, min(limit, self.ring(onset))), 4), at, round(free, 4))

    def level(self, onset: float) -> float:
        """The hit's peak in its first 30 ms."""
        a = round(onset * self.fps)
        return float(self.env[a:a + round(0.03 * self.fps)].max(initial=0.0))

    def onset(self, at: float, window: float = 0.04) -> float | None:
        """Where the hit near `at` starts in the drum stem: the first sample above 20 % of
        its peak, searched from the last quiet millisecond before it."""
        a, b = max(0, round((at - window) * self.fps)), min(len(self.env), round((at + window) * self.fps))
        if b - a < 2:
            return None
        seg = self.env[a:b]
        peak = int(np.argmax(seg))
        if seg[peak] <= 1e-4:
            return None
        quiet = np.nonzero(seg[:peak] < 0.25 * seg[peak])[0]
        frame = a + (quiet[-1] + 1 if len(quiet) else 0)
        # To the sample: the first one above 20 % of the hit's peak, from a frame before.
        hop = self.sr / self.fps
        lo, hi = int(max(0, frame - 1) * hop), int((a + peak + 1) * hop)
        mono = np.abs(self.audio[lo:hi]).mean(axis=1)
        above = np.nonzero(mono >= 0.2 * mono.max())[0] if len(mono) else []
        return (lo + (above[0] if len(above) else 0)) / self.sr

    def ring(self, onset: float) -> float:
        """How long the hit from `onset` rings: until 10-frame blocks are 40 dB below its peak."""
        frames = self.env[round(onset * self.fps):round((onset + 2.0) * self.fps)]
        n = len(frames) // 10
        if not n:
            return 0.05
        level = frames[: n * 10].reshape(n, 10).max(axis=1)
        peak = int(np.argmax(level[:5]))
        below = np.nonzero(level[peak:] < level[peak] * 0.01)[0]
        return ((peak + below[0]) if len(below) else n) * 10 / self.fps

    def render(self, cut: Cut) -> np.ndarray:
        a = int(cut.start * self.sr)
        out = self.audio[a:a + int(cut.length * self.sr)].copy()
        n = len(out)
        fade_in = min(n, int(FADE_IN * self.sr))
        out[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
        fade = min(n, int(min(FADE, cut.length / 4) * self.sr))
        out[n - fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        return out

    def wav(self, cut: Cut) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, self.render(cut), self.sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()


def picks(folder: Path) -> dict[str, dict]:
    """The sound of each voice that has one: {"start", "length"} for one Trackyard cut,
    {"file": name} for a file put there by hand."""
    song = SongFolder.open(folder)
    stored = song.sidecar().get("sounds", {})
    out = {}
    for v in VOICES:
        file = next((folder / DIR / f"{v}{ext}" for ext in EXTENSIONS if (folder / DIR / f"{v}{ext}").exists()), None)
        if file is None:
            continue
        out[v] = stored[v] if v in stored and file.suffix == ".wav" else {"file": file.name}
    return out


def save(folder: Path, changes: dict[str, dict | None], stem: DrumStem) -> None:
    """Write or remove sounds: voice → {"start", "length"} cuts it from the drum stem,
    None removes the voice's file."""
    folder = folder.resolve()
    for v, pick in changes.items():
        if v not in VOICES:
            raise ValueError(f"unknown voice {v!r}")
        for ext in EXTENSIONS:
            (folder / DIR / f"{v}{ext}").unlink(missing_ok=True)
        if pick is not None:
            if not stem.available:
                raise ValueError("this song has no drum stem to cut sounds from")
            cut = Cut(float(pick["start"]), min(float(pick["length"]), MAX_SECONDS[v]), 0.0, 0.0)
            if cut.start < 0 or cut.length <= 0:
                raise ValueError(f"bad sound for {v}")
            (folder / DIR).mkdir(exist_ok=True)
            (folder / DIR / f"{v}.wav").write_bytes(stem.wav(cut))
    if (folder / DIR).is_dir() and not any((folder / DIR).iterdir()):
        (folder / DIR).rmdir()
    song = SongFolder.open(folder)
    stored = {**song.sidecar().get("sounds", {}),
              **{v: {"start": float(p["start"]), "length": float(p["length"])} for v, p in changes.items() if p}}
    song.update_sidecar(sounds={v: p for v, p in stored.items() if changes.get(v, p) is not None})
