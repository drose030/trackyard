# Development

How Trackyard works inside, and how to evaluate, retrain and test it. For using it,
see the [README](../README.md). The roadmap is in [plan.md](plan.md), measured results
in [evaluation.md](evaluation.md).

## Setup

Same requirements as in the README, plus the dev extras (pytest, pyflakes, mir_eval,
scikit-learn):

```bash
uv venv -p 3.11 .venv
uv pip install -p .venv/bin/python -e ".[dev]"
```

The install is about 1 GB, mostly PyTorch. The first run downloads about 160 MB of
model weights (Demucs `htdemucs` and Beat This! `final0`); ADTOF's weights ship with
its package.

`cache/`, `results/` and `out/` are ignored by git. Test songs are copyrighted
recordings and must never be committed.

## How a song is converted

In `trackyard/`:

1. **`pipeline.py`** decodes the audio and caches the analysis per input file (name
   plus content hash) in `cache/`: the mix, the Demucs drum stem, ADTOF activations and
   beats. A second run skips the slow steps.
2. **Separation and transcription:**
   - Demucs separates the drums.
   - ADTOF transcribes from the drum stem. Kicks come from the full mix when Demucs
     loses them, as it does with 808s.
3. **`tempo.py`** fits the tempo map. Beat This! beats are:
   - refined below one frame,
   - repaired (missed or doubled beats),
   - pulled onto confident kick/snare hits,
   - split into constant-tempo segments.

   Time signatures come from the downbeats. `--tempo auto` doubles below 70 BPM, or when
   the hi-hat runs in 16ths and the result stays ≤ 170 BPM, and halves above 190 BPM.
4. **`notes.py`** snaps each beat's hits to the coarsest grid that fits (1/16, 1/24 or
   1/32) and maps the classes to lanes:
   - hi-hat → yellow cymbal,
   - cymbals and toms → their lane, chosen by small trained models (`lanes.py`,
     trained on 118 hand-charted songs with `scripts/train_lanes.py`),
   - at most two hand notes plus kick at a time.
5. **`difficulty.py`** derives Hard, Medium and Easy from Expert by dropping notes,
   never moving them, so the tom markers stay valid. Two presets:
   - `mpc` (default): adjustable controls (density, a share per instrument, finest
     grid, hands, notes at once, shortest gap), set for even steps. Strong beat
     positions survive first; the snare backbeat stays; Medium and Easy use one hand
     plus kick.
   - `clone-hero`: fixed rules tuned to match hand-made charts: hi-hats and kicks thin
     out, fills slow down, Easy is about one hand plus one kick per bar.
6. **`export.py`** writes the song folder: `notes.mid` (pro drums in four difficulties,
   tempo map, time signatures, `BEAT` and `EVENTS` tracks), `song.ini`, the OGG stems,
   the cover and `trackyard.json` (per-note confidence, conversion and difficulty
   settings).

## The editor

`trackyard edit` runs `trackyard/editor/server.py`, a standard-library HTTP server on
127.0.0.1, with plain JS and canvas in `trackyard/editor/static/` (no build step). The
server's module docstring lists the JSON API. Web Audio synthesizes the note ticks and
the metronome; the stems play through `<audio>` elements, served with range requests so
they can seek.

- New songs from Convert MP3 are drafts in `cache/drafts/` until their first save moves
  them into the chosen folder. A new conversion deletes the other unsaved drafts.
- Settings (last save folder, last Save-as folder, dismissed hints) live in
  `~/.trackyard/editor.json`; volumes are kept in the browser's local storage.
- `trackyard/chartedit.py` saves: it replaces only the drum gems and their pro-drums
  markers (tom markers in MIDI, cymbal markers in `.chart`), and the tempo events and
  offset when they changed. Everything else in the file stays. The offset is written as
  `delay` in `song.ini` (and as `Offset` in a `.chart`).
- In the chart file, the Cymbal and Floor tom rows are the green lane as cymbal and as
  tom. Cymbal versus tom is stored per lane and tick for all difficulties at once, so
  the level being edited decides it everywhere.
- Tempo mode finds drum hits in the drum stems in the browser (energy jumps of 8 dB
  within 4 ms) for snapping and the timing dots.

## The desktop app

