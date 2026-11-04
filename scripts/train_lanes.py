#!/usr/bin/env python3
"""Train the cymbal-lane and tom-lane models from hand-made charts.

For every song folder under the given roots (with a drum chart and a cached Trackyard
analysis, i.e. after `scripts/evaluate.py` ran on it):

1. detect drum hits exactly like `trackyard convert` does,
2. label each detected cymbal/tom with the lane of the hand-charted note within ±50 ms,
3. compute `trackyard.lanes` features.

Then it cross-validates by song (5 folds: a model is always tested on songs it never
saw) against the old hand rules, trains on everything and writes
trackyard/models/lanes.json.

    python scripts/train_lanes.py ~/Music/chart-tests-rock
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trackyard import audio as audio_io  # noqa: E402
from trackyard.chart import read_notes  # noqa: E402
from trackyard.lanes import CYMBAL_LANES, MODEL_PATH, TOM_LANES, Context, cymbal_features, export_gbc, tom_features, tom_pitch  # noqa: E402
from trackyard.pipeline import analyze, detect_onsets  # noqa: E402
from trackyard.songfolder import find_songs  # noqa: E402


def label(times: list[float], ref: list[tuple[float, str]], lanes: tuple[str, ...], window: float = 0.05):
    """Nearest hand-charted note of one of `lanes` within ±window, else None."""
    r = [(t, lane) for t, lane in ref if lane in lanes]
    rt = np.array([t for t, _ in r])
    out = []
    for t in times:
        if not len(rt):
            out.append(None)
            continue
        d = np.abs(rt - t)
        cand = [r[i][1] for i in np.where(d <= window)[0]]
        # A crash or ride beats a hi-hat when both sit on the hit.
        out.append(None if not cand else next((c for c in cand if not c.startswith("yellow")), cand[0]))
    return out


def old_rules(ctx: Context, cy: list[float], tt: list[float]) -> tuple[list[str], list[str]]:
    beat = float(np.median(np.diff(ctx.beats))) if len(ctx.beats) > 1 else 0.5
    a = np.array(cy)
    cym = ["blue-cym" if np.sum(np.abs(a - t) <= beat) > 1 else "green-cym" for t in cy]
    if len(tt) < 6:
        return cym, ["blue-tom"] * len(tt)
    p = np.array([tom_pitch(ctx, t) for t in tt])
    lo, hi = np.quantile(p, [1 / 3, 2 / 3])
    toms = ["yellow-tom" if x > hi else "green-tom" if x < lo else "blue-tom" for x in p]
    return cym, toms


def build(roots: list[Path], cache: Path) -> dict:
    data = {k: [] for k in ("cx", "cy", "cg", "cb", "tx", "ty", "tg", "tb")}
    songs = []
    for s in find_songs(roots):
        try:
            ref_notes = read_notes(s.path, s.ini, "expert")
        except Exception:
            continue
        d = cache / s.cache_key
        if not ref_notes.notes or not (d / "adtof_demucs.npy").exists() or not (d / "beats_v2.json").exists():
            continue
        a = analyze(d, lambda: None, "cpu", log=lambda m: None)
        onsets = detect_onsets(a)
        drums = audio_io.read(a.drums_path)
        ctx = Context(drums.mean(1), audio_io.SAMPLE_RATE, a.acts["demucs"],
                      {c: np.array(sorted(t for t, _ in v)) for c, v in onsets.items()},
                      np.array(a.beats), np.array(a.downbeats))
        tm = ref_notes.tempo_map
        ref = [(tm.seconds(t) + ref_notes.offset, lane) for t, lane in ref_notes.notes]
        cy, tt = list(ctx.onsets["CY"]), list(ctx.onsets["TT"])
        base_c, base_t = old_rules(ctx, cy, tt)
        g = len(songs)
        songs.append(s.path.name)
        for x, labels, base, kx, ky, kg, kb in (
            (cymbal_features(ctx, cy), label(cy, ref, CYMBAL_LANES), base_c, "cx", "cy", "cg", "cb"),
            (tom_features(ctx, tt), label(tt, ref, TOM_LANES), base_t, "tx", "ty", "tg", "tb"),
        ):
            for row, y, b in zip(x, labels, base):
                if y is not None:
                    data[kx].append(row)
                    data[ky].append(y)
                    data[kg].append(g)
                    data[kb].append(b)
        print(f"  {len(songs):3d} {s.path.name[:50]}", flush=True)
    return {k: np.array(v) for k, v in data.items()} | {"songs": np.array(songs)}


def cross_validate(x, y, groups, baseline, name):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import GroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    models = {
        "logistic": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
        "boosted trees": lambda: GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1,
                                                            subsample=0.8, random_state=0),
    }
    print(f"\n{name}: {len(y)} labelled hits from {len(set(groups))} songs; classes "
          + ", ".join(f"{c} {np.mean(y == c):.0%}" for c in sorted(set(y))))
    print(f"  old rules        accuracy {accuracy_score(y, baseline):.3f}  macro-F1 {f1_score(y, baseline, average='macro'):.3f}")
    scores = {}
    for label_, make in models.items():
        pred = np.empty(len(y), dtype=object)
        for train, test in GroupKFold(5).split(x, y, groups):
            pred[test] = make().fit(x[train], y[train]).predict(x[test])
        pred = pred.astype(str)
        acc, f1 = accuracy_score(y, pred), f1_score(y, pred, average="macro")
        scores[label_] = (acc, f1)
        print(f"  {label_:16} accuracy {acc:.3f}  macro-F1 {f1:.3f}   (song-grouped 5-fold)")
    return scores


def transfer(train: dict, test: dict, root: Path) -> None:
    """Train on `train`, score on a different set of songs (other charters, other eras)."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    print(f"\nTransfer to {root} ({len(test['songs'])} songs)")
    for name, kx, ky, kb in (("Cymbals", "cx", "cy", "cb"), ("Toms", "tx", "ty", "tb")):
        if not len(test[ky]):
            print(f"  {name}: no labelled hits")
            continue
        line = f"  {name:8} n={len(test[ky]):5}  old rules {accuracy_score(test[ky], test[kb]):.3f}"
        for label_, model in (("logistic", make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))),
                              ("trees", GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1,
                                                                   subsample=0.8, random_state=0))):
            pred = model.fit(train[kx], train[ky]).predict(test[kx])
            line += f"  {label_} {accuracy_score(test[ky], pred):.3f}"
        print(line)


