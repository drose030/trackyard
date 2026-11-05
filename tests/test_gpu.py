import sys
import zipfile

import pytest

from trackyard import gpu
from trackyard.editor.server import App

PY = f"cp{sys.version_info.major}{sys.version_info.minor}"
SHA = "ab" * 32


def index(*tags):
    return "\n".join(f'<a href="https://download-r2.pytorch.org/whl/{t}/torch-2.15.0%2B{t}-{PY}-{PY}-win_amd64.whl'
                     f'#sha256={SHA}">x</a>' for t in tags)


def test_wheels_finds_the_cuda_builds_of_this_version_for_this_python():
    found = gpu.wheels("2.15.0", index("cu132", "cu134") + index("cpu").replace("2.15.0", "2.14.0"))
    assert sorted(found) == ["cu132", "cu134"]
    assert found["cu134"][0].endswith(f"torch-2.15.0%2Bcu134-{PY}-{PY}-win_amd64.whl")
    assert found["cu134"][1] == SHA


def test_choose_takes_the_newest_build_the_gpu_and_driver_can_run():
    found = gpu.wheels("2.15.0", index("cu126", "cu128", "cu132"))
    assert gpu.choose(found, {"name": "RTX 3070", "driver": 581, "cc": 8.6}, "2.15.0")[0] == "cu132"
    assert gpu.choose(found, {"name": "RTX 3070", "driver": 560, "cc": 8.6}, "2.15.0")[0] == "cu128"
    assert gpu.choose(found, {"name": "GTX 1080", "driver": 581, "cc": 6.1}, "2.15.0")[0] == "cu128"


def test_choose_explains_why_nothing_fits():
    found = gpu.wheels("2.15.0", index("cu132", "cu134"))
    with pytest.raises(RuntimeError, match="driver to version 580"):
        gpu.choose(found, {"name": "RTX 3070", "driver": 560, "cc": 8.6}, "2.15.0")
    with pytest.raises(RuntimeError, match="no longer supports this GPU"):
        gpu.choose(found, {"name": "GTX 1080", "driver": 581, "cc": 6.1}, "2.15.0")


def test_install_unpacks_the_wheel_without_the_parts_the_app_leaves_out(tmp_path, monkeypatch):
    wheel = tmp_path / "torch.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("torch/__init__.py", "")
        z.writestr("torch/include/huge.h", "")
        z.writestr("torch/lib/torch_cuda.dll", "")
        z.writestr("torch/lib/torch_cuda.lib", "")
        z.writestr("torch-2.15.0+cu134.dist-info/METADATA", "Metadata-Version: 2.1\nName: torch\nVersion: 2.15.0+cu134\n")
    monkeypatch.setattr(gpu, "nvidia", lambda: {"name": "RTX 3070", "driver": 581, "cc": 8.6})
    monkeypatch.setattr(gpu, "wheels", lambda version: {"cu134": (wheel.as_uri(), "")})
    monkeypatch.setattr(gpu, "FREE_SPACE", 0)
    monkeypatch.setattr(sys, "path", list(sys.path))
    checked = []
    steps = []

    gpu.install(tmp_path / "gpu", lambda step, fraction: steps.append(step), check=checked.append)

    site = tmp_path / "gpu" / "site"
    assert checked == [tmp_path / "gpu" / "site.partial"]
    assert (site / "torch" / "lib" / "torch_cuda.dll").exists()
    assert not (site / "torch" / "include").exists()
    assert not (site / "torch" / "lib" / "torch_cuda.lib").exists()
    assert not list((tmp_path / "gpu").glob("*.whl"))
    assert gpu.installed(tmp_path / "gpu")
    assert any(s.startswith("Downloading PyTorch") for s in steps)


def test_a_failed_check_leaves_nothing_behind(tmp_path, monkeypatch):
    wheel = tmp_path / "torch.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("torch/__init__.py", "")
    monkeypatch.setattr(gpu, "nvidia", lambda: {"name": "RTX 3070", "driver": 581, "cc": 8.6})
    monkeypatch.setattr(gpu, "wheels", lambda version: {"cu134": (wheel.as_uri(), "")})
    monkeypatch.setattr(gpu, "FREE_SPACE", 0)

    def broken(folder):
        raise RuntimeError("no GPU")

    with pytest.raises(RuntimeError, match="no GPU"):
        gpu.install(tmp_path / "gpu", lambda step, fraction: None, check=broken)
    assert sorted(p.name for p in (tmp_path / "gpu").iterdir()) == []


def test_the_editor_offers_gpu_support_only_with_an_nvidia_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "nvidia", lambda: None)
    assert App(None, tmp_path, settings_path=tmp_path / "s.json", gpu_dir=tmp_path / "gpu").state()["gpu"] is None
    monkeypatch.setattr(gpu, "nvidia", lambda: {"name": "RTX 3070", "driver": 581, "cc": 8.6})
    assert App(None, tmp_path, settings_path=tmp_path / "s.json").state()["gpu"] is None
    info = App(None, tmp_path, settings_path=tmp_path / "s.json", gpu_dir=tmp_path / "gpu").state()["gpu"]
    assert info == {"name": "RTX 3070", "installed": False, "active": False, "task": None}
