#!/usr/bin/env python3
"""Evaluate automatic drum charts against hand-made ones.

For every Clone Hero song folder found under the given paths:

1. mix all stems into one file (simulates a finished song without stems),
2. transcribe drums with ADTOF from (a) the mix, (b) a Demucs drum stem of the mix,
   and (c) the song's original drum stem when there is one (upper bound),
3. track beats and downbeats on the mix with Beat This!,
4. build the full Trackyard chart (tempo map, snapped notes, lanes), write it as
   notes.mid and read it back ("chart" source),
5. compare all of it with the Expert drum chart and its tempo map.

Audio and model outputs are cached per song under --cache; reruns only redo what's missing.

    python scripts/evaluate.py ~/Music/chart-tests ../Beat-Train/Songs
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import mir_eval
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trackyard import analysis  # noqa: E402
from trackyard import audio as audio_io  # noqa: E402
from trackyard.chart import CLASSES, ReferenceChart, load_chart  # noqa: E402
from trackyard.difficulty import LEVELS, all_levels, preset_settings  # noqa: E402
from trackyard.export import write_midi  # noqa: E402
from trackyard.metrics import EMPTY, OnsetScore, clip, match_onsets, merge_close, score_beats  # noqa: E402
from trackyard.pipeline import Analysis, analyze, build_chart  # noqa: E402
from trackyard.songfolder import SongFolder, find_songs  # noqa: E402

ADTOF_SOURCES = ("mix", "demucs", "stem")
SOURCES = (*ADTOF_SOURCES, "chart")
SOURCE_LABEL = {
    "mix": "ADTOF on the full mix",
    "demucs": "ADTOF on Demucs drums from the mix",
    "stem": "ADTOF on the original drum stem (upper bound)",
    "chart": "finished Trackyard chart (snapped to the fitted tempo map)",
}
LANES = ("kick", "red", "yellow-cym", "yellow-tom", "blue-cym", "blue-tom", "green-cym", "green-tom")
COLORS = ("kick", "red", "yellow", "blue", "green")
CONF_BINS = (0.0, 0.3, 0.5, 0.7, 0.9, 1.01)
SWEEP = np.round(np.arange(0.10, 0.71, 0.05), 2)

Estimates = dict[str, list[tuple[float, float]]]  # class → [(time, confidence)]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def prepare(song: SongFolder, cache: Path, sources: list[str], device: str) -> Analysis:
    d = cache / song.cache_key
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.txt").write_text(str(song.path) + "\n")
    adtof = tuple(s for s in ADTOF_SOURCES if s in sources or (s in ("mix", "demucs") and "chart" in sources))
    return analyze(d, lambda: audio_io.mix(list(song.stems().values())), device, adtof, song.drum_stems(), log)


def chart_estimates(a: Analysis, tempo: str) -> tuple[ReferenceChart, object, Path]:
    """Build the Trackyard chart with all levels, write notes.mid and read Expert back like any chart."""
    chart = build_chart(a, tempo)
    out = a.dir / "auto"
    # The charter-like rules: the lower levels are compared with hand-made ones.
    levels = all_levels(chart.notes, chart.tempo_map.ppq, set(chart.fit.downbeat_ticks),
                        preset_settings("clone-hero"))
    write_midi(out / "notes.mid", "auto", chart.tempo_map, chart.notes, chart.fit.beat_ticks, chart.fit.downbeat_ticks,
               {k: v for k, v in levels.items() if k != "expert"})
    return load_chart(out, {}), chart.fit, out


def score_level(song: SongFolder, auto_dir: Path, level: str) -> OnsetScore | None:
    """Generated vs hand-made chart of one lower level: lane must match, ±50 ms."""
    try:
        hand = load_chart(song.path, song.ini, level)
    except Exception:
        return None
    if not hand.hits:
        return None
    ours = load_chart(auto_dir, {}, level)
    total = EMPTY
    for lane in {h.lane for h in hand.hits} | {h.lane for h in ours.hits}:
        total = total + match_onsets([h.time for h in hand.hits if h.lane == lane],
                                     [h.time for h in ours.hits if h.lane == lane], 0.05)
    return total


def score_song(ref: ReferenceChart, est: Estimates, lo: float, hi: float, window: float, shift: float = 0.0):
    """Per-class scores plus the class-agnostic 'ALL' score; also (conf, matched) per estimate.

    `shift` (seconds) is subtracted from every estimate, to score with a song's sync offset removed.
    """
    est = {c: [(t - shift, conf) for t, conf in v] for c, v in est.items()}
    scores, conf = {}, []
    for cls in CLASSES:
        r = clip([h.time for h in ref.hits if h.cls == cls], lo, hi)
        e = [(t, c) for t, c in est.get(cls, []) if lo <= t <= hi]
        scores[cls] = match_onsets(r, [t for t, _ in e], window)
        if len(r) and len(e):
            matched = {j for _, j in mir_eval.util.match_events(np.sort(r), np.array([t for t, _ in e]), window)}
            conf += [(c, j in matched) for j, (_, c) in enumerate(e)]
        else:
            conf += [(c, False) for _, c in e]
    all_ref = merge_close(clip([h.time for h in ref.hits], lo, hi))
    all_est = merge_close([t for v in est.values() for t, _ in v if lo <= t <= hi])
    scores["ALL"] = match_onsets(all_ref, all_est, window)
    return scores, conf


def score_lanes(ref: ReferenceChart, auto: ReferenceChart, lo: float, hi: float) -> dict[str, OnsetScore]:
    """F@50 per chart lane (pro drums) and per colour (what Beat-Train's highway shows)."""
    out = {}
    for key, names in [(lane, (lane,)) for lane in LANES] + [
        (c, tuple(lane for lane in LANES if lane.split("-")[0] == c)) for c in COLORS[2:]
    ]:
        r = clip([h.time for h in ref.hits if h.lane in names], lo, hi)
        e = clip([h.time for h in auto.hits if h.lane in names], lo, hi)
        out[key] = match_onsets(r, e, 0.05)
    return out


def fmt(x: float, digits: int = 2) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{digits}f}"