`desktop/` is an Electron app around the editor. `main.js` starts
`python -m trackyard.cli edit --no-browser --port 0 --exit-with-stdin`, waits for the
address it prints and loads it in a window; `preload.js` gives the editor native file
dialogs. The server's data (analysis cache and drafts in `analysis/`, model weights in `torch/`,
bytecode in `pycache/`) goes to the app's data folder
(`~/Library/Application Support/Trackyard` on a Mac), its log to `logs/server.log`
there. The server quits when the app's pipe to it closes, also after a crash.

```bash
cd desktop
npm install
npm start           # runs against the repo's .venv
npm run dist        # macOS: bundle Python, then build the DMG
```

`npm run dist` runs `scripts/bundle-mac.sh`, which builds `desktop/build/python`: a
copy of uv's standalone Python 3.11 (python-build-standalone, which can be moved
anywhere) with Trackyard installed, and only what a conversion imports:
- librosa and its numba/llvmlite/scikit-learn stack are removed; ADTOF only uses
  `librosa.load` and `librosa.stft`, and `trackyard/librosa_lite.py` stands in for them
  (`tests/test_librosa_lite.py` checks they match; on a whole song the ADTOF hits are
  identical);
- scipy, sympy, networkx, pip, setuptools, PyTorch's headers, compiler and ONNX
  export, and Tk are removed. To check a change, list `sys.modules` after a
  conversion;
- no bytecode: the app sets `PYTHONPYCACHEPREFIX` to its data folder;
- no model weights: the first conversion downloads them into the data folder.

ffmpeg comes from the `imageio-ffmpeg` package when there's none on the `PATH`.
The DMG is about 300 MB, the installed app about 900 MB.

**Releases:** `.github/workflows/desktop.yml` builds the DMG on GitHub. Pushing a tag
`v<version>` sets that version, builds, and attaches the DMG to the tag's release
(created if missing). "Run workflow" in the Actions tab builds without a release and
keeps the DMG for three days as a test build.
electron-builder then writes `desktop/dist/Trackyard-<version>-arm64.dmg`. The build
is unsigned (ad hoc); see [plan.md](plan.md) for signing and the Windows build.

## Evaluate against hand-made charts

Run the evaluation on any folder of Clone Hero song folders. Each folder needs
`song.ini`, `notes.mid` or `notes.chart`, and its audio stems:

```bash
.venv/bin/python scripts/evaluate.py ~/Music/chart-tests ../Beat-Train/Songs
```

For each song, the script:

1. Mixes all stems into one file. This simulates a finished song without stems.
2. Transcribes the drums with ADTOF from three sources:
   - the mix,
   - a Demucs drum stem,
   - the original drum stem, as an upper bound.
3. Builds the finished Trackyard chart (`chart` source).
4. Compares everything with the Expert drum chart:
   - precision, recall and F-measure per class and per lane, at ±50 ms and ±30 ms,
   - beats and downbeats against the chart's tempo map.

`scripts/evaluate_difficulty.py` does the same for the reductions alone: it applies them
to each hand-made Expert chart and compares the result with the hand-made Hard, Medium
and Easy. Both scripts use the `clone-hero` preset for the lower levels, since that is
the one meant to match hand-made charts.

Options: `--only TEXT` filters songs by folder name, and `--sources demucs,chart` picks
sources. Run `--help` for the rest. Reports go to `results/<timestamp>/report.md`
plus CSVs. Summaries worth keeping go into [evaluation.md](evaluation.md) by hand.

## Retrain the lane models

```bash
.venv/bin/python scripts/train_lanes.py ~/Music/chart-tests-rock ~/Music/chart-tests ../Beat-Train/Songs
```

The script:
- needs the songs analysed once by `scripts/evaluate.py` (it reads the cache),
- prints the song-grouped cross-validation against the old rules,
- writes `trackyard/models/lanes.json`; `--test ROOT` also reports transfer to songs it
  wasn't trained on.

scikit-learn is only needed here (dev extra), not for converting: the models are
exported to JSON and evaluated with numpy.

## Tests and lint

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pyflakes trackyard scripts tests
node --check trackyard/editor/static/editor.js
```

`tests/test_editor.py` checks the editor API and that saving keeps everything except
what was edited.

`tests/test_export.py` also checks exported charts against Beat-Train's own chart parser.
It builds `tools/beattrain-check`, a tiny Swift tool whose `BeatTrainCore` is a symlink
into `../Beat-Train-MCP-Sample`. Without Swift or that repo, the test is skipped.
