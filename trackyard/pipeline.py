"""The conversion pipeline, shared by `trackyard convert` and the evaluation.

    audio → mix.wav → Demucs drums → ADTOF (mix + drums) → Beat This! beats
          → tempo map → snapped notes on lanes → song folder

Every model step caches its output in one folder per input, so reruns (other tempo
options, a new export) skip the slow parts.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import analysis, chartfile
from . import audio as audio_io
from .difficulty import DEFAULT_PRESET, all_levels, preset_settings
from .export import folder_name, read_tags, write_ini, write_ogg
from .lanes import Context
from .notes import ChartNote, build_notes
from .progress import Progress, demucs_segments, model_downloads
from .songfolder import songs_dir, write_sidecar
from .tempo import TempoFit, fit_tempo_map
from .tempomap import TempoMap

# Kicks come from the Demucs stem unless it found far fewer than the full mix: Demucs
# moves 808 kicks into its bass stem. Per song, because on real kits Demucs kicks are
# better (docs/evaluation.md).
MIX_KICK_RATIO = 0.6
# ADTOF thresholds for charts: lower than ADTOF's defaults (TT 0.32, CY 0.30) for toms
# and cymbals, which recall too little; tuned on all three test sets.
CHART_THRESHOLDS = {"TT": 0.20, "CY": 0.20}
ANCHOR_CONFIDENCE = 0.6


@dataclass
class Analysis:
    dir: Path
    mix_path: Path
    drums_path: Path | None
    acts: dict[str, np.ndarray]  # "mix", "demucs", "stem" → ADTOF activations
    beats: list[float]
    downbeats: list[float]


def analyze(cache_dir: Path, make_mix: Callable[[], np.ndarray], device: str,
            sources: tuple[str, ...] = ("mix", "demucs"), drum_stems: list[Path] | None = None,
            log: Callable[[str], None] = print, progress: Progress | None = None) -> Analysis:
    progress = progress or Progress()
    with model_downloads(progress):
        return _analyze(cache_dir, make_mix, device, sources, drum_stems, log, progress)


def _analyze(d: Path, make_mix, device, sources, drum_stems, log, progress: Progress) -> Analysis:
    d.mkdir(parents=True, exist_ok=True)
    mix_path = d / "mix.wav"
    if not mix_path.exists():
        progress("Reading the audio")
        audio_io.write(mix_path, make_mix())
    mix = None

    paths = {"mix": mix_path}
    if "demucs" in sources:
        paths["demucs"] = d / "demucs_drums.wav"
        if not paths["demucs"].exists():
            t = time.time()
            progress("Separating the drums (Demucs; on a CPU this takes a few minutes)", 0.0)
            mix = audio_io.read(mix_path)
            with demucs_segments(progress):
                audio_io.write(paths["demucs"], analysis.separate_drums(mix, device))
            log(f"  demucs {time.time() - t:.0f}s")
    if "stem" in sources and drum_stems:
        paths["stem"] = d / "drum_stem.wav"
        if not paths["stem"].exists():
            audio_io.write(paths["stem"], audio_io.mix(drum_stems))

    acts = {}
    for name, p in paths.items():
        if name not in sources:
            continue
        npy = d / f"adtof_{name}.npy"
        if not npy.exists():
            t = time.time()
            progress(f"Finding the drum hits (ADTOF, {name})", 0.0)
            np.save(npy, analysis.adtof_activations(p, progress=progress.fraction))
            log(f"  adtof[{name}] {time.time() - t:.0f}s")
        acts[name] = np.load(npy)

    beats_json = d / "beats_v2.json"  # v2: sub-frame refined
    if not beats_json.exists():
        t = time.time()
        progress("Finding the beat (Beat This!)")
        mix = mix if mix is not None else audio_io.read(mix_path)
        beats, downs = analysis.track_beats(mix, device)
        beats_json.write_text(json.dumps({"beats": beats, "downbeats": downs}))
        log(f"  beat_this {time.time() - t:.0f}s")
    b = json.loads(beats_json.read_text())
    return Analysis(d, mix_path, paths.get("demucs"), acts, b["beats"], b["downbeats"])


@dataclass
class Chart:
    fit: TempoFit
    notes: list[ChartNote]

    @property
    def tempo_map(self) -> TempoMap:
        return self.fit.tempo_map


def detect_onsets(a: Analysis) -> dict[str, list[tuple[float, float]]]:
    """The drum hits a chart is built from: {class: [(time, confidence)]}."""
    thresholds = {**analysis.default_thresholds(), **CHART_THRESHOLDS}
    drum_src = "demucs" if "demucs" in a.acts else "mix"
    onsets = analysis.pick_onsets(a.acts[drum_src], thresholds)
    if drum_src != "mix":
        mix_kicks = analysis.pick_onsets(a.acts["mix"], thresholds)["KD"]
        if len(onsets["KD"]) < MIX_KICK_RATIO * len(mix_kicks):
            onsets["KD"] = mix_kicks
    return onsets


def build_chart(a: Analysis, tempo: str = "auto", triplets: bool = True, finest: int = 32,
                learned_lanes: bool = True) -> Chart:
    onsets = detect_onsets(a)
    anchors = sorted(t for cls in ("KD", "SD") for t, c in onsets[cls] if c >= ANCHOR_CONFIDENCE)
    hats = [t for t, _ in onsets["HH"]]
    fit = fit_tempo_map(a.beats, a.downbeats, tempo, anchors=anchors, hats=hats)
    drums = audio_io.read(a.drums_path) if a.drums_path else None
    lane_ctx = None
    if learned_lanes and drums is not None and "demucs" in a.acts:
        lane_ctx = Context(drums.mean(1), audio_io.SAMPLE_RATE, a.acts["demucs"],
                           {c: np.array(sorted(t for t, _ in v)) for c, v in onsets.items()},
                           np.array(a.beats), np.array(a.downbeats))
    notes = build_notes(onsets, fit.tempo_map, drums, audio_io.SAMPLE_RATE, triplets, finest, lane_ctx)
    return Chart(fit, notes)


def file_cache_key(path: Path) -> str:
    h = hashlib.sha1()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-")[:48]
    return f"{slug}-{h.hexdigest()[:8]}"


def convert(src: Path, out_root: Path, cache: Path, device: str, tempo: str = "auto",
            taken: set[str] | None = None,
            triplets: bool = True, finest: int = 32, stems: bool = True, lower: bool = True,
            learned_lanes: bool = True, levels_preset: str = DEFAULT_PRESET, progress: Progress | None = None,
            log: Callable[[str], None] = print) -> Path:
    """One audio file → a playable song folder under `out_root`. Returns the folder.

    `taken`: folder names already written in this run; a clash gets " (2)", " (3)", …
    """
    progress = progress or Progress()
    a = analyze(cache / file_cache_key(src), lambda: audio_io.decode(src), device, log=log, progress=progress)
    progress("Writing the chart")
    chart = build_chart(a, tempo, triplets, finest, learned_lanes)
    info, cover = read_tags(src)
    mix = audio_io.read(a.mix_path)
    info.length_ms = int(len(mix) / audio_io.SAMPLE_RATE * 1000)

    name = base = folder_name(info)
    n = 2
    while taken is not None and name in taken:
        name, n = f"{base} ({n})", n + 1
    if taken is not None:
        taken.add(name)
    folder = out_root / name
    folder.mkdir(parents=True, exist_ok=True)
    level_settings = preset_settings(levels_preset)
    levels = all_levels(chart.notes, chart.tempo_map.ppq, set(chart.fit.downbeat_ticks), level_settings) if lower else {}
    chartfile.create(folder, info.name, chart.tempo_map,
                     {lvl: [(n.tick, n.lane) for n in notes] for lvl, notes in {**levels, "expert": chart.notes}.items()},
                     chart.fit.beat_ticks, chart.fit.downbeat_ticks)
    write_ini(folder / "song.ini", info, chart.notes)
    if stems and a.drums_path:
        drums = audio_io.read(a.drums_path)
        n = min(len(mix), len(drums))
        write_ogg(folder / "drums.ogg", drums[:n], audio_io.SAMPLE_RATE)
        write_ogg(folder / "song.ogg", mix[:n] - drums[:n], audio_io.SAMPLE_RATE)  # sums back to the mix
    else:
        write_ogg(folder / "song.ogg", mix, audio_io.SAMPLE_RATE)
    if cover:
        (folder / f"album{cover[1]}").write_bytes(cover[0])
    write_sidecar(folder, chart.notes, {
        "source": src.name, "tempo": tempo, "tempo_factor": chart.fit.factor, "bpm": round(chart.fit.bpm, 2),
        "triplets": triplets, "finest": finest, "learned_lanes": learned_lanes,
    }, level_settings if lower else None)
    return folder


def copy_to_sd(folder: Path, sd_root: Path) -> Path:
    """Copy a song folder to `<sd>/BeatTrain/Songs/` (where Beat-Train on the MPC looks)."""
    dest = songs_dir(sd_root, sd_card=True) / folder.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(folder, dest)
    return dest
