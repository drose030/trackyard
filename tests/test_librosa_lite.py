import numpy as np
import pytest
import soundfile as sf

from trackyard import librosa_lite

librosa = pytest.importorskip("librosa")


def test_stft_matches_librosa():
    rng = np.random.default_rng(0)
    y = rng.normal(size=44100 * 3).astype(np.float32) * 0.3
    win = np.hanning(2048).astype(np.float32)
    ours = np.abs(librosa_lite.stft(y, n_fft=2048, hop_length=441, window=win, center=True, pad_mode="constant", block=50))
    ref = np.abs(librosa.stft(y, n_fft=2048, hop_length=441, window=win, center=True, pad_mode="constant"))
    assert ours.shape == ref.shape
    assert np.allclose(ours, ref, rtol=1e-4, atol=1e-4)


def test_load_matches_librosa(tmp_path):
    rng = np.random.default_rng(1)
    stereo = (rng.normal(size=(44100, 2)) * 0.2).astype(np.float32)
    path = tmp_path / "mix.wav"
    sf.write(path, stereo, 44100, subtype="FLOAT")
    ours, sr = librosa_lite.load(path, sr=44100, mono=True)
    ref, _ = librosa.load(path, sr=44100, mono=True)
    assert sr == 44100 and ours.dtype == np.float32
    assert np.allclose(ours, ref, atol=1e-6)
