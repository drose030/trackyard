# Trackyard plan

## Goal

Turn a finished song (no stems, no MIDI) into a Clone Hero / Beat-Train drum chart
that needs minutes of correction, not hours of charting.

Moonscraper Chart Editor is the reference for manual charting. Trackyard's point is
the automatic first pass, plus an editor built around reviewing that pass: show the
stems, highlight uncertain notes, make tempo-map fixes cheap.

## Architecture

The architecture follows StemKit: Python does the analysis, a local web UI sits on top.

- **Python core (`trackyard/`)**
  - import and decode with ffmpeg
  - Demucs separation
  - beat/downbeat tracking (Beat This!)
  - drum transcription (ADTOF)
  - tempo-map fitting, lane mapping, difficulty levels
  - Clone Hero read/write (`song.ini`, `notes.mid`, `notes.chart`)
- **CLI** (`trackyard convert`) for batch use, the same code path the editor calls.
- **Editor** (`trackyard edit`): a standard-library Python server on 127.0.0.1 plus
  plain JS/canvas, no build step. Web Audio for note ticks and the metronome.
- **Cache**: one folder per input file (name plus content hash) with the mix, the
  Demucs drum stem, ADTOF activations and beats, so reruns skip the slow steps. Editor
  drafts live in `cache/drafts` until their first save.

All analysis runs on the Mac. The MPC Sample is far too slow for separation or
transcription, so it only plays the exported song folder.

## Milestones

### 1. Evaluation spike (done)

`scripts/evaluate.py` measures drum transcription per class and lane, and beats and
downbeats, against hand-made charts. Results are in [evaluation.md](evaluation.md),
on three test sets: 11 Beat-Train songs, 24 RhythmVerse hip-hop charts and 83 Rock
Band 3 songs. It settled Demucs before ADTOF, kicks per song from the stem or the mix,
the chart thresholds and how tempo maps are fitted.

### 2. CLI: song file → song folder (done)

- `trackyard convert`: tags and cover → `song.ini`; Demucs `drums.ogg` + `song.ogg`
- tempo map and time signatures; metrical level picked like a charter (`--tempo auto`)
- grid snapping 1/16 / 1/24 / 1/32 per beat
- lanes from learned models (`--lane-rules` for the simple rules), `notes.mid` with
  tom markers, `BEAT`/`EVENTS` tracks
- Hard/Medium/Easy from Expert (`trackyard/difficulty.py`), `mpc` or `clone-hero`
  preset
- `trackyard.json` with confidences and level settings; `--sd` copy
- round-trip test through Beat-Train's parser

Any drum F 0.88 at ±50 ms on the first test set, 0.90 and 0.93 on the other two.
The `clone-hero` rules score F 0.92 / 0.82 / 0.72 against the hand-made lower levels
of the first set, versus 0.87 / 0.71 / 0.52 for leaving Expert as is. Play-tested on
the MPC: the songs play well, but the charter-like Medium felt close to Hard, which led
to the `mpc` preset.

Still open:
- tom-lane and ride/crash assignment: the learned models are right 79 % (cymbals) and
  49 % (toms) of the time, cross-validated, but transfer weakly to other charters
- fewer tempo events on programmed tracks

### 3. Editor UI (first version done)

Done:
- Convert MP3 in the background into a draft; the first save picks a parent folder.
  Open song (Trackyard's or Clone Hero's, `notes.mid` or `notes.chart`), Save, Save as
  (into `BeatTrain/Songs` on an SD card).
- one waveform lane per stem with mute and volume; six rows named and coloured like
  Beat-Train's instruments (floor tom on green via the tom markers)
- low-confidence highlighting, jump to uncertain notes
- add / select / box select / move (also between lanes) / delete, cymbal/tom toggle,
  copy/cut/paste at the playhead, snap 1/4–1/32, undo/redo, zoom
