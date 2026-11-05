import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from trackyard import chartfile
from trackyard.chart import load_chart
from trackyard.export import SongInfo, write_ini, write_ogg
from trackyard.notes import ChartNote
from trackyard.songfolder import read_ini
from trackyard.tempomap import TempoMap

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "beattrain-check"

TM = TempoMap(480, [(0, 100.0), (1920, 120.0)], [(0, 4, 4), (7680, 3, 4)])
NOTES = [
    ChartNote(1920, "kick", 0.9, 0, "KD"),
    ChartNote(1920, "yellow-cym", 0.9, 0, "HH"),
    ChartNote(2160, "red", 0.9, 0, "SD"),
    ChartNote(2400, "yellow-tom", 0.9, 0, "TT"),
    ChartNote(2520, "blue-tom", 0.9, 0, "TT"),
    ChartNote(2640, "green-tom", 0.9, 0, "TT"),
    ChartNote(2880, "blue-cym", 0.9, 0, "CY"),
    ChartNote(2880, "kick", 0.9, 0, "KD"),
    ChartNote(3000, "green-cym", 0.9, 0, "CY"),
]
LOWER = {"hard": NOTES[:7], "medium": NOTES[:4], "easy": [NOTES[0], NOTES[2]]}
BEATS = list(range(0, 9600, 480))
DOWNS = [0, 1920, 3840, 5760, 7680, 9120]


@pytest.fixture
def song(tmp_path):
    levels = {lvl: [(n.tick, n.lane) for n in notes] for lvl, notes in {"expert": NOTES, **LOWER}.items()}
    chartfile.create(tmp_path, "Test", TM, levels, BEATS, DOWNS)
    write_ini(tmp_path / "song.ini", SongInfo(name="Test", artist="Band", length_ms=10000), NOTES)
    return tmp_path


def test_midi_round_trip_through_our_reader(song):
    ref = load_chart(song, {})
    got = sorted((round(h.time, 6), h.lane) for h in ref.hits)
    want = sorted((round(TM.seconds(n.tick), 6), n.lane) for n in NOTES)
    assert got == want
    assert [t for t, _ in ref.tempo_map.tempos] == [t for t, _ in TM.tempos]
    assert [b for _, b in ref.tempo_map.tempos] == pytest.approx([b for _, b in TM.tempos], rel=1e-6)
    assert [(n, d) for _, n, d in ref.tempo_map.signatures] == [(4, 4), (3, 4)]


@pytest.mark.parametrize("level", ["hard", "medium", "easy"])
def test_lower_difficulties_round_trip(song, level):
    ref = load_chart(song, {}, level)
    assert sorted((round(h.time, 6), h.lane) for h in ref.hits) == sorted(
        (round(TM.seconds(n.tick), 6), n.lane) for n in LOWER[level])


def test_song_ini(song):
    values = read_ini(song / "song.ini")
    assert values["name"] == "Test" and values["artist"] == "Band"
    assert values["pro_drums"] == "True" and values["song_length"] == "10000"


def test_ogg_is_readable(tmp_path):
    sr = 44100
    tone = (0.3 * np.sin(np.linspace(0, 2000, sr * 2))).astype(np.float32)
    write_ogg(tmp_path / "a.ogg", np.stack([tone, tone], 1), sr)
    back, back_sr = sf.read(tmp_path / "a.ogg")
    assert back_sr == sr and back.shape[1] == 2 and abs(len(back) - 2 * sr) < 2048


@pytest.fixture(scope="session")
def beattrain_check():
    if shutil.which("swift") is None or not (CHECK / "Sources" / "BeatTrainCore").exists():
        pytest.skip("needs swift and the Beat-Train-MCP-Sample repo next to this one")
    subprocess.run(["swift", "build", "-c", "release"], cwd=CHECK, check=True, capture_output=True)
    return CHECK / ".build" / "release" / "beattrain-check"


@pytest.mark.parametrize("level", ["expert", "hard", "medium", "easy"])
def test_beat_train_reads_exported_chart(song, beattrain_check, level):
    out = subprocess.run([beattrain_check, song / "notes.mid", level], check=True, capture_output=True).stdout
    data = json.loads(out)
    assert sorted(data["difficulties"]) == ["easy", "expert", "hard", "medium"]
    got = sorted((n["tick"], n["lane"], n["row"]) for n in data["notes"])
    # Beat-Train's highway: kick and red share lane 0, yellow lane 1, blue lane 2,
    # green cymbal (orange pad) on top of lane 3 and floor tom (green pad) below it.
    where = {"kick": (0, "bottom"), "red": (0, "top"), "yellow": (1, "top"), "blue": (2, "top"),
             "green-cym": (3, "top"), "green-tom": (3, "bottom")}
    notes = NOTES if level == "expert" else LOWER[level]
    assert got == sorted((n.tick, *where.get(n.lane, where.get(n.lane.split("-")[0], ()))) for n in notes)
    for n in data["notes"]:
        assert n["time"] == pytest.approx(TM.seconds(n["tick"]), abs=1e-6)


def test_ini_delay_and_chart_offset_are_not_added(tmp_path):
    (tmp_path / "notes.chart").write_text(
        "[Song]\n{\n  Resolution = 192\n  Offset = -2\n}\n[SyncTrack]\n{\n  0 = TS 4\n  0 = B 120000\n}\n"
        "[ExpertDrums]\n{\n  768 = N 0 0\n}\n"
    )
    assert load_chart(tmp_path, {"delay": "-2000"}).hits[0].time == pytest.approx(0.0)
    assert load_chart(tmp_path, {}).hits[0].time == pytest.approx(0.0)
    assert load_chart(tmp_path, {"delay": "500"}).hits[0].time == pytest.approx(2.5)
