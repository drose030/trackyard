import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import mido
import numpy as np
import pytest
import soundfile as sf

from trackyard.chart import TempoMap, read_notes
from trackyard.chartedit import replace_drums_chart, save_drums
import time

from trackyard.editor.server import App, make_handler
from trackyard.export import write_midi
from trackyard.notes import ChartNote

TM = TempoMap(480, [(0, 120.0)], [(0, 4, 4)])


def midi_with_guitar(path):
    notes = [ChartNote(480, "kick", 0.3, 0.5, "KD"), ChartNote(960, "yellow-tom", 0.9, 1.0, "TT")]
    write_midi(path / "notes.mid", "Song", TM, notes, list(range(0, 4800, 480)), [0, 1920, 3840],
               {"hard": notes[:1]})
    mid = mido.MidiFile(path / "notes.mid")
    guitar = mido.MidiTrack([mido.MetaMessage("track_name", name="PART GUITAR"),
                             mido.Message("note_on", note=96, velocity=100, time=480),
                             mido.Message("note_off", note=96, velocity=0, time=120)])
    mid.tracks.append(guitar)
    drums = next(t for t in mid.tracks if t.name == "PART DRUMS")
    # A star power phrase at tick 0; zero length so the following delta times stay valid.
    drums.insert(1, mido.Message("note_on", note=116, velocity=100, time=0))
    drums.insert(2, mido.Message("note_off", note=116, velocity=0, time=0))
    mid.save(path / "notes.mid")
    (path / "song.ini").write_text("[song]\nname = Song\nartist = Band\n")
    (path / "trackyard.json").write_text(json.dumps({"notes": [{"tick": 480, "lane": "kick", "confidence": 0.3}]}))


def test_save_replaces_only_drum_notes(tmp_path):
    midi_with_guitar(tmp_path)
    save_drums(tmp_path, {"expert": [(240, "red"), (480, "blue-tom"), (960, "green-cym")], "hard": [(240, "red")]})
    assert read_notes(tmp_path, {}, "expert").notes == [(240, "red"), (480, "blue-tom"), (960, "green-cym")]
    assert read_notes(tmp_path, {}, "hard").notes == [(240, "red")]
    assert read_notes(tmp_path, {}, "medium").notes == []
    mid = mido.MidiFile(tmp_path / "notes.mid")
    guitar = next(t for t in mid.tracks if t.name == "PART GUITAR")
    assert [m.note for m in guitar if m.type == "note_on"] == [96]
    drums = next(t for t in mid.tracks if t.name == "PART DRUMS")
    assert 116 in [m.note for m in drums if m.type == "note_on"]
    assert (tmp_path / "notes.mid.orig").exists()


def test_save_chart_keeps_other_sections_and_valid_flags(tmp_path):
    chart = tmp_path / "notes.chart"
    chart.write_text(
        "[Song]\n{\n  Resolution = 192\n}\n[SyncTrack]\n{\n  0 = B 120000\n}\n"
        "[ExpertSingle]\n{\n  192 = N 0 0\n}\n"
        "[ExpertDrums]\n{\n  0 = S 2 768\n  192 = N 1 0\n  192 = N 34 0\n  384 = N 2 0\n  384 = N 35 0\n}\n"
    )
    replace_drums_chart(chart, {"expert": [(192, "red"), (576, "yellow-cym")]})
    text = chart.read_text()
    assert "[ExpertSingle]\n{\n  192 = N 0 0\n}" in text
    assert "0 = S 2 768" in text
    assert "192 = N 34 0" in text  # red accent, red note still there
    assert "384 = N 35 0" not in text  # yellow accent whose note is gone
    assert read_notes(tmp_path, {"pro_drums": "True"}, "expert").notes == [(192, "red"), (576, "yellow-cym")]


def make_song(folder):
    folder.mkdir(parents=True, exist_ok=True)
    midi_with_guitar(folder)
    tone = (0.2 * np.sin(np.linspace(0, 500, 44100))).astype(np.float32)
    sf.write(folder / "drums.ogg", np.stack([tone, tone], 1), 44100, format="OGG", subtype="VORBIS")
    (folder / "secret.txt").write_text("no")
    return folder


