import mido
import pytest

from trackyard import chartfile
from trackyard.tempomap import TempoMap

TM = TempoMap(480, [(0, 120.0)], [(0, 4, 4)])
LEVELS = {"expert": [(0, "kick"), (480, "yellow-tom"), (960, "blue-cym"), (1920, "red")], "hard": [(0, "kick"), (1920, "red")]}


def midi_song(folder, levels=LEVELS):
    chartfile.create(folder, "Song", TempoMap(480, [(0, 120.0)], [(0, 4, 4)]), levels, list(range(0, 3840, 480)), [0, 1920])
    (folder / "song.ini").write_text("[song]\nname = Song\ndelay = 0\nartist = Band\n")
    return folder


def chart_song(folder):
    (folder / "notes.chart").write_text(
        "[Song]\n{\n  Resolution = 192\n  Offset = 0.5\n}\n"
        "[SyncTrack]\n{\n  0 = TS 4\n  0 = B 120000\n  768 = B 140000\n}\n"
        "[ExpertSingle]\n{\n  192 = N 0 0\n}\n"
        "[ExpertDrums]\n{\n  0 = S 2 768\n  0 = N 0 0\n  192 = N 1 0\n  192 = N 34 0\n  384 = N 2 0\n  384 = N 35 0\n}\n")
    (folder / "song.ini").write_text("[song]\nname = Song\npro_drums = True\n")
    return folder


@pytest.fixture(params=["mid", "chart"])
def song(request, tmp_path):
    return midi_song(tmp_path) if request.param == "mid" else chart_song(tmp_path)


def test_write_then_read_gives_the_same_notes(song):
    levels = {"expert": [(0, "kick"), (192, "red"), (384, "yellow-tom"), (576, "green-cym")],
              "medium": [(0, "kick"), (384, "yellow-tom")]}
    chartfile.save(song, levels)
    chart = chartfile.read(song)
    assert chart.levels["expert"] == levels["expert"] and chart.levels["medium"] == levels["medium"]
    assert (song / (chartfile.path(song).name + ".orig")).exists()


def test_tempo_and_offset_round_trip(song):
    before = chartfile.read(song)
    chartfile.save(song, tempos=[(0, 100.0), (384, 90.25)], offset=0.012)
    chart = chartfile.read(song)
    assert [(t, round(b, 3)) for t, b in chart.tempo_map.tempos] == [(0, 100.0), (384, 90.25)]
    assert chart.tempo_map.signatures == [(0, 4, 4)]
    assert chart.offset == pytest.approx(0.012)
    assert chart.levels == before.levels  # notes keep their ticks


def test_difficulties_not_saved_keep_their_notes(song):
    before = chartfile.read(song)
    chartfile.save(song, {"easy": [(0, "kick")]})
    after = chartfile.read(song)
    assert after.levels["expert"] == before.levels["expert"] and after.levels["easy"] == [(0, "kick")]


def test_midi_keeps_other_tracks_and_star_power(tmp_path):
    midi_song(tmp_path)
    mid = mido.MidiFile(tmp_path / "notes.mid")
    mid.tracks.append(mido.MidiTrack([mido.MetaMessage("track_name", name="PART GUITAR"),
                                      mido.Message("note_on", note=96, velocity=100, time=480),
                                      mido.Message("note_off", note=96, velocity=0, time=120)]))
    drums = next(t for t in mid.tracks if t.name == "PART DRUMS")
    drums.insert(1, mido.Message("note_on", note=116, velocity=100, time=0))
    drums.insert(2, mido.Message("note_off", note=116, velocity=0, time=0))
    mid.save(tmp_path / "notes.mid")
    chartfile.save(tmp_path, {"expert": [(240, "red")]}, tempos=[(0, 90.0)])
    mid = mido.MidiFile(tmp_path / "notes.mid")
    guitar = next(t for t in mid.tracks if t.name == "PART GUITAR")
    assert [m.note for m in guitar if m.type == "note_on"] == [96]
    drums = next(t for t in mid.tracks if t.name == "PART DRUMS")
    assert 116 in [m.note for m in drums if m.type == "note_on"]
    assert sum(m.type == "set_tempo" for t in mid.tracks for m in t) == 1
    assert all(t[-1].type == "end_of_track" for t in mid.tracks)
    assert mid.tracks[0].name == "Song"


def test_chart_keeps_other_sections_and_valid_flags(tmp_path):
    chart_song(tmp_path)
    chartfile.save(tmp_path, {"expert": [(192, "red"), (576, "yellow-cym")]})
    text = (tmp_path / "notes.chart").read_text()
    assert "[ExpertSingle]\n{\n  192 = N 0 0\n}" in text
    assert "0 = S 2 768" in text
    assert "192 = N 34 0" in text  # red accent, red note still there
    assert "384 = N 35 0" not in text  # yellow accent whose note is gone
    assert chartfile.read(tmp_path).levels["expert"] == [(192, "red"), (576, "yellow-cym")]


def test_offset_comes_from_the_ini_with_the_chart_as_fallback(tmp_path):
    chart_song(tmp_path)
    assert chartfile.read(tmp_path).offset == 0.5
    assert chartfile.read(tmp_path, {"delay": "-2000"}).offset == -2.0
    chartfile.save(tmp_path, offset=0.0)
    assert chartfile.read(tmp_path).offset == 0.0  # the .chart Offset is rewritten as well


def test_ini_delay_is_added_or_replaced(tmp_path):
    midi_song(tmp_path)
    ini = tmp_path / "song.ini"
    ini.write_text("[Song]\nname = X\n")
    chartfile.save(tmp_path, offset=-0.0204)
    assert ini.read_text().splitlines() == ["[Song]", "delay = -20", "name = X"]
    chartfile.save(tmp_path, offset=0.1)
    assert "delay = 100" in ini.read_text() and ini.read_text().count("delay") == 1


@pytest.mark.parametrize("levels, message", [
    ({"expert": [(0, "blue-cym"), (0, "blue-tom")]}, "two blue notes"),
    ({"expert": [(0, "blue-cym")], "hard": [(0, "blue-tom")]}, "cymbal on one difficulty"),
    ({"expert": [(0, "purple")]}, "bad note"),
    ({"expert": [(-1, "red")]}, "bad note"),
    ({"insane": [(0, "red")]}, "unknown difficulty"),
])
def test_save_refuses_what_a_chart_cannot_hold(song, levels, message):
    with pytest.raises(ValueError, match=message):
        chartfile.save(song, levels)
