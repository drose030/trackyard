import os

from trackyard import audio


def test_ffmpeg_falls_back_to_the_bundled_binary(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    exe = audio.ffmpeg()
    assert os.path.isfile(exe) and os.access(exe, os.X_OK)
