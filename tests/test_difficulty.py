import pytest

from trackyard.tempomap import TempoMap
from trackyard.difficulty import (Controls, all_levels, lower_levels, preset_settings, reduce, reduce_level,
                                  reduce_with)
from trackyard.notes import ChartNote

PPQ = 480
SEC_PER_TICK = 0.5 / PPQ  # 120 BPM


def note(tick, lane, conf, cls, sec_per_tick=SEC_PER_TICK):
    return ChartNote(tick, lane, conf, tick * sec_per_tick, cls)


def groove(bars=4):
    """Expert rock beat: 16th hi-hats, kick on 1, 1-e-and of 3, snare on 2 and 4."""
    notes = []
    for bar in range(bars):
        t0 = bar * 4 * PPQ
        for i in range(16):
            notes.append(note(t0 + i * PPQ // 4, "yellow-cym", 0.8, "HH"))
        for pos in (0, 8 * PPQ // 4, 9 * PPQ // 4, 10 * PPQ // 4):
            notes.append(note(t0 + pos, "kick", 0.8, "KD"))
        for beat in (1, 3):
            notes.append(note(t0 + beat * PPQ, "red", 0.9, "SD"))
    return sorted(notes, key=lambda n: n.tick)


def lanes_at(notes, lane):
    return sorted(n.tick for n in notes if n.lane == lane)


@pytest.mark.parametrize("preset", ["clone-hero", "mpc"])
def test_levels_get_sparser_and_keep_the_backbeat(preset):
    expert = groove()
    levels = all_levels(expert, PPQ, {b * 4 * PPQ for b in range(4)}, preset_settings(preset))
    counts = [len(levels[k]) for k in ("expert", "hard", "medium", "easy")]
    assert counts == sorted(counts, reverse=True) and counts[-1] < counts[0] / 2
    for lvl in ("hard", "medium", "easy"):
        assert lanes_at(levels[lvl], "red") == lanes_at(expert, "red")


def test_hard_has_no_sixteenth_hats():
    hard = reduce(groove(1), "hard", PPQ)
    assert all(t % (PPQ // 2) == 0 for t in lanes_at(hard, "yellow-cym"))
    assert len(lanes_at(hard, "yellow-cym")) == 8


def test_easy_one_hand_and_one_kick_per_bar():
    easy = reduce(groove(4), "easy", PPQ, {b * 4 * PPQ for b in range(4)})
    by_tick = {}
    for n in easy:
        by_tick.setdefault(n.tick, []).append(n.lane)
    assert all(sum(lane != "kick" for lane in lanes) <= 1 for lanes in by_tick.values())
    assert lanes_at(easy, "kick") == [b * 4 * PPQ for b in range(4)]


@pytest.mark.parametrize("preset", ["clone-hero", "mpc"])
def test_reduction_only_drops_notes(preset):
    expert = groove()
    keys = {(n.tick, n.lane) for n in expert}
    for notes in all_levels(expert, PPQ, None, preset_settings(preset)).values():
        assert {(n.tick, n.lane) for n in notes} <= keys


def test_medium_hats_stay_playable_at_double_tempo():
    # A chart at 160 BPM: half-beat hats are 0.19 s apart, faster than Medium allows.
    fast = [note(i * PPQ // 2, "yellow-cym", 0.8, "HH", sec_per_tick=60 / 160 / PPQ) for i in range(32)]
    hats = [n.time for n in reduce(fast, "medium", PPQ)]
    assert min(b - a for a, b in zip(hats, hats[1:])) >= 0.25 - 1e-9


def test_lower_levels_time_notes_by_the_given_tempo_map():
    hats = [(i * PPQ // 2, "yellow-cym") for i in range(32)]
    medium = {lvl: {"preset": "clone-hero"} for lvl in ("hard", "medium", "easy")}
    at = {bpm: lower_levels(hats, TempoMap(PPQ, [(0, bpm)], []), medium)["medium"] for bpm in (100, 160)}
    assert len(at[160]) < len(at[100]) == len(hats)
    assert set(at[160]) <= set(hats)


def controls(**kw):
    base = dict(density=1.0, weights={}, finest=8, triplets=True, max_hands=2, max_notes=3, min_gap_ms=0)
    return Controls(**{**base, **kw})


def test_full_controls_keep_expert():
    expert = groove()
    key = lambda n: (n.tick, n.lane)  # noqa: E731
    assert sorted(reduce_with(expert, controls(), PPQ), key=key) == sorted(expert, key=key)


def test_density_thins_hats_by_metrical_position():
    hats = lanes_at(reduce_with(groove(1), controls(density=0.5), PPQ), "yellow-cym")
    assert hats == list(range(0, 4 * PPQ, PPQ // 2))
    # 70 %: still eighths, no scattered sixteenths.
    hats = lanes_at(reduce_with(groove(1), controls(density=0.7), PPQ), "yellow-cym")
    assert hats == list(range(0, 4 * PPQ, PPQ // 2))
    hats = lanes_at(reduce_with(groove(1), controls(density=0.25), PPQ), "yellow-cym")
    assert hats == list(range(0, 4 * PPQ, PPQ))


def test_instrument_weights_and_off():
    out = reduce_with(groove(1), controls(density=0.5, weights={"snare": 2.0, "hihat": 0.0}), PPQ)
    assert lanes_at(out, "yellow-cym") == []
    assert lanes_at(out, "red") == [PPQ, 3 * PPQ]


def test_grid_hands_and_gap_limits():
    out = reduce_with(groove(1), controls(finest=2), PPQ)
    assert all(n.tick % (PPQ // 2) == 0 for n in out)
    out = reduce_with(groove(1), controls(max_hands=1, max_notes=2), PPQ)
    for tick in {n.tick for n in out}:
        lanes = [n.lane for n in out if n.tick == tick]
        assert sum(lane != "kick" for lane in lanes) <= 1 and len(lanes) <= 2
    assert lanes_at(out, "red") == [PPQ, 3 * PPQ]  # the snare wins over the hat
    out = reduce_with(groove(1), controls(min_gap_ms=300), PPQ)  # 120 BPM: an eighth is 250 ms
    hands = sorted({n.time for n in out if n.lane != "kick"})
    assert min(b - a for a, b in zip(hands, hands[1:])) >= 0.3 - 1e-9


def test_settings_round_trip_and_clone_hero_preset():
    expert = groove()
    for level, settings in preset_settings("mpc").items():
        assert Controls.from_dict(settings).to_dict() == {k: v for k, v in settings.items() if k != "preset"}
    assert reduce_level(expert, "medium", PPQ, None, {"preset": "clone-hero"}) == reduce(expert, "medium", PPQ)