def main() -> None:
    np.seterr(all="ignore")  # Accelerate's matmul raises spurious FP warnings on Apple Silicon
    import warnings

    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("cache"))
    ap.add_argument("--dataset", type=Path, default=Path("cache/lanes_dataset.npz"))
    ap.add_argument("--rebuild", action="store_true", help="recompute features even if the dataset exists")
    ap.add_argument("--out", type=Path, default=MODEL_PATH)
    ap.add_argument("--test", type=Path, nargs="*", default=[],
                    help="other song roots: train on everything above, report accuracy there (transfer check)")
    args = ap.parse_args()

    if args.dataset.exists() and not args.rebuild:
        data = dict(np.load(args.dataset, allow_pickle=False))
    else:
        data = build([r.expanduser() for r in args.roots], args.cache)
        np.savez(args.dataset, **data)

    cross_validate(data["cx"], data["cy"], data["cg"], data["cb"], "Cymbals (yellow / blue / green)")
    cross_validate(data["tx"], data["ty"], data["tg"], data["tb"], "Toms (yellow / blue / green)")

    from sklearn.ensemble import GradientBoostingClassifier

    for root in args.test:
        test = build([root.expanduser()], args.cache)
        transfer(data, test, root)

    out = {"version": 1, "trained_on": [r.name for r in args.roots], "songs": int(len(data["songs"])), "models": {}}
    for name, x, y in (("cymbal", data["cx"], data["cy"]), ("tom", data["tx"], data["ty"])):
        model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1, subsample=0.8,
                                           random_state=0).fit(x, y)
        out["models"][name] = export_gbc(model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out))
    print(f"\nwrote {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
