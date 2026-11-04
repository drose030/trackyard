import mido
import pytest

from trackyard.chart import TempoMap, read_notes
from trackyard.chartedit import save_tempo, set_ini_delay
from trackyard.export import write_midi
from trackyard.notes import ChartNote
from trackyard.songfolder import read_ini

NOTES = [ChartNote(0, "kick", 0.9, 0.0, "KD"), ChartNote(1920, "red", 0.9, 1.0, "SD")]


def midi_song(folder):
    tm = TempoMap(480, [(0, 120.0)], [(0, 4, 4)])
    write_midi(folder / "notes.mid", "Song", tm, NOTES, list(range(0, 3840, 480)), [0, 1920])
    (folder / "song.ini").write_text("[song]\nname = Song\ndelay = 0\nartist = Band\n")
    return folder


def test_midi_tempo_and_delay_round_trip(tmp_path):
    song = midi_song(tmp_path)
    save_tempo(song, [(0, 120.0), (960, 93.5)], 0.012)
    ini = read_ini(song / "song.ini")
    chart = read_notes(song, ini, "expert")
    assert [(t, round(b, 3)) for t, b in chart.tempo_map.tempos] == [(0, 120.0), (960, 93.5)]
    assert chart.offset == pytest.approx(0.012)
    assert sorted(chart.notes) == sorted((n.tick, n.lane) for n in NOTES)  # notes keep their ticks
    assert chart.tempo_map.signatures == [(0, 4, 4)]
    assert ini["artist"] == "Band" and (song / "notes.mid.orig").exists()
    mid = mido.MidiFile(song / "notes.mid")
    assert sum(m.type == "set_tempo" for t in mid.tracks for m in t) == 2
    assert all(t[-1].type == "end_of_track" for t in mid.tracks)


def test_chart_tempo_and_offset(tmp_path):
    (tmp_path / "notes.chart").write_text(
        "[Song]\n{\n  Resolution = 192\n  Offset = 0.5\n}\n"
        "[SyncTrack]\n{\n  0 = TS 4\n  0 = B 120000\n  768 = B 140000\n}\n"
        "[ExpertDrums]\n{\n  0 = N 0 0\n  192 = N 1 0\n}\n")
    (tmp_path / "song.ini").write_text("[song]\nname = Song\n")
    save_tempo(tmp_path, [(0, 100.0), (384, 90.25)], 0.0)
    chart = read_notes(tmp_path, read_ini(tmp_path / "song.ini"), "expert")
    assert chart.tempo_map.tempos == [(0, 100.0), (384, 90.25)]
    assert chart.tempo_map.signatures == [(0, 4, 4)]
    assert chart.offset == 0.0  # the .chart Offset is rewritten as well
    assert sorted(chart.notes) == [(0, "kick"), (192, "red")]


def test_ini_delay_is_added_or_replaced(tmp_path):
    ini = tmp_path / "song.ini"
    ini.write_text("[Song]\nname = X\n")
    set_ini_delay(ini, -0.0204)
    assert ini.read_text().splitlines() == ["[Song]", "delay = -20", "name = X"]
    set_ini_delay(ini, 0.1)
    assert "delay = 100" in ini.read_text() and ini.read_text().count("delay") == 1
