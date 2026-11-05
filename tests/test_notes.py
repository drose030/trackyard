import numpy as np

from trackyard.tempomap import TempoMap
from trackyard.notes import ChartNote, _resolve, build_notes, snap_ticks

TM = TempoMap(480, [(0, 120.0)], [])  # one beat = 0.5 s


def test_straight_sixteenths_snap_to_sixteenths():
    times = [0.0, 0.126, 0.249, 0.377]
    assert snap_ticks(times, TM) == [0, 120, 240, 360]


def test_triplet_beat_snaps_to_triplets():
    times = [1.0, 1.0 + 1 / 6, 1.0 + 2 / 6 + 0.004]
    assert snap_ticks(times, TM) == [960, 1120, 1280]
    assert snap_ticks(times, TM, triplets=False, finest=16) == [960, 1080, 1320]


def test_sixteenth_triplets_and_thirty_seconds():
    sextuplets = [2.0 + i / 12 for i in range(6)]
    assert snap_ticks(sextuplets, TM) == [1920 + 80 * i for i in range(6)]
    dotted = [3.0, 3.0 + 3 / 16, 3.0 + 7 / 16]  # 3/8 and 7/8 of a beat
    assert snap_ticks(dotted, TM) == [2880, 2880 + 180, 2880 + 420]


def test_straight_beat_stays_on_sixteenths_despite_jitter():
    times = [4.0 + 0.008, 4.125 - 0.012, 4.25 + 0.015, 4.375]
    assert snap_ticks(times, TM) == [3840, 3960, 4080, 4200]


def test_tom_moves_off_a_cymbal_lane():
    notes = [ChartNote(0, "yellow-cym", 0.9, 0, "HH"), ChartNote(0, "yellow-tom", 0.8, 0, "TT")]
    assert [n.lane for n in _resolve(notes)] == ["yellow-cym", "blue-tom"]


def test_at_most_two_hands_plus_kick():
    notes = [
        ChartNote(0, "kick", 0.5, 0, "KD"),
        ChartNote(0, "red", 0.9, 0, "SD"),
        ChartNote(0, "yellow-cym", 0.4, 0, "HH"),
        ChartNote(0, "green-cym", 0.7, 0, "CY"),
    ]
    assert [n.lane for n in _resolve(notes)] == ["kick", "red", "green-cym"]


def test_ride_vs_crash():
    onsets = {"CY": [(0.0, 0.8), (0.25, 0.8), (0.5, 0.8), (3.0, 0.9)]}
    lanes = [n.lane for n in build_notes(onsets, TM, None, 44100)]
    assert lanes == ["blue-cym", "blue-cym", "blue-cym", "green-cym"]


def test_toms_split_by_pitch():
    sr = 44100
    times = [i * 0.5 for i in range(9)]
    freqs = [300, 200, 100] * 3
    audio = np.zeros(int(5 * sr))
    t = np.arange(int(0.1 * sr)) / sr
    for start, f in zip(times, freqs):
        a = int(start * sr)
        audio[a : a + len(t)] += np.sin(2 * np.pi * f * t) * np.exp(-t * 20)
    notes = build_notes({"TT": [(x, 0.8) for x in times]}, TM, audio, sr)
    assert [n.lane for n in notes[:3]] == ["yellow-tom", "blue-tom", "green-tom"]
