# Evaluation — results

Run on 2026-11-03 against the 11 hand-charted Beat-Train songs (hip-hop plus two
Beatles tracks), with `scripts/evaluate.py` and the default settings. The full run took
about 3 minutes on an M-series Mac:

- Demucs: about 10 s per song
- ADTOF: about 2 s per source
- Beat This!: about 1 s

Reproduce with:

```bash
.venv/bin/python scripts/evaluate.py ../Beat-Train/Songs
```

Each section records the code as it was when it ran; later sections change some of
the behaviour described earlier, and the notes in brackets point to them. Every Hard,
Medium and Easy score uses the `clone-hero` rules, which both evaluation scripts still
use. The last section measures the `mpc` preset, the default for conversions.

## Milestone 2: finished charts

The `chart` source runs the whole `trackyard convert` pipeline:
- fitted tempo map,
- notes snapped per beat to 1/16, 1/24 or 1/32,
- lanes, writing `notes.mid`, then reading it back.

It is then scored like a hand chart.

| | Kick | Snare | Hi-hat | Toms | Cymbals | Any drum | Any drum ±30 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADTOF raw (kick from the mix, rest and "any drum" from Demucs) | 0.86 | 0.82 | 0.76 | 0.41 | 0.47 | 0.88 | 0.75 |
| Finished chart | 0.84 | 0.82 | 0.76 | 0.41 | 0.47 | **0.86** | 0.74 |

- **Snapping costs almost nothing on programmed music.** The hip-hop tracks keep their
  scores; Eminem, Helicopter and Still D.R.E. reach 0.96–0.97. The losses are in live
  recordings (Abbey Road 0.90 → 0.86, meet the grahams 0.88 → 0.81), where the beat
  grid between anchors is less exact.
- **Grid choice matters.**
  - With 1/16 + 1/12 only, Eminem dropped to 0.81: its kicks sit on sixteenth-note
    triplets.
  - meet the grahams has kicks on 32nds.
  - Per beat, the coarsest grid that keeps every hit within 20 ms wins: 1/16, then 1/24,
    then 1/32.
