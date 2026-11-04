import numpy as np
import pytest

from trackyard.chart import TempoMap
from trackyard.tempo import clean_beats, fit_tempo_map


def grid(bpm, n, start=0.5, jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    b = start + np.arange(n) * 60.0 / bpm
    return list(b + rng.uniform(-jitter, jitter, n))


def fitted_times(fit):
    return np.array([fit.tempo_map.seconds(t) for t in fit.beat_ticks])


def test_constant_tempo_with_frame_jitter_gives_one_tempo():
    beats = grid(96, 200, jitter=0.008)
    fit = fit_tempo_map(beats, beats[::4], "normal")
    tempos = fit.tempo_map.tempos
    assert len(tempos) == 2  # lead-in + song
    assert tempos[1][1] == pytest.approx(96, abs=0.05)
    assert np.abs(fitted_times(fit) - grid(96, 200)).max() < 0.004


def test_tempo_change_is_followed():
    a = grid(100, 64)
    b = [a[-1] + (i + 1) * 0.5 for i in range(64)]  # 120 BPM
    beats = a + b
    fit = fit_tempo_map(beats, beats[::4], "normal")
    assert np.abs(fitted_times(fit) - np.array(beats)).max() < 0.012
    bpms = [bpm for _, bpm in fit.tempo_map.tempos[1:]]
    assert bpms[0] == pytest.approx(100, abs=0.1) and bpms[-1] == pytest.approx(120, abs=0.1)


def test_lead_in_keeps_tick_zero_at_time_zero():
    beats = grid(120, 40, start=1.3)
    fit = fit_tempo_map(beats, beats[::4], "normal")
    tm = fit.tempo_map
    assert tm.seconds(0) == 0
    assert tm.seconds(fit.beat_ticks[0]) == pytest.approx(1.3, abs=1e-6)
    assert fit.beat_ticks[0] % tm.ppq == 0


def test_missed_and_doubled_beats_are_repaired():
    b = grid(100, 30)
    broken = b[:10] + b[11:21] + [b[20] + 0.05] + b[21:]  # beat 10 missing, an extra after 20
    fixed = clean_beats(np.array(broken))
    assert len(fixed) == 30
    assert np.abs(fixed - b).max() < 1e-6


def test_signatures_follow_downbeats():
    beats = grid(100, 40)
    downs = [beats[i] for i in (0, 4, 8, 11, 15, 19, 23)]  # one 3/4 bar
    fit = fit_tempo_map(beats, downs, "normal")
    sigs = [(n, d) for _, n, d in fit.tempo_map.signatures]
    assert (3, 4) in sigs and sigs[-1] == (4, 4)


@pytest.mark.parametrize("scale,factor", [("double", 2.0), ("half", 0.5), ("normal", 1.0)])
def test_metrical_level(scale, factor):
    beats = grid(90, 64)
    fit = fit_tempo_map(beats, beats[::4], scale)
    assert fit.factor == factor
    assert fit.bpm == pytest.approx(90 * factor, rel=0.01)


def hats_on(beats, per_beat):
    return [b + i * (beats[1] - beats[0]) / per_beat for b in beats[:-1] for i in range(per_beat)]


def test_auto_doubles_slow_tempos():
    beats = grid(65, 64)
    assert fit_tempo_map(beats, beats[::4], "auto").factor == 2.0
    beats = grid(95, 64)
    assert fit_tempo_map(beats, beats[::4], "auto").factor == 1.0


def test_auto_doubles_when_hats_run_in_sixteenths():
    beats = grid(80, 64)
    assert fit_tempo_map(beats, beats[::4], "auto", hats=hats_on(beats, 4)).factor == 2.0
    assert fit_tempo_map(beats, beats[::4], "auto", hats=hats_on(beats, 2)).factor == 1.0


def test_auto_does_not_double_past_170_bpm():
    beats = grid(90, 64)
    assert fit_tempo_map(beats, beats[::4], "auto", hats=hats_on(beats, 4)).factor == 1.0


def test_tick_at_inverts_seconds():
    tm = TempoMap(480, [(0, 120.0), (1920, 90.0), (5000, 140.0)], [])
    for tick in (0, 100, 1920, 3000, 7000):
        assert tm.tick_at(tm.seconds(tick)) == pytest.approx(tick)


def test_anchors_shift_grid_onto_transients():
    beats = grid(100, 64)
    onsets = [b - 0.011 for b in beats]
    fit = fit_tempo_map(beats, beats[::4], "normal", anchors=onsets)
    assert np.median(fitted_times(fit) - np.array(onsets)) == pytest.approx(0, abs=0.001)


def test_downbeat_right_at_the_start_moves_into_the_lead_in():
    beats = grid(92, 64, start=0.02)
    fit = fit_tempo_map(beats, beats[::4], "normal", anchors=[b - 0.01 for b in beats])
    tm = fit.tempo_map
    assert all(30 < bpm < 300 for _, bpm in tm.tempos)
    assert tm.seconds(fit.beat_ticks[0]) == pytest.approx(beats[4] - 0.01, abs=0.002)