- playback with note ticks and metronome, 50/75 % speed without pitch change
- all four difficulties; regenerate all lower levels or one
- difficulty controls per level (below)
- tempo mode: drag beat lines onto drum hits to make anchors, offset, timing dots;
  saved as tempo events and `song.ini` delay, notes keep their ticks
- saving only replaces drum notes, tempo and offset; other instruments and events stay

**Difficulty controls.** On the MPC pads, the charter-like Medium played almost like
Hard (4.2–4.7 vs. 5.0 notes/s on test songs), because Clone Hero charters chart for a
real kit. So each lower level has a panel: preset, density, a share per instrument,
finest grid, triplets, hands, notes at once, shortest gap. Strong metrical positions
survive first, and the level keeps each instrument's share within those limits. The
`mpc` preset is the default for conversions; on the four converted test songs it gives
Expert 6.2 notes/s → Hard 4.0 → Medium 2.8 → Easy 1.6, where the charter-like rules
gave Medium 3.8. Settings are stored per song in `trackyard.json`; the export still
writes four fixed levels, so Beat-Train and Clone Hero both get them.

Later, maybe: a density knob in Beat-Train itself that reduces Expert while playing.
Any difficulty without reconverting, but the logic would live in the game (Swift) and
Clone Hero wouldn't get it.

Next:
- Play-test the `mpc` preset on the pads and tune its values.
- Tempo: double/halve the tempo from the editor (renumbers every note), edit time
  signatures.

Still open after that:
- loop region, stem solo
- play-test with keyboard, MIDI kit or the MPC as a USB-MIDI controller, hit judging
  reused from Beat-Train's rules
- user presets for the difficulty controls
- re-detect Expert from the audio (replaces hand edits; the analysis is cached)
- `.sng` song files

### 4. Desktop app for macOS and Windows (in progress)

An Electron app (`desktop/`) that starts the editor server and shows it in its own
window, with native file dialogs. Most people should download one installer and
start right away.

- **Everything in the installer** but the model weights: Python, the Trackyard
  packages, PyTorch for the CPU (on a Mac also the Apple GPU) and ffmpeg, trimmed to
  what a conversion imports. About 300 MB on macOS, well under GitHub's 2 GB limit per
  release file. The models (about 160 MB) download on the first conversion.
- **NVIDIA on Windows:** "Download GPU support" on the start screen fetches the CUDA
  build of PyTorch (2 GB, straight from PyTorch's package index) into the user's data
  folder; the server uses it from then on. Built, not yet tested on a real GPU.
- **AMD on Windows:** PyTorch with ROCm exists for Radeon RX 7000/9000 on Windows 11,
  but needs AMD's own driver package. Later, if at all; the CPU always works.
- Intel Macs are out: PyTorch no longer ships current builds for them.

Steps:
1. macOS app: Electron shell (server, window, dialogs), bundled Python and models,
   unsigned DMG.
2. Windows app, CPU: built with GitHub Actions, tested on a real PC.
3. The NVIDIA download.
4. Signing (Apple Developer ID and notarization; a Windows code-signing certificate)
   once other people use it; until then the README explains the first-start warnings.

Later, maybe: a Linux AppImage.

## Open questions

- **Metrical level:** "auto" matches the charter's choice on 32 of 35 test charts.
  The misses are charter choices (halved to 41 BPM, doubled past 170 BPM, a 1:3
  relation) that the user has to fix with `--tempo` for now.
- **Lanes:** ADTOF has one tom class and one cymbal class. The learned models beat the
  pitch and ride/crash rules on the charters they were trained on, but lane choice is
  a charting convention as much as a sound.
- **Charting style vs. transcription:** hand charts simplify (ghost notes, open hats,
  flams). Those differences count as errors in the evaluation, but they are not
  necessarily transcription mistakes.
- **Licences:** ADTOF is CC BY-NC-SA. Fine for private use; a public release needs
  another model.