- **Tempo map.** Beat This! beats get:
  1. sub-frame refinement,
  2. cleanup of missed or doubled beats,
  3. a pull onto confident kick/snare hits within 25 ms,
  4. a shift by the median transient offset (Beat This! is about 10 ms late).

  The grid then lands within 0–6 ms of the hand charts on most songs. "auto" then only
  doubled tempos below 70 BPM: Helicopter comes out at the charter's 134 BPM, while
  Eminem stays at 82 (the charter used 41). (Today's rule: *Choosing the tempo level*.)
- **Lanes are the weak part.** Kick, red and yellow cymbal are fine. Tom lanes
  (F 0.06–0.19) and blue/green cymbals (0.36/0.41) are mostly wrong. ADTOF misses most
  toms, and the rules of the time (toms split by pitch thirds) don't match how charters
  spread toms. (Learned models replaced the rules: *Kick source, thresholds and learned
  lanes*.) On Beat-Train's highway,
  which ignores cymbal/tom, that leaves yellow 0.72, blue 0.12 and green 0.27.
- **Beat-Train compatibility.** Exported charts round-trip through Beat-Train's own Swift
  parser (`tools/beattrain-check`, run by the tests). Beat-Train reads the pro-drums tom
  markers: green toms land on its green floor-tom pad, green cymbals on orange.

## Second test set: 24 RhythmVerse charts (2026-11-03)

These are hip-hop charts from `~/Music/chart-tests`. 23 are by the same charter, and all
are `.chart` with pro cymbal markers and a single full-mix `song` audio file, so there is
no drum-stem upper bound. *Mac Miller – Cinderella* is in the set twice.

| F@50 | Kick | Snare | Hi-hat | Toms | Cymbals | Any drum | Any drum ±30 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADTOF on Demucs drums | 0.93 | 0.67 | 0.79 | 0.19 | 0.05 | 0.89 | 0.89 |
| Finished chart | 0.93 | 0.67 | 0.79 | 0.19 | 0.05 | **0.89** | 0.88 |

| Level (hand Expert reduced vs. hand chart) | `clone-hero` rules | Expert as-is |
|---|---:|---:|
| Hard | 0.91 | 0.89 |
| Medium | 0.86 | 0.74 |
| Easy | 0.81 | 0.56 |

- **Timing holds up on a new charter.** Any drum is 0.89 at both ±50 and ±30 ms. These
  charts are tightly synced, and most songs reach 0.92–0.99.
- **The difficulty rules generalise.** They were tuned on the first set, yet do better
  here for Medium and Easy. Medium is still about 18 % too dense (fixed in *Medium
  retuned*).
- **Snare recall is only 0.56.** This charter puts claps, rimshots and snare rolls on
  red: *To Zion* has 1,152 red notes and ghost-note markers, *Deep Down Body Thurst*
  878. ADTOF only hears part of that as snare.
- **Blue cymbal is used for a second hat-like sound** (1,263 notes, e.g. 410 in *I Know
  What You Want*). ADTOF reports those as hi-hat, so they land on yellow. Crashes
  (green) are rare and mostly missed.
- **Metrical level:** 7 of 24 charts are at double the felt tempo (e.g. 146 BPM where
  Beat This! and "auto" give 73). The charter doubles songs whose hats run in 16ths;
  "auto" then only doubled below 70 BPM. This doesn't change note timing, but it does
  change beat lines and the beat-based difficulty rules. (Fixed in *Choosing the tempo
  level*.)
- **Bug found and fixed:** *J^p^n – Bloom* sets both `delay = -2000` and `Offset = -2`.
  The reader added them (−4 s); it now uses the ini `delay` and falls back to `Offset`.
  Bloom went from 0.16 to 0.65.

## Kick source, thresholds and learned lanes (2026-11-03)

**Quick wins**, measured end to end on the finished charts (F@50, before → after):

| | Kick | Toms | Cymbals | Any drum |
|---|---|---|---|---|
| First set (11) | 0.84 → 0.85 | 0.41 → 0.43 | 0.47 → 0.43 | 0.86 → 0.88 |
| Hip-hop (24) | 0.93 → 0.94 | 0.19 → 0.22 | 0.05 → 0.09 | 0.89 → 0.90 |
| Rock Band 3 (83) | 0.93 → 0.95 | 0.56 → 0.60 | 0.73 → 0.75 | 0.93 → 0.93 |

- Kicks come from the Demucs stem unless it found fewer than 60 % of the mix's kicks
  (the 808 case).
- The chart thresholds are TT 0.20 and CY 0.20 instead of ADTOF's 0.32 / 0.30. The
  raw-ADTOF rows in the reports keep the defaults.

**Learned lanes.** Two models replace the hand rules: blue vs. green vs. yellow
cymbal, and which tom.
- Labels: every detected cymbal/tom that matches a hand-charted note within ±50 ms.
- Features (`trackyard/lanes.py`):
  - the sound: bands, brightness, decay, tom pitch after the attack, relative pitch,
  - the context: downbeat, kick/snare at the same time, cymbal and hat density, place
    in the fill.
- Model: gradient-boosted trees (scikit-learn for training only), exported as JSON and
  run with numpy (`trackyard/models/lanes.json`).
- Training: `scripts/train_lanes.py`.

Accuracy per labelled hit, cross-validated by song (5 folds, a song is never in its own
training data):

| Trained and tested on | Cymbals: rules → model | Toms: rules → model |
|---|---|---|
| Rock Band 3 only (81 / 72 songs) | 0.72 → **0.80** | 0.43 → **0.57** |
| All three sets (97 / 83 songs), the shipped model | 0.71 → **0.79** | 0.39 → **0.49** |

**Transfer to other charters is weak.** Trained on Rock Band 3 only and tested on the
other sets:
- First set, mostly the Beatles: cymbals 0.38 → 0.38, toms 0.22 → 0.23.
- Hip-hop: cymbals 0.65 → 0.55, toms 0.18 → 0.33.

End to end on the two Beatles songs, the cymbal/tom lanes even dropped slightly
(0.17 → 0.15, 0.25 → 0.23). Lane choice is a charting convention as much as a sound:
it depends on the kit, the era and the charter. The models learn what they've seen.
That's why the shipped model is trained on all three sets, and
`trackyard convert --lane-rules` brings back the simple rules. The editor's T key and
dragging between lanes remain the real fix for lanes.

## Third test set: 83 Rock Band 3 songs (2026-11-03)

These are the on-disc Rock Band 3 setlist as Clone Hero folders from
`~/Music/chart-tests-rock`, charted by Harmonix:
- real drummers, Pro Drums with toms and cymbals (13 % blue/green cymbals, 6 % toms),
- separate drum stems, so the upper bound is available again.

| F@50 | Kick | Snare | Hi-hat | Toms | Cymbals | Any drum | Any drum ±30 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADTOF on the mix | 0.93 | 0.79 | 0.74 | 0.54 | 0.74 | 0.94 | 0.91 |
| ADTOF on Demucs drums | 0.96 | 0.80 | 0.74 | 0.57 | 0.73 | 0.93 | 0.91 |
| ADTOF on the original drum stem | 0.96 | 0.82 | 0.79 | 0.67 | 0.82 | 0.96 | 0.94 |
| Finished chart | 0.93 | 0.79 | 0.74 | 0.56 | 0.73 | **0.93** | 0.90 |

Lanes of the finished chart (F@50):

| Lane | kick | red | yellow cym | yellow tom | blue cym | blue tom | green cym | green tom |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| F | 0.93 | 0.79 | 0.74 | 0.19 | 0.62 | 0.36 | 0.38 | 0.11 |

On Beat-Train's highway, which ignores cymbal/tom: yellow 0.73, blue 0.56, green 0.31.

Generated levels (`clone-hero` rules) against Harmonix's: Expert 0.76, Hard 0.74,
Medium 0.72, Easy 0.55.
The chosen tempo level matches in 79 of 83 songs.

- **On real kits, timing and the common classes are as good as on hip-hop**: any drum
  0.93, kick 0.93–0.96. Cymbals (0.73) and toms (0.56) are detected far better than in
  the hip-hop sets, where those "cymbals" were mostly other sounds.
- **Kicks: Demucs is better on rock** (0.96 vs. 0.93 from the mix; better in 55 of 83
  songs). The "kicks from the mix" rule was made for 808s in hip-hop. (Now decided per
  song: *Kick source, thresholds and learned lanes*.)
- **Lower TT/CY thresholds help**: on Demucs drums, CY 0.15 gives 0.77 (vs. 0.73) and
  TT 0.20 gives 0.61 (vs. 0.57). The hip-hop sets point the same way or are neutral.
  (Charts now use TT 0.20 and CY 0.20.)
- **The lanes are the bottleneck.**
  - Ride vs. crash: green-cymbal recall is only 0.25, because "another cymbal within a
    beat → ride" also catches crash-heavy choruses.
  - Tom lanes split by pitch thirds: 0.11–0.36.
  - This set is large and labelled well enough to learn those decisions instead of
    guessing rules. (That led to the learned lane models.)
- **Easy is weaker on rock** (0.55; recall 0.52). Harmonix keeps more notes on Easy than
  the rules do.

## Choosing the tempo level (2026-11-03)

Charters pick a metrical level, and Trackyard has to guess it. In the 8 double-tempo
charts of both test sets, 44–50 % of the detected hi-hats sit on the "e"/"a" sixteenths
of the felt beat. In normal-tempo charts that share is mostly below 0.42. Busy-hat songs
the charter did *not* double would have ended up above 170 BPM.

"auto" therefore doubles in two cases:
- below 70 BPM,
- when ≥ 40 % of the hats are on the "e"/"a", with ≥ 1.5 hats per beat that has any,
  and the doubled tempo is ≤ 170 BPM.

It still halves above 190 BPM.

Results on all 35 songs, finished charts, each level against the hand-made one (same
lane, ±50 ms):

| | Old rule (< 70 BPM) | New rule |
|---|---:|---:|
| Tempo level matches the chart | 27/35 | **32/35** |
| Expert | 0.74 | 0.74 |
| Hard | 0.68 | **0.71** |
| Medium | 0.73 | 0.71 |
| Easy | 0.58 | **0.61** |

Three songs still miss:
- *Infinite*: the charter halved it, to 41 BPM.
- *Bloom*: the chart and the detected beat are in a 1:3 relation.
- *SKITZO*: the charter doubled to 184 BPM, past the 170 limit.

Medium got slightly worse: at the doubled tempo its beat-based spacing keeps more notes,
and Medium was already too dense.

## Medium retuned (2026-11-03)

Medium was 4–18 % too dense, and it got worse with the double-tempo rule. A grid search
was run on each test set separately and cross-checked on the other. It picked two
changes:

1. **Allow kick plus two hands at once** (`max_notes` 3 instead of 2). This was best on
   both sets independently: old set 0.823 → 0.833, new set 0.855 → 0.860.
2. **Cymbals at least 0.25 s apart**, i.e. hats no faster than eighths at 120 BPM. This
   is a new time-based gap on top of the beat-based one, so double-tempo charts thin
   out properly. New set 0.860 → 0.897 (density 1.19× → 1.02×); old set 0.833 → 0.826.

| Medium | Old set (9) | New set (23) | Finished charts, all 35 (end to end) |
|---|---:|---:|---:|
| Before | 0.82 (1.04×) | 0.86 (1.18×) | 0.71 |
| After | **0.83** (1.05×) | **0.90** (1.02×) | **0.73** |

## Hard, Medium and Easy

`scripts/evaluate_difficulty.py` applies the `clone-hero` reduction rules to each
song's **hand-made** Expert chart (measured before *Medium retuned*). It then compares the result with the same song's
hand-made Hard, Medium and Easy:
- notes must match exactly (same tick, same lane),
- the baseline in brackets is Expert left unreduced.

Helicopter and Juice WRLD have no lower difficulties.

| Song | Hard F (baseline) | notes ours/hand | Medium F (baseline) | notes ours/hand | Easy F (baseline) | notes ours/hand |
|---|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mix | **0.96** (0.93) | 1301/1196 | **0.86** (0.84) | 1290/985 | **0.79** (0.57) | 541/550 |
| A$AP Rocky - Helicopter (MrPeebo) | – | – | – | – | – | – |
| Dr Dre - Still DRE (Radio Mix) (fe | **1.00** (0.96) | 1205/1205 | **0.91** (0.87) | 1205/1004 | **1.00** (0.56) | 504/504 |
| Eminem - Infinite (Radio Mix) | **0.84** (0.80) | 495/676 | **0.95** (0.65) | 484/492 | **0.99** (0.47) | 310/308 |
| Juice WRLD - We Dont Get Along | – | – | – | – | – | – |
| Kanye West - Heard Em Say | **1.00** (0.70) | 468/468 | **0.67** (0.31) | 312/156 | **1.00** (0.43) | 234/234 |
| Kendrick Lamar - meet the grahams | **0.91** (0.81) | 1435/1212 | **0.69** (0.40) | 736/726 | **0.48** (0.23) | 605/484 |
| Kendrick Lamar, Blxst, Amanda Reif | **0.94** (0.89) | 1171/1314 | **0.85** (0.86) | 898/1219 | **0.65** (0.74) | 451/938 |
| Kurtis Blow - The Breaks (Part 1) | **0.98** (0.82) | 1042/1064 | **0.81** (0.62) | 944/678 | **0.76** (0.53) | 470/540 |
| The Beatles - A Day in the Life | **0.93** (0.88) | 569/648 | **0.77** (0.73) | 454/501 | **0.73** (0.60) | 307/363 |
| The Beatles - Abbey Road Medley | **0.86** (0.91) | 3431/3609 | **0.76** (0.75) | 2685/2895 | **0.53** (0.52) | 1538/1726 |

| Level | F ours | F Expert as-is | Notes ours / hand |
|---|---:|---:|---:|
| Hard | **0.92** | 0.87 | 0.98 |
| Medium | **0.82** | 0.71 | 1.04 |
| Easy | **0.72** | 0.52 | 0.88 |

- **Hard is close to hand-made** (0.92). It mostly drops 16th hi-hats and kicks.
- **Medium (0.82) and Easy (0.72) are where charters differ most.** For example, Easy
  hi-hats are on half notes in the Dr. Dre and 2Pac charts and on quarters in *Die Hard*.
- **Tempo-dependent rules:** Eminem's chart is at half tempo (41 BPM), so its Easy
  "quarters" are our eighths. That is the song where Easy scores worst.
- **Overfitting risk:** the spacing values come from a small grid search on these same
  9 songs. (The RhythmVerse set later showed they generalise.)

## Spike findings (ADTOF and Beat This! on their own)

**Any-drum timing works well.** "ALL" asks: is there a drum hit here, whatever the
class? On the full mix it scores F@50 0.83; with a Demucs drum stem first it scores
0.88. The original drum stem reaches 0.92, so Demucs closes most of the gap. Several
songs reach 0.96–0.98.

**Demucs before ADTOF is worth it.** It helps snare (+0.04), hi-hat (+0.05) and ALL
(+0.05), and it removes timing jitter: the median offset drops to 1–5 ms. The exception
is 808 kicks. On Juice WRLD, Demucs moved the kick into the bass stem, so kick recall
fell to 0. (The pipeline now runs ADTOF on both and takes the mix's kicks when Demucs
finds far fewer.)

**Kick and snare are reliable** (F 0.82–0.88). Hi-hat is usable (0.76). Toms (0.41)
and cymbals (0.47) are weak, and still only 0.53/0.62 from the clean drum stem. Part of
that is charting style rather than recognition. Hip-hop charters put claps,
percussion or hats on tom lanes:
- *meet the grahams* has no snare or hi-hat in its chart, only kick and toms,
- *Heard 'Em Say* charts its yellow notes as toms,
- *Die Hard* has 174 blue toms that ADTOF never finds.

The Beatles songs, which are real drum kits, score TT 0.6–0.75.

**The confidence value is meaningful.** Picked notes with activation ≥ 0.9 are 99 %
correct, 0.7–0.9 about 85 %, 0.3–0.5 about 45–60 %, and below 0.3 about 40 %. A
highlight below 0.5, as the editor now uses, flags about a fifth of all notes, and
those hold about a third of all false positives.

**The default ADTOF thresholds are fine on this set.** A global per-class sweep gains
at most 0.01–0.03, toms included. (The larger Rock Band 3 set later justified lower
tom and cymbal thresholds.)

**±30 ms is noticeably harder than ±50 ms** (ALL 0.75 vs 0.88 with Demucs). Grid
snapping did not close the gap: the finished charts score 0.74 at ±30 ms.

**Sync offsets exist in the reference charts.** Juice WRLD's chart is about 45 ms
early against its audio, which makes F@30 = 0. With that offset removed, ALL rises to
0.94. The editor's Tempo mode has a global offset for this.

**Beat positions are very reliable** (AMLt ≥ 0.98 on 10 of 11 songs). Downbeats are
right on 9 of 11. The weak spot is the **metrical level**:
- Helicopter is charted at 134 BPM where Beat This! hears 67,
- Eminem's chart sits at 41 BPM against 81.

Both are octave choices by the charter, not tracking errors. Odd meters and changing
time signatures (6/4 in *meet the grahams*) confuse the downbeats.

### What this means for Trackyard

| Step | Automatic? | Manual work expected |
|---|---|---|
| Beat grid / tempo map | yes | pick double/half tempo, check odd-meter sections |
| Global offset | yes (estimate) | nudge if the export feels off |
| Kick, snare | yes | spot fixes, 808 kicks |
| Hi-hat | yes | open/closed and ghost notes, quiet hats in dense mixes |
| Toms | partly | assign lanes, fill in missed toms |
| Cymbals | partly | crash vs. ride (green/blue), missed crashes |
| Charting style (what goes on which lane in non-drum-kit music) | no | the user's choice |

Caveats:
- 11 songs, mostly hip-hop with sparse kits. (The Rock Band 3 set later covered real
  kits.)
- The reference is a game chart, not a transcription: simplifications count as errors.
- Songs without separate drum stems (3 of 11) are missing from the upper-bound rows.

## Full report

The milestone 2 run on the first test set, before the kick and threshold changes.

Sources: `mix` = ADTOF on the full mix; `demucs` = ADTOF on Demucs drums from the mix; `stem` = ADTOF on the original drum stem (upper bound); `chart` = finished Trackyard chart (snapped to the fitted tempo map)

## Per class, all songs (micro average)

| Source | Class | Ref notes | Est | P@50 | R@50 | F@50 | F@30 | Median offset ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| mix | KD | 4038 | 4227 | 0.84 | 0.88 | **0.86** | 0.78 | 2 |
| mix | SD | 2296 | 2765 | 0.71 | 0.86 | **0.78** | 0.73 | 4 |
| mix | HH | 6388 | 7051 | 0.68 | 0.75 | **0.71** | 0.61 | 7 |
| mix | TT | 3117 | 1289 | 0.66 | 0.27 | **0.39** | 0.35 | 8 |
| mix | CY | 560 | 419 | 0.56 | 0.42 | **0.48** | 0.40 | 12 |
| mix | ALL | 11796 | 10389 | 0.89 | 0.78 | **0.83** | 0.72 | 6 |
| demucs | KD | 4038 | 4457 | 0.80 | 0.89 | **0.84** | 0.81 | -1 |
| demucs | SD | 2296 | 2377 | 0.81 | 0.84 | **0.82** | 0.78 | 1 |
| demucs | HH | 6388 | 7232 | 0.71 | 0.81 | **0.76** | 0.62 | 5 |
| demucs | TT | 3117 | 1429 | 0.65 | 0.30 | **0.41** | 0.38 | 3 |
| demucs | CY | 560 | 308 | 0.67 | 0.37 | **0.47** | 0.41 | 9 |
| demucs | ALL | 11796 | 10719 | 0.92 | 0.84 | **0.88** | 0.75 | 4 |
| stem | KD | 3137 | 3437 | 0.84 | 0.92 | **0.88** | 0.82 | 1 |
| stem | SD | 1706 | 2127 | 0.76 | 0.95 | **0.84** | 0.80 | 2 |
| stem | HH | 4845 | 6502 | 0.67 | 0.90 | **0.77** | 0.75 | 3 |
| stem | TT | 2705 | 1309 | 0.81 | 0.39 | **0.53** | 0.48 | 9 |
| stem | CY | 530 | 393 | 0.73 | 0.54 | **0.62** | 0.54 | 8 |
| stem | ALL | 9273 | 9297 | 0.92 | 0.92 | **0.92** | 0.84 | 4 |
| chart | KD | 4038 | 4227 | 0.82 | 0.86 | **0.84** | 0.75 | 3 |
| chart | SD | 2296 | 2374 | 0.81 | 0.83 | **0.82** | 0.78 | 2 |
| chart | HH | 6388 | 7228 | 0.72 | 0.81 | **0.76** | 0.63 | 6 |
| chart | TT | 3117 | 1429 | 0.65 | 0.30 | **0.41** | 0.37 | 7 |
| chart | CY | 560 | 308 | 0.66 | 0.36 | **0.47** | 0.37 | 19 |
| chart | ALL | 11796 | 10526 | 0.92 | 0.82 | **0.86** | 0.74 | 6 |

## Finished chart per lane (F@50, all songs)

Pro-drums lanes, then colours only (cymbal/tom ignored, as on Beat-Train's highway).

| Lane | Ref notes | Est | P | R | F |
|---|---:|---:|---:|---:|---:|
| kick | 4038 | 4227 | 0.82 | 0.86 | **0.84** |
| red | 2296 | 2374 | 0.81 | 0.83 | **0.82** |
| yellow-cym | 6388 | 7228 | 0.72 | 0.81 | **0.76** |
| yellow-tom | 638 | 292 | 0.10 | 0.05 | **0.06** |
| blue-cym | 296 | 192 | 0.46 | 0.30 | **0.36** |
| blue-tom | 1975 | 885 | 0.13 | 0.06 | **0.08** |
| green-cym | 264 | 116 | 0.66 | 0.29 | **0.41** |
| green-tom | 504 | 252 | 0.29 | 0.14 | **0.19** |
| yellow | 7026 | 7520 | 0.70 | 0.75 | **0.72** |
| blue | 2271 | 1077 | 0.19 | 0.09 | **0.12** |
| green | 768 | 368 | 0.42 | 0.20 | **0.27** |

## Per song — ADTOF on the full mix (F@50)

| Song | KD | SD | HH | TT | CY | ALL | ALL@30 | Offset ms | ALL synced | ALL@30 synced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mi… | 0.99 | 1.00 | 0.77 | 0.00 | 0.00† | **0.81** | 0.81 | 0 | 0.81 | 0.81 |
| A$AP Rocky - Helicopter (MrPeebo) | 0.95 | 0.97 | 0.93 | 0.03 | –† | **0.96** | 0.96 | 8 | 0.96 | 0.96 |
| Dr Dre - Still DRE (Radio Mix) (f… | 0.93 | 1.00 | 0.95 | –† | –† | **0.94** | 0.90 | 6 | 0.94 | 0.92 |
| Eminem - Infinite (Radio Mix) | 0.88 | 0.99 | 0.86 | –† | 0.00† | **0.89** | 0.88 | -2 | 0.89 | 0.88 |
| Juice WRLD - We Dont Get Along | 0.87 | 0.55 | 0.68 | –† | 0.00 | **0.70** | 0.00 | 44 | 0.78 | 0.78 |
| Kanye West - Heard Em Say | 0.76 | 0.69 | 0.00† | 0.17 | –† | **0.88** | 0.88 | -1 | 0.88 | 0.88 |
| Kendrick Lamar - meet the grahams | 0.86 | 0.00† | 0.00† | 0.00 | 0.00† | **0.82** | 0.54 | 22 | 0.74 | 0.66 |
| Kendrick Lamar, Blxst, Amanda Rei… | 0.89 | 0.95 | 0.20 | 0.00 | 0.00 | **0.65** | 0.65 | -1 | 0.65 | 0.65 |
| Kurtis Blow - The Breaks (Part 1) | 0.83 | 0.81 | 0.86 | 0.63 | 0.50 | **0.87** | 0.82 | 14 | 0.87 | 0.85 |
| The Beatles - A Day in the Life | 0.64 | 0.86 | 0.41 | 0.62 | 0.23 | **0.71** | 0.68 | -1 | 0.71 | 0.68 |
| The Beatles - Abbey Road Medley | 0.84 | 0.66 | 0.75 | 0.58 | 0.55 | **0.85** | 0.75 | 11 | 0.85 | 0.78 |

† class absent from the chart (any estimate there is a false positive; F is 0 or –). *Synced*: the song's median offset removed first, as a global offset setting would.

## Per song — ADTOF on Demucs drums from the mix (F@50)

| Song | KD | SD | HH | TT | CY | ALL | ALL@30 | Offset ms | ALL synced | ALL@30 synced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mi… | 0.97 | 1.00 | 0.82 | 0.00 | 0.00† | **0.86** | 0.86 | -3 | 0.86 | 0.86 |
| A$AP Rocky - Helicopter (MrPeebo) | 1.00 | 0.99 | 0.93 | 0.16 | –† | **0.97** | 0.97 | 5 | 0.97 | 0.97 |
| Dr Dre - Still DRE (Radio Mix) (f… | 0.99 | 1.00 | 0.95 | –† | –† | **0.98** | 0.97 | 2 | 0.98 | 0.98 |
| Eminem - Infinite (Radio Mix) | 0.87 | 0.99 | 0.93 | –† | 0.00† | **0.96** | 0.96 | -5 | 0.96 | 0.96 |
| Juice WRLD - We Dont Get Along | 0.00 | 0.31 | 0.91 | –† | 0.00 | **0.91** | 0.00 | 41 | 0.94 | 0.94 |
| Kanye West - Heard Em Say | 0.81 | 0.76 | 0.00† | 0.52 | –† | **0.90** | 0.90 | -5 | 0.90 | 0.90 |
| Kendrick Lamar - meet the grahams | 0.84 | 0.00† | 0.00† | 0.00 | –† | **0.88** | 0.58 | 23 | 0.84 | 0.74 |
| Kendrick Lamar, Blxst, Amanda Rei… | 0.88 | 0.94 | 0.42 | 0.00 | 0.00 | **0.65** | 0.65 | -4 | 0.65 | 0.65 |
| Kurtis Blow - The Breaks (Part 1) | 0.83 | 0.80 | 0.82 | 0.55 | 0.50 | **0.83** | 0.76 | 15 | 0.83 | 0.81 |
| The Beatles - A Day in the Life | 0.62 | 0.90 | 0.52 | 0.69 | 0.14 | **0.76** | 0.70 | -4 | 0.76 | 0.71 |
| The Beatles - Abbey Road Medley | 0.80 | 0.76 | 0.74 | 0.57 | 0.58 | **0.90** | 0.81 | 8 | 0.90 | 0.83 |

† class absent from the chart (any estimate there is a false positive; F is 0 or –). *Synced*: the song's median offset removed first, as a global offset setting would.

## Per song — ADTOF on the original drum stem (upper bound) (F@50)

| Song | KD | SD | HH | TT | CY | ALL | ALL@30 | Offset ms | ALL synced | ALL@30 synced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mi… | 1.00 | 1.00 | 0.75 | 0.00 | 0.00† | **0.79** | 0.79 | -4 | 0.79 | 0.79 |
| A$AP Rocky - Helicopter (MrPeebo) | 0.99 | 1.00 | 0.93 | 0.23 | –† | **0.97** | 0.97 | 5 | 0.97 | 0.97 |
| Dr Dre - Still DRE (Radio Mix) (f… | 1.00 | 1.00 | 0.95 | –† | –† | **1.00** | 1.00 | 2 | 1.00 | 1.00 |
| Eminem - Infinite (Radio Mix) | 0.95 | 0.98 | 0.99 | –† | 0.00† | **0.99** | 0.98 | -5 | 0.99 | 0.99 |
| Kendrick Lamar - meet the grahams | 0.94 | 0.00† | 0.00† | 0.00 | –† | **0.96** | 0.66 | 19 | 0.88 | 0.74 |
| Kurtis Blow - The Breaks (Part 1) | 0.83 | 0.81 | 0.85 | 0.56 | 0.67 | **0.86** | 0.81 | 14 | 0.86 | 0.84 |
| The Beatles - A Day in the Life | 0.49 | 0.89 | 0.53 | 0.77 | 0.47 | **0.82** | 0.78 | -4 | 0.81 | 0.78 |
| The Beatles - Abbey Road Medley | 0.78 | 0.80 | 0.78 | 0.75 | 0.64 | **0.95** | 0.84 | 11 | 0.95 | 0.87 |

† class absent from the chart (any estimate there is a false positive; F is 0 or –). *Synced*: the song's median offset removed first, as a global offset setting would.

## Per song — finished Trackyard chart (snapped to the fitted tempo map) (F@50)

| Song | KD | SD | HH | TT | CY | ALL | ALL@30 | Offset ms | ALL synced | ALL@30 synced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mi… | 0.99 | 1.00 | 0.82 | 0.00 | 0.00† | **0.86** | 0.86 | -2 | 0.86 | 0.86 |
| A$AP Rocky - Helicopter (MrPeebo) | 0.95 | 0.99 | 0.93 | 0.16 | –† | **0.97** | 0.97 | 6 | 0.97 | 0.97 |
| Dr Dre - Still DRE (Radio Mix) (f… | 0.93 | 1.00 | 0.95 | –† | –† | **0.97** | 0.97 | 4 | 0.97 | 0.97 |
| Eminem - Infinite (Radio Mix) | 0.87 | 0.99 | 0.93 | –† | 0.00† | **0.96** | 0.95 | -3 | 0.96 | 0.95 |
| Juice WRLD - We Dont Get Along | 0.98 | 0.41 | 0.94 | –† | 0.00 | **0.94** | 0.00 | 46 | 0.94 | 0.94 |
| Kanye West - Heard Em Say | 0.76 | 0.76 | 0.00† | 0.52 | –† | **0.91** | 0.91 | -6 | 0.91 | 0.91 |
| Kendrick Lamar - meet the grahams | 0.74 | 0.00† | 0.00† | 0.00 | –† | **0.81** | 0.64 | 20 | 0.81 | 0.72 |
| Kendrick Lamar, Blxst, Amanda Rei… | 0.89 | 0.94 | 0.42 | 0.00 | 0.00 | **0.65** | 0.65 | -1 | 0.65 | 0.65 |
| Kurtis Blow - The Breaks (Part 1) | 0.83 | 0.80 | 0.81 | 0.54 | 0.50 | **0.83** | 0.81 | 12 | 0.83 | 0.80 |
| The Beatles - A Day in the Life | 0.64 | 0.89 | 0.52 | 0.67 | 0.14 | **0.75** | 0.66 | 1 | 0.75 | 0.67 |
| The Beatles - Abbey Road Medley | 0.84 | 0.75 | 0.74 | 0.57 | 0.58 | **0.86** | 0.73 | 12 | 0.86 | 0.77 |

† class absent from the chart (any estimate there is a false positive; F is 0 or –). *Synced*: the song's median offset removed first, as a global offset setting would.

## Precision by confidence (ADTOF activation at the picked peak, ±50 ms)

| Source | 0.0–0.3 | 0.3–0.5 | 0.5–0.7 | 0.7–0.9 | 0.9–1.0 |
|---|---:|---:|---:|---:|---:|
| mix | 0.35 (n=249) | 0.44 (n=3420) | 0.71 (n=4767) | 0.87 (n=6901) | 0.99 (n=414) |
| demucs | 0.44 (n=199) | 0.60 (n=3327) | 0.75 (n=5447) | 0.82 (n=6647) | 0.99 (n=183) |
| stem | 0.38 (n=154) | 0.55 (n=2547) | 0.73 (n=4187) | 0.83 (n=6733) | 0.99 (n=147) |

## Best global threshold per class (F@50, all songs)

| Source | KD | SD | HH | TT | CY |
|---|---:|---:|---:|---:|---:|
| mix | 0.20 → 0.86 (default 0.86) | 0.40 → 0.80 (default 0.78) | 0.25 → 0.71 (default 0.71) | 0.10 → 0.43 (default 0.39) | 0.25 → 0.48 (default 0.48) |
| demucs | 0.25 → 0.84 (default 0.84) | 0.20 → 0.83 (default 0.82) | 0.15 → 0.76 (default 0.76) | 0.15 → 0.44 (default 0.41) | 0.30 → 0.47 (default 0.47) |
| stem | 0.15 → 0.88 (default 0.88) | 0.30 → 0.85 (default 0.84) | 0.25 → 0.77 (default 0.77) | 0.15 → 0.61 (default 0.53) | 0.40 → 0.62 (default 0.62) |

## Beats and tempo

*Raw*: Beat This! on the full mix. *Grid*: the fitted tempo map of the finished chart.

| Song | Chart tempos | Grid tempos | Chart BPM | Raw BPM | Grid BPM | Raw F@70 | Raw AMLt | Grid F@30 | Grid AMLt | Downbeat F@70 | Grid offset ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2Pac - How Do U Want It (Radio Mi… | 9 | 3 | 92.5 | 92.7 | 92.5 | **1.00** | 1.00 | 1.00 | 1.00 | 0.98 | -1 |
| A$AP Rocky - Helicopter (MrPeebo) | 2 | 13 | 134.0 | 67.0 | 134.0 | **0.66** | 0.99 | 1.00 | 0.99 | 0.67 | 6 |
| Dr Dre - Still DRE (Radio Mix) (f… | 106 | 36 | 93.5 | 93.6 | 93.4 | **1.00** | 1.00 | 1.00 | 1.00 | 1.00 | 3 |
| Eminem - Infinite (Radio Mix) | 42 | 57 | 41.0 | 82.1 | 82.1 | **0.66** | 0.98 | 0.65 | 0.98 | 0.65 | -3 |
| Juice WRLD - We Dont Get Along | 59 | 2 | 90.0 | 90.0 | 90.0 | **1.00** | 1.00 | 0.00 | 1.00 | 1.00 | 46 |
| Kanye West - Heard Em Say | 3 | 53 | 95.1 | 95.1 | 95.1 | **1.00** | 1.00 | 1.00 | 1.00 | 0.99 | -6 |
| Kendrick Lamar - meet the grahams | 15 | 191 | 75.0 | 75.5 | 75.1 | **0.91** | 0.82 | 0.69 | 0.85 | 0.61 | 20 |
| Kendrick Lamar, Blxst, Amanda Rei… | 1 | 10 | 101.0 | 100.6 | 101.0 | **1.00** | 1.00 | 1.00 | 1.00 | 1.00 | -1 |
| Kurtis Blow - The Breaks (Part 1) | 53 | 32 | 112.8 | 112.4 | 112.9 | **0.99** | 1.00 | 0.92 | 1.00 | 0.99 | 13 |
| The Beatles - A Day in the Life | 132 | 71 | 81.5 | 81.2 | 81.6 | **1.00** | 1.00 | 0.91 | 0.99 | 0.68 | 1 |
| The Beatles - Abbey Road Medley | 562 | 274 | 89.0 | 90.4 | 90.9 | **0.97** | 0.93 | 0.79 | 0.94 | 0.93 | 11 |

AMLt accepts double/half tempo and off-beat phase; CMLt (in beats.csv) does not.

## MPC preset (difficulty controls)

The `clone-hero` rules above match hand-made charts, but on the MPC pads Medium played
almost like Hard. The `mpc` preset aims for even steps instead. Median notes per
second, with each song's Expert as input:

| Songs | Preset | Expert | Hard | Medium | Easy |
|---|---|---:|---:|---:|---:|
| 4 converted test songs (`out/`) | clone-hero | 6.15 | 4.22 | 3.81 | 1.72 |
| | **mpc** | 6.15 | **4.03** | **2.81** | **1.61** |
| 107 hand-charted songs (RhythmVerse and Rock Band 3 sets) | clone-hero | 6.97 | 5.64 | 4.40 | 2.58 |
| | **mpc** | 6.97 | **5.41** | **3.30** | **1.95** |

On the converted songs that is 66 / 46 / 26 % of Expert. Medium and Easy allow one hand
plus kick; the snare keeps its backbeat on every level. These numbers measure density
only: whether the steps feel even still needs a play-test on the pads.