def start(app, picker=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, picker or (lambda kind, start: None)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def server(tmp_path):
    song = make_song(tmp_path / "Song")
    httpd, url = start(App(song, tmp_path / "cache", settings_path=tmp_path / "settings.json"))
    yield url
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def post(url, data):
    req = urllib.request.Request(url, json.dumps(data).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_song_api(server):
    song = json.loads(get(server + "/api/song")[1])
    assert song["name"] == "Song" and song["ppq"] == 480 and song["editable"]
    assert song["levels"]["expert"] == [[480, "kick"], [960, "yellow-tom"]]
    assert song["confidence"] == {"480:kick": 0.3}
    assert song["stems"] == ["drums.ogg"]
    assert get(server + "/")[0] == 200 and get(server + "/editor.js")[0] == 200


def test_only_stems_are_served(server):
    assert get(server + "/audio/drums.ogg")[0] == 200
    for bad in ("/audio/secret.txt", "/audio/..%2Fsong.ini", "/../song.ini", "/audio/notes.mid"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(server + bad)
        assert e.value.code == 404


def test_save_and_reduce_api(server):
    expert = [[t, "yellow-cym"] for t in range(0, 7680, 120)] + [[t, "red"] for t in range(480, 7680, 960)]
    levels = post(server + "/api/reduce", {"expert": expert})
    assert 0 < len(levels["easy"]) < len(levels["medium"]) <= len(levels["hard"]) < len(expert)
    assert post(server + "/api/save", {"levels": {"expert": expert, **levels}})["file"] == "notes.mid"
    song = json.loads(get(server + "/api/song")[1])
    assert len(song["levels"]["expert"]) == len(expert)
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/save", {"levels": {"expert": [[0, "purple"]]}})
    assert e.value.code == 400



def test_level_settings_drive_reduce_and_are_saved(server, tmp_path):
    song = json.loads(get(server + "/api/song")[1])
    assert song["level_settings"]["medium"] == {"preset": "clone-hero"}  # no settings stored yet
    mpc = song["presets"]["mpc"]
    expert = [[t, "yellow-cym"] for t in range(0, 7680, 120)] + [[t, "red"] for t in range(480, 7680, 960)]
    rules = post(server + "/api/reduce", {"expert": expert})
    thin = post(server + "/api/reduce", {"expert": expert, "settings": {"medium": {**mpc["medium"], "density": 0.25}}})
    assert len(thin["medium"]) < len(rules["medium"]) and thin["hard"] == rules["hard"]
    no_hats = {**mpc["easy"], "preset": "custom", "weights": {**mpc["easy"]["weights"], "hihat": 0}}
    levels = post(server + "/api/reduce", {"expert": expert, "settings": {"easy": no_hats}})
    assert {lane for _, lane in levels["easy"]} == {"red"}

    post(server + "/api/save", {"levels": {"expert": expert, **levels}, "settings": {"easy": no_hats}})
    sidecar = json.loads((tmp_path / "Song" / "trackyard.json").read_text())
    assert sidecar["levels"]["easy"]["weights"]["hihat"] == 0 and sidecar["notes"]
    song = json.loads(get(server + "/api/song")[1])
    assert song["level_settings"]["easy"]["preset"] == "custom"
    assert song["level_settings"]["hard"] == {"preset": "clone-hero"}
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/reduce", {"expert": expert, "settings": {"expert": mpc["easy"]}})
    assert e.value.code == 400


def test_save_tempo_map_and_offset(server, tmp_path):
    song = json.loads(get(server + "/api/song")[1])
    levels = song["levels"]
    post(server + "/api/save", {"levels": levels, "tempos": [[0, 120], [960, 100.5]], "offset": -0.03})
    song = json.loads(get(server + "/api/song")[1])
    assert [(t["tick"], round(t["bpm"], 3)) for t in song["tempos"]] == [(0, 120.0), (960, 100.5)]
    assert song["offset"] == pytest.approx(-0.03)
    assert song["levels"] == levels  # notes keep their ticks
    assert "delay = -30" in (tmp_path / "Song" / "song.ini").read_text()
    for bad in ([[480, 120]], [[0, 120], [0, 90]], [[0, 5]]):
        with pytest.raises(urllib.error.HTTPError) as e:
            post(server + "/api/save", {"levels": levels, "tempos": bad})
        assert e.value.code == 400

def test_start_without_song_then_open(tmp_path):
    song = make_song(tmp_path / "Band - Song")
    httpd, url = start(App(None, tmp_path / "cache", settings_path=tmp_path / "s.json"),
                       picker=lambda kind, start: str(song) if kind == "folder" else None)
    try:
        assert json.loads(get(url + "/api/state")[1])["song"] is False
        with pytest.raises(urllib.error.HTTPError) as e:
            get(url + "/api/song")
        assert e.value.code == 404
        assert post(url + "/api/pick", {"kind": "folder"}) == {"path": str(song), "cancelled": False}
        assert post(url + "/api/pick", {"kind": "audio"})["cancelled"]
        assert post(url + "/api/open", {"folder": str(song)})["name"] == "Song"
        state = json.loads(get(url + "/api/state")[1])
        assert state["song"] and state["version"] == 1
    finally:
        httpd.shutdown()


def test_convert_makes_a_draft_that_moves_on_first_save(tmp_path):
    mp3 = tmp_path / "in.mp3"
    mp3.write_bytes(b"fake")
    calls = []

    def fake_convert(src, out_root, log):
        calls.append((src, out_root))
        log("working")
        return make_song(out_root / "Band - Song")

    def wait(url):
        for _ in range(100):
            job = json.loads(get(url + "/api/job")[1])
            if job["state"] != "running":
                return job
            time.sleep(0.05)

    settings = tmp_path / "s.json"
    cache = tmp_path / "cache"
    httpd, url = start(App(None, cache, settings_path=settings, converter=fake_convert))
    try:
        job = post(url + "/api/convert", {"audio": str(mp3)})
        assert job["state"] in ("running", "done")
        job = wait(url)
        assert job["state"] == "done", job
        assert calls[0][0] == mp3 and calls[0][1].parent == (cache / "drafts").resolve()
        song = json.loads(get(url + "/api/song")[1])
        assert song["name"] == "Song" and song["draft"]
        levels = song["levels"]

        with pytest.raises(urllib.error.HTTPError) as e:  # a draft needs a destination
            post(url + "/api/save", {"levels": levels})
        assert e.value.code == 400
        library = tmp_path / "library"
        library.mkdir()
        saved = post(url + "/api/save", {"levels": levels, "dest": str(library)})
        target = library / "Band - Song"
        assert saved["folder"] == str(target.resolve())
        assert (target / "notes.mid").exists() and (target / "drums.ogg").exists()
        assert not list((cache / "drafts").iterdir())
        assert not json.loads(get(url + "/api/song")[1])["draft"]
        assert json.loads(settings.read_text())["out_dir"] == str(library.resolve())
        assert post(url + "/api/save", {"levels": levels})["folder"] == str(target.resolve())

        # A second draft with the same name asks before replacing the saved song.
        post(url + "/api/convert", {"audio": str(mp3)})
        assert wait(url)["state"] == "done"
        with pytest.raises(urllib.error.HTTPError) as e:
            post(url + "/api/save", {"levels": levels, "dest": str(library)})
        assert e.value.code == 409
        post(url + "/api/save", {"levels": levels, "dest": str(library), "overwrite": True})
        assert [p.name for p in library.iterdir()] == ["Band - Song"]

        post(url + "/api/settings", {"hide_hint": "levels"})
        assert post(url + "/api/settings", {"hide_hint": "levels"})["hidden_hints"] == ["levels"]
        with pytest.raises(urllib.error.HTTPError) as e:
            post(url + "/api/convert", {"audio": str(tmp_path / "missing.mp3")})
        assert e.value.code == 400
    finally:
        httpd.shutdown()


def test_save_as_copies_edits_and_keeps_editing_the_copy(tmp_path, server):
    song = json.loads(get(server + "/api/song")[1])
    original = tmp_path / "Song"
    before = (original / "notes.mid").read_bytes()
    levels = {**song["levels"], "expert": song["levels"]["expert"][:1]}  # an unsaved edit
    sd = tmp_path / "sd"
    (sd / "BeatTrain" / "Songs").mkdir(parents=True)
    saved = post(server + "/api/save_as", {"levels": levels, "dest": str(sd)})
    copy = sd / "BeatTrain" / "Songs" / "Song"
    assert saved["folder"] == str(copy.resolve())
    assert (original / "notes.mid").read_bytes() == before  # the original is untouched
    assert not list(copy.glob("*.orig"))
    opened = json.loads(get(server + "/api/song")[1])
    assert opened["folder"] == str(copy.resolve()) and len(opened["levels"]["expert"]) == 1
    assert json.loads(get(server + "/api/state")[1])["settings"]["export_dir"] == str(sd.resolve())

    with pytest.raises(urllib.error.HTTPError) as e:  # into the folder it is already in
        post(server + "/api/save_as", {"levels": levels, "dest": str(sd)})
    assert e.value.code == 400
    plain = tmp_path / "plain"
    (plain / "Song").mkdir(parents=True)
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/save_as", {"levels": levels, "dest": str(plain)})
    assert e.value.code == 409
    assert post(server + "/api/save_as", {"levels": levels, "dest": str(plain), "overwrite": True})["ok"]
    assert (plain / "Song" / "notes.mid").exists()
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server + "/api/save_as", {"levels": levels})
    assert e.value.code == 400


def test_audio_supports_range_requests(server):
    full = get(server + "/audio/drums.ogg")[1]
    req = urllib.request.Request(server + "/audio/drums.ogg", headers={"Range": "bytes=100-199"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == f"bytes 100-199/{len(full)}"
        assert r.read() == full[100:200]
    req = urllib.request.Request(server + "/audio/drums.ogg", headers={"Range": "bytes=50-"})
    with urllib.request.urlopen(req) as r:
        assert r.read() == full[50:]