def short(name: str, n: int = 34) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path, help="song folders or folders of song folders")
    ap.add_argument("--cache", type=Path, default=Path("cache"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--sources", default=",".join(SOURCES), help="comma list of " + ",".join(SOURCES))
    ap.add_argument("--tempo", default="auto", help="metrical level for the chart source (auto/half/normal/double)")
    ap.add_argument("--device", default=analysis.best_device())
    ap.add_argument("--only", default="", help="only songs whose folder name contains this text")
    args = ap.parse_args()

    sources = [s for s in args.sources.split(",") if s]
    songs = [s for s in find_songs([r.expanduser() for r in args.roots]) if args.only.lower() in s.path.name.lower()]
    if not songs:
        sys.exit("no song folders (song.ini + notes.mid/notes.chart) found")
    log(f"{len(songs)} songs, sources {sources}, device {args.device}")

    thresholds = analysis.default_thresholds()
    rows, beat_rows = [], []
    totals = {(src, w, c): EMPTY for src in sources for w in (0.05, 0.03) for c in (*CLASSES, "ALL")}
    lane_totals = {k: EMPTY for k in (*LANES, *COLORS[2:])}
    level_totals = {lvl: EMPTY for lvl in ("expert", *LEVELS)}
    conf_all: dict[str, list] = {src: [] for src in sources}
    per_song: list[tuple[SongFolder, ReferenceChart, Analysis, float, float]] = []

    for i, song in enumerate(songs, 1):
        log(f"[{i}/{len(songs)}] {song.path.name}")
        try:
            ref = load_chart(song.path, song.ini)
        except Exception as e:  # unreadable chart: report and go on
            log(f"  skipped: {e}")
            continue
        if not ref.hits:
            log("  skipped: no Expert drum notes")
            continue
        a = prepare(song, args.cache, sources, args.device)
        lo, hi = ref.hits[0].time - 1.0, ref.hits[-1].time + 1.0
        per_song.append((song, ref, a, lo, hi))

        ests: dict[str, Estimates] = {
            src: analysis.pick_onsets(a.acts[src], thresholds) for src in ADTOF_SOURCES if src in sources and src in a.acts
        }
        fit = None
        if "chart" in sources:
            auto, fit, auto_dir = chart_estimates(a, args.tempo)
            ests["chart"] = {c: [(h.time, 1.0) for h in auto.hits if h.cls == c] for c in CLASSES}
            for k, s in score_lanes(ref, auto, lo, hi).items():
                lane_totals[k] = lane_totals[k] + s
            level_scores = {lvl: score_level(song, auto_dir, lvl) for lvl in level_totals}
            for lvl, sc in level_scores.items():
                if sc is not None:
                    level_totals[lvl] = level_totals[lvl] + sc

        for src, est in ests.items():
            for w in (0.05, 0.03):
                scores, conf = score_song(ref, est, lo, hi, w)
                if w == 0.05:
                    conf_all[src] += conf
                for cls, s in scores.items():
                    totals[(src, w, cls)] = totals[(src, w, cls)] + s
                    rows.append(_row(song, src, w, cls, False, s, s.median_offset_ms))
            # Same song with its own median sync offset removed (what a global offset slider would fix).
            shift = score_song(ref, est, lo, hi, 0.1)[0]["ALL"].median_offset_ms / 1000
            shift = 0.0 if np.isnan(shift) else shift
            for w in (0.05, 0.03):
                s = score_song(ref, est, lo, hi, w, shift)[0]["ALL"]
                rows.append(_row(song, src, w, "ALL", True, s, shift * 1000))

        rb, rd = clip(ref.beats, lo, hi), clip(ref.downbeats, lo, hi)
        raw = score_beats(rb, clip(a.beats, lo - 0.1, hi + 0.1), rd, clip(a.downbeats, lo - 0.1, hi + 0.1))
        row = {"song": song.path.name, "tempo_changes": len(ref.tempo_map.tempos),
               **{f"raw_{k}": round(v, 4) for k, v in vars(raw).items()}}
        if fit is not None:
            gb = [fit.tempo_map.seconds(t) for t in fit.beat_ticks]
            gd = [fit.tempo_map.seconds(t) for t in fit.downbeat_ticks]
            grid = score_beats(rb, clip(gb, lo - 0.1, hi + 0.1), rd, clip(gd, lo - 0.1, hi + 0.1))
            r, e = np.array(rb), np.array(clip(gb, lo - 0.1, hi + 0.1))
            row.update({f"grid_{k}": round(v, 4) for k, v in vars(grid).items()})
            row.update(grid_f30=round(mir_eval.beat.f_measure(r, e, 0.03), 4), grid_tempos=len(fit.tempo_map.tempos),
                       grid_factor=fit.factor,
                       tempo_match=bool(abs(grid.est_bpm / grid.ref_bpm - 1) < 0.04),
                       **{f"{lvl}_f": round(sc.f, 4) for lvl, sc in level_scores.items() if sc is not None})
        beat_rows.append(row)

    sweep = threshold_sweep(per_song, [s for s in sources if s in ADTOF_SOURCES], thresholds)
    write_outputs(args, sources, [p[0] for p in per_song], rows, beat_rows, totals, lane_totals, conf_all, sweep,
                  thresholds, level_totals)


def _row(song, src, w, cls, corrected, s: OnsetScore, offset_ms) -> dict:
    return {
        "song": song.path.name, "source": src, "window_ms": int(w * 1000), "class": cls, "corrected": corrected,
        "n_ref": s.n_ref, "n_est": s.n_est, "tp": s.tp,
        "precision": round(s.precision, 4), "recall": round(s.recall, 4), "f": round(s.f, 4),
        "median_offset_ms": round(offset_ms, 1),
    }


def threshold_sweep(per_song, sources, thresholds):
    """Global per-class threshold sweep (does a different ADTOF threshold help?)."""
    sweep: dict[tuple[str, str], tuple[float, float]] = {}
    for src in sources:
        for cls in CLASSES:
            best = (thresholds[cls], -1.0)
            for th in SWEEP:
                tot = EMPTY
                for _, ref, a, lo, hi in per_song:
                    if src not in a.acts:
                        continue
                    est = analysis.pick_onsets(a.acts[src], {**thresholds, cls: float(th)})[cls]
                    r = clip([h.time for h in ref.hits if h.cls == cls], lo, hi)
                    tot = tot + match_onsets(r, [t for t, _ in est if lo <= t <= hi], 0.05)
                if tot.f > best[1]:
                    best = (float(th), tot.f)
            sweep[(src, cls)] = best
    return sweep


def write_outputs(args, sources, songs_used, rows, beat_rows, totals, lane_totals, conf_all, sweep, thresholds,
                  level_totals):
    run = args.out / dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    for name, data in (("onsets.csv", rows), ("beats.csv", beat_rows)):
        with open(run / name, "w", newline="") as f:
            fields = list(dict.fromkeys(k for r in data for k in r))
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)

    lookup = {(r["song"], r["source"], r["window_ms"], r["class"], r["corrected"]): r for r in rows}
    md: list[str] = [f"# Trackyard evaluation — {dt.date.today()}", ""]
    md += [f"{len(songs_used)} songs. ADTOF default thresholds "
           + ", ".join(f"{c} {thresholds[c]:.2f}" for c in CLASSES)
           + f"; chart tempo option `{args.tempo}`. F-measure per class at ±50 ms; ALL = any drum onset regardless "
           "of class. Only the chart's span (first to last Expert drum note, ±1 s) is scored.", ""]
    md += ["Sources: " + "; ".join(f"`{s}` = {SOURCE_LABEL[s]}" for s in sources), ""]

    md += ["## Per class, all songs (micro average)", ""]
    md += ["| Source | Class | Ref notes | Est | P@50 | R@50 | F@50 | F@30 | Median offset ms |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for src in sources:
        for cls in (*CLASSES, "ALL"):
            s50, s30 = totals[(src, 0.05, cls)], totals[(src, 0.03, cls)]
            if not s50.n_ref and not s50.n_est:
                continue
            md.append(f"| {src} | {cls} | {s50.n_ref} | {s50.n_est} | {fmt(s50.precision)} | {fmt(s50.recall)} | "
                      f"**{fmt(s50.f)}** | {fmt(s30.f)} | {fmt(s50.median_offset_ms, 0)} |")
    md += [""]

    if "chart" in sources:
        md += ["## Finished chart per lane (F@50, all songs)", ""]
        md += ["Pro-drums lanes, then colours only (cymbal/tom ignored, as on Beat-Train's highway).", ""]
        md += ["| Lane | Ref notes | Est | P | R | F |", "|---|---:|---:|---:|---:|---:|"]
        for k, s in lane_totals.items():
            if s.n_ref or s.n_est:
                md.append(f"| {k} | {s.n_ref} | {s.n_est} | {fmt(s.precision)} | {fmt(s.recall)} | **{fmt(s.f)}** |")
        md += [""]

    if "chart" in sources:
        md += ["## Finished chart per difficulty (vs. the hand-made level, same lane, ±50 ms)", ""]
        md += ["| Level | Ref notes | Est | P | R | F |", "|---|---:|---:|---:|---:|---:|"]
        for lvl, s in level_totals.items():
            if s.n_ref:
                md.append(f"| {lvl.title()} | {s.n_ref} | {s.n_est} | {fmt(s.precision)} | {fmt(s.recall)} | **{fmt(s.f)}** |")
        matches = [b["tempo_match"] for b in beat_rows if "tempo_match" in b]
        md += ["", f"Chart tempo (metrical level) matches the hand chart within 4 % in {sum(matches)} of "
               f"{len(matches)} songs.", ""]

    for src in sources:
        md += [f"## Per song — {SOURCE_LABEL[src]} (F@50)", ""]
        md += ["| Song | " + " | ".join(CLASSES) + " | ALL | ALL@30 | Offset ms | ALL synced | ALL@30 synced |",
               "|---|" + "---:|" * (len(CLASSES) + 5)]
        for song in songs_used:
            name = song.path.name
            if (name, src, 50, "ALL", False) not in lookup:
                continue
            cells = []
            for cls in CLASSES:
                r = lookup[(name, src, 50, cls, False)]
                cells.append(fmt(r["f"]) + ("" if r["n_ref"] else "†"))
            a50, a30 = lookup[(name, src, 50, "ALL", False)], lookup[(name, src, 30, "ALL", False)]
            c50, c30 = lookup[(name, src, 50, "ALL", True)], lookup[(name, src, 30, "ALL", True)]
            md.append(f"| {short(name)} | " + " | ".join(cells)
                      + f" | **{fmt(a50['f'])}** | {fmt(a30['f'])} | {fmt(a50['median_offset_ms'], 0)} "
                      + f"| {fmt(c50['f'])} | {fmt(c30['f'])} |")
        md += ["", "† class absent from the chart (any estimate there is a false positive; F is 0 or –). "
               "*Synced*: the song's median offset removed first, as a global offset setting would.", ""]

    adtof = [s for s in sources if s in ADTOF_SOURCES]
    if adtof:
        md += ["## Precision by confidence (ADTOF activation at the picked peak, ±50 ms)", ""]
        md += ["| Source | " + " | ".join(f"{a:.1f}–{min(b, 1.0):.1f}" for a, b in zip(CONF_BINS, CONF_BINS[1:])) + " |",
               "|---|" + "---:|" * (len(CONF_BINS) - 1)]
        for src in adtof:
            cells = []
            for a, b in zip(CONF_BINS, CONF_BINS[1:]):
                sel = [m for c, m in conf_all[src] if a <= c < b]
                cells.append(f"{fmt(sum(sel) / len(sel)) if sel else '–'} (n={len(sel)})")
            md.append(f"| {src} | " + " | ".join(cells) + " |")
        md += [""]

        md += ["## Best global threshold per class (F@50, all songs)", ""]
        md += ["| Source | " + " | ".join(CLASSES) + " |", "|---|" + "---:|" * len(CLASSES)]
        for src in adtof:
            md.append(f"| {src} | " + " | ".join(
                f"{sweep[(src, c)][0]:.2f} → {fmt(sweep[(src, c)][1])} (default {fmt(totals[(src, 0.05, c)].f)})"
                for c in CLASSES) + " |")
        md += [""]

    md += ["## Beats and tempo", ""]
    md += ["*Raw*: Beat This! on the full mix. *Grid*: the fitted tempo map of the finished chart.", ""]
    md += ["| Song | Chart tempos | Grid tempos | Chart BPM | Raw BPM | Grid BPM | Raw F@70 | Raw AMLt | "
           "Grid F@30 | Grid AMLt | Downbeat F@70 | Grid offset ms |",
           "|---|" + "---:|" * 11]
    for b in beat_rows:
        md.append(f"| {short(b['song'])} | {b['tempo_changes']} | {b.get('grid_tempos', '–')} | "
                  f"{fmt(b['raw_ref_bpm'], 1)} | {fmt(b['raw_est_bpm'], 1)} | {fmt(b.get('grid_est_bpm'), 1)} | "
                  f"**{fmt(b['raw_f70'])}** | {fmt(b['raw_amlt'])} | {fmt(b.get('grid_f30'))} | "
                  f"{fmt(b.get('grid_amlt'))} | {fmt(b.get('grid_downbeat_f70', b['raw_downbeat_f70']))} | "
                  f"{fmt(b.get('grid_median_offset_ms'), 0)} |")
    md += ["", "AMLt accepts double/half tempo and off-beat phase; CMLt (in beats.csv) does not.", ""]

    (run / "report.md").write_text("\n".join(md))
    print("\n".join(md))
    log(f"\nwrote {run}/report.md, onsets.csv, beats.csv")


if __name__ == "__main__":
    main()
