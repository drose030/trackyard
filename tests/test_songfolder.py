import pytest

from trackyard.songfolder import SongFolder, find_songs, songs_dir, write_sidecar
from trackyard.notes import ChartNote


def song(folder, *files):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "song.ini").write_text("[song]\nname = Song\n")
    (folder / "notes.mid").write_bytes(b"")
    for name in files:
        (folder / name).write_bytes(b"")
    return folder


def test_open_needs_song_ini_and_a_chart(tmp_path):
    (tmp_path / "B").mkdir()
    (tmp_path / "B" / "song.ini").write_text("[song]\n")
    with pytest.raises(FileNotFoundError, match="notes.mid or notes.chart"):
        SongFolder.open(tmp_path / "B")
    assert SongFolder.open(song(tmp_path / "A")).ini["name"] == "Song"
    assert [s.path.name for s in find_songs([tmp_path])] == ["A"]


def test_audio_has_a_role_per_file(tmp_path):
    folder = song(tmp_path, "drums_1.ogg", "song.ogg", "crowd.ogg", "preview.ogg", "guitar.opus", "cover.png")
    assert SongFolder.open(folder).audio() == {
        "crowd.ogg": "crowd", "drums_1.ogg": "drums", "guitar.opus": "mix", "song.ogg": "mix"}


def test_songs_go_into_beattrain_songs_on_an_sd_card(tmp_path):
    assert songs_dir(tmp_path) == tmp_path  # an ordinary folder
    assert songs_dir(tmp_path, sd_card=True) == tmp_path / "BeatTrain" / "Songs"
    assert songs_dir(tmp_path) == tmp_path / "BeatTrain" / "Songs"  # now it looks like one


def test_sidecar_keeps_notes_when_level_settings_change(tmp_path):
    folder = song(tmp_path)
    write_sidecar(folder, [ChartNote(480, "kick", 0.25, 0.5, "KD")], {"tempo": "auto"}, {"hard": {"preset": "mpc"}})
    s = SongFolder.open(folder)
    s.save_level_settings({"easy": {"preset": "clone-hero"}})
    assert s.confidence() == {(480, "kick"): 0.25}
    assert s.sidecar()["levels"] == {"hard": {"preset": "mpc"}, "easy": {"preset": "clone-hero"}}
