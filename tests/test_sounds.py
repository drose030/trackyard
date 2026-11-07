import numpy as np
import pytest
import soundfile as sf

from trackyard import chartfile, sounds
from trackyard.tempomap import TempoMap

SR = 44100
KICKS = [1.0, 2.0, 3.0]
SNARES = [2.08]


def hit(audio, at, freq, decay):
    n = int(0.8 * SR)
    t = np.arange(n) / SR
    a = int(at * SR)
    audio[a:a + n] += 0.6 * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)


@pytest.fixture
def song(tmp_path):
    audio = np.zeros(int(4 * SR))
    for at in KICKS:
        hit(audio, at, 60, 12)
    for at in SNARES:
        hit(audio, at, 200, 30)
    sf.write(tmp_path / "drums.wav", np.stack([audio, audio], 1).astype(np.float32), SR)
    chartfile.create(tmp_path, "Song", TempoMap(480, [(0, 120)], []), {"expert": [(960, "kick")]}, [0], [0])
    (tmp_path / "song.ini").write_text("[song]\nname = Song\n")
    return tmp_path


NOTES = [(t, "kick") for t in KICKS] + [(t, "red") for t in SNARES]


def test_voices_follow_beat_train_rows():
    assert [sounds.voice(lane) for lane in ("kick", "red", "yellow-cym", "blue-tom", "green-cym", "green-tom")] == \
        ["kick", "red", "yellow", "blue", "orange", "green"]


def test_candidates_start_at_the_hit_and_put_clean_hits_first(song):
    stem = sounds.DrumStem(song)
    cuts = stem.candidates(NOTES, "kick")
    assert [c.at for c in cuts][-1] == 2.0  # the kick with a snare 80 ms later comes last
    for c in cuts:
        assert c.at - 0.0015 <= c.start <= c.at
    for c in cuts[:-1]:
        assert 0.2 < c.length <= sounds.MAX_SECONDS["kick"]
    crowded = cuts[-1]
    assert crowded.start + crowded.length <= 2.08  # stops before the snare
    assert stem.candidates(NOTES, "orange") == []


def test_a_cut_fades_in_and_out(song):
    stem = sounds.DrumStem(song)
    cut = stem.candidates(NOTES, "kick")[0]
    out = stem.render(cut)
    assert len(out) == int(cut.length * SR)
    assert abs(out[0]).max() < 0.01 and abs(out[-1]).max() < 0.01
    assert abs(out).max() > 0.3


def test_saving_writes_and_removes_files(song):
    stem = sounds.DrumStem(song)
    cut = stem.candidates(NOTES, "kick")[0]
    sounds.save(song, {"kick": {"start": cut.start, "length": cut.length}}, stem)
    info = sf.info(song / "sounds" / "kick.wav")
    assert info.subtype == "PCM_16" and info.samplerate == SR
    assert sounds.picks(song) == {"kick": {"start": cut.start, "length": cut.length}}

    (song / "sounds" / "red.ogg").write_bytes(b"")  # put there by hand
    assert sounds.picks(song)["red"] == {"file": "red.ogg"}

    sounds.save(song, {"kick": None, "red": None}, stem)
    assert not (song / "sounds").exists()
    assert sounds.picks(song) == {}


def test_save_refuses_unknown_voices(song):
    with pytest.raises(ValueError, match="unknown voice"):
        sounds.save(song, {"purple": None}, sounds.DrumStem(song))


def stem_with(tmp_path, hits):
    """hits: (time, freq, decay, amplitude)."""
    audio = np.zeros(int(5 * SR))
    for at, freq, decay, amp in hits:
        n = int(0.8 * SR)
        t = np.arange(n) / SR
        a = int(at * SR)
        audio[a:a + n] += amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
    sf.write(tmp_path / "drums.wav", np.stack([audio, audio], 1).astype(np.float32), SR)
    chartfile.create(tmp_path, "Song", TempoMap(480, [(0, 120)], []), {"expert": [(960, "kick")]}, [0], [0])
    (tmp_path / "song.ini").write_text("[song]\nname = Song\n")
    return sounds.DrumStem(tmp_path)


def test_hits_shared_with_other_rows_are_still_offered_fewest_first(tmp_path):
    stem = stem_with(tmp_path, [(t, 60, 12, 0.6) for t in (1.0, 2.0, 3.0)] + [(1.0, 5000, 80, 0.2), (2.0, 5000, 80, 0.2)])
    notes = [(t, "kick") for t in (1.0, 2.0, 3.0)] + [(1.0, "yellow-cym"), (2.0, "yellow-cym")]
    assert [c.at for c in stem.candidates(notes, "kick")] == [3.0, 1.0, 2.0]
    only_shared = [(t, "kick") for t in (1.0, 2.0)] + [(1.0, "yellow-cym"), (2.0, "yellow-cym")]
    assert len(stem.candidates(only_shared, "kick")) == 2


def test_ghost_notes_rank_below_ordinary_hits(tmp_path):
    stem = stem_with(tmp_path, [(1.0, 60, 12, 0.6), (2.0, 60, 12, 0.08), (3.0, 60, 12, 0.5)])
    cuts = stem.candidates([(t, "kick") for t in (1.0, 2.0, 3.0)], "kick")
    assert cuts[-1].at == 2.0


def test_a_cut_ends_before_the_next_hit_of_any_row(tmp_path):
    stem = stem_with(tmp_path, [(1.0, 60, 4, 0.6), (1.3, 3000, 60, 0.4)])
    cut = stem.candidates([(1.0, "kick"), (1.3, "red")], "kick")[0]
    assert cut.start + cut.length <= 1.3 - 0.004


def test_a_cut_starts_at_the_attack_in_the_stem_not_at_the_chart_time(tmp_path):
    stem = stem_with(tmp_path, [(1.012, 60, 12, 0.6)])  # the chart says 1.000
    cut = stem.candidates([(1.0, "kick")], "kick")[0]
    assert 1.0105 <= cut.start <= 1.012
    out = stem.render(cut)
    assert abs(out[0]).max() < 0.01 and abs(out[: int(0.004 * SR)]).max() > 0.1
