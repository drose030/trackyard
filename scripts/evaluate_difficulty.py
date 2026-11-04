#!/usr/bin/env python3
"""How close are Trackyard's Hard/Medium/Easy reductions to hand-made ones?

Applies `trackyard.difficulty.reduce` to each song's hand-made Expert chart and
compares the result with the same song's hand-made Hard, Medium and Easy charts
(per lane, exact tick match; reported as F-measure). "Expert as-is" is the baseline:
what you'd get by not reducing at all.

    python scripts/evaluate_difficulty.py ../Beat-Train/Songs
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trackyard.chart import ReferenceChart, load_chart  # noqa: E402
from trackyard.difficulty import LEVELS, reduce  # noqa: E402
from trackyard.notes import ChartNote  # noqa: E402
from trackyard.songfolder import find_songs  # noqa: E402


def chart_notes(ref: ReferenceChart) -> list[ChartNote]:
    tm = ref.tempo_map
    return [ChartNote(round(tm.tick_at(h.time - ref.offset)), h.lane, 1.0, h.time, h.cls) for h in ref.hits]


def f_measure(ref: Counter, est: Counter) -> tuple[float, int, int]:
    tp = sum((ref & est).values())
    n_ref, n_est = sum(ref.values()), sum(est.values())
    f = 2 * tp / (n_ref + n_est) if n_ref + n_est else float("nan")
    return f, n_ref, n_est


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path)
    args = ap.parse_args()

    totals = {lvl: [Counter(), Counter(), Counter()] for lvl in LEVELS}  # ref, ours, expert as-is
    print("| Song | " + " | ".join(f"{lvl.title()} F (baseline) | notes ours/hand" for lvl in LEVELS) + " |")
    print("|---|" + "---:|---:|" * len(LEVELS))
    for song in find_songs([r.expanduser() for r in args.roots]):
        expert = load_chart(song.path, song.ini, "expert")
        notes = chart_notes(expert)
        ppq = expert.tempo_map.ppq
        beat_ticks, downs = expert.tempo_map.beats(max((n.tick for n in notes), default=0))
        downbeats = {t for t, d in zip(beat_ticks, downs) if d}
        cells = []
        for lvl in LEVELS:
            hand = chart_notes(load_chart(song.path, song.ini, lvl))
            if not hand:
                cells.append("– | –")
                continue
            ref = Counter((n.tick, n.lane) for n in hand)
            ours = Counter((n.tick, n.lane) for n in reduce(notes, lvl, ppq, downbeats))
            base = Counter((n.tick, n.lane) for n in notes)
            for c, part in zip(totals[lvl], (ref, ours, base)):
                c.update(part)
            f, n_ref, n_est = f_measure(ref, ours)
            fb, _, _ = f_measure(ref, base)
            cells.append(f"**{f:.2f}** ({fb:.2f}) | {n_est}/{n_ref}")
        print(f"| {song.path.name[:34]} | " + " | ".join(cells) + " |")

    print()
    print("| Level | F ours | F Expert as-is | Notes ours / hand |")
    print("|---|---:|---:|---:|")
    for lvl, (ref, ours, base) in totals.items():
        f, n_ref, n_est = f_measure(ref, ours)
        fb, _, _ = f_measure(ref, base)
        print(f"| {lvl.title()} | **{f:.2f}** | {fb:.2f} | {n_est / max(n_ref, 1):.2f} |")


if __name__ == "__main__":
    main()
