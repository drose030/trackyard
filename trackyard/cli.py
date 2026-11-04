"""Command line: `trackyard convert song.mp3 [...]` and `trackyard edit [folder]`."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import analysis
from .difficulty import DEFAULT_PRESET, PRESETS
from .pipeline import convert, copy_to_sd


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="trackyard", description="Turn songs into Clone Hero / Beat-Train drum charts.")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="audio file(s) → song folder(s)")
    c.add_argument("inputs", nargs="+", type=Path, help="MP3, WAV, OGG or FLAC files")
    c.add_argument("--out", type=Path, default=Path("out"), help="parent folder for song folders (default: out)")
    c.add_argument("--cache", type=Path, default=Path("cache"), help="analysis cache (default: cache)")
    c.add_argument("--tempo", choices=("auto", "half", "normal", "double"), default="auto",
                   help="metrical level relative to the detected beat; auto doubles when hi-hats run in 16ths")
    c.add_argument("--grid", type=int, choices=(16, 32), default=32, help="finest straight grid (default 32)")
    c.add_argument("--no-triplets", action="store_true", help="no 1/12 or 1/24 triplet grid")
    c.add_argument("--lane-rules", action="store_true",
                   help="pick cymbal and tom lanes with simple rules instead of the trained models")
    c.add_argument("--levels", choices=PRESETS, default=DEFAULT_PRESET,
                   help="how Hard/Medium/Easy are made: mpc = even steps for the MPC pads (default), "
                        "clone-hero = like hand-made Clone Hero charts")
    c.add_argument("--expert-only", action="store_true", help="no generated Hard/Medium/Easy")
    c.add_argument("--single-audio", action="store_true", help="write the full mix as song.ogg, no drums.ogg")
    c.add_argument("--sd", type=Path, help="also copy to <SD>/BeatTrain/Songs/")
    c.add_argument("--device", default=analysis.best_device())

    e = sub.add_parser("edit", help="open a song folder in the browser editor")
    e.add_argument("folder", type=Path, nargs="?", help="song folder to open (optional: open or convert from the editor)")
    e.add_argument("--cache", type=Path, default=Path("cache"), help="analysis cache for conversions (default: cache)")
    e.add_argument("--port", type=int, default=8765, help="local port (default 8765; 0 picks a free one)")
    e.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = ap.parse_args(argv)

    if args.command == "edit":
        from .editor.server import serve

        serve(args.folder, args.port, open_browser=not args.no_browser, cache=args.cache)
        return

    if args.sd and not args.sd.is_dir():
        sys.exit(f"SD card path not found: {args.sd}")
    failed = 0
    taken: set[str] = set()
    for src in args.inputs:
        if not src.is_file():
            log(f"not a file: {src}")
            failed += 1
            continue
        log(f"{src.name}")
        t = time.time()
        folder = convert(src, args.out, args.cache, args.device, args.tempo, taken,
                         triplets=not args.no_triplets, finest=args.grid, stems=not args.single_audio,
                         lower=not args.expert_only, learned_lanes=not args.lane_rules,
                         levels_preset=args.levels, log=log)
        log(f"  → {folder} ({time.time() - t:.0f}s)")
        if args.sd:
            log(f"  → {copy_to_sd(folder, args.sd)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
