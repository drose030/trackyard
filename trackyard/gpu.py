"""NVIDIA GPU support for the Windows app. The app ships PyTorch for the CPU; this
downloads the matching CUDA build (one wheel with the CUDA libraries inside) into the
app's data folder and puts it in front of the bundled one."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .progress import fetch

INDEX = "https://download.pytorch.org/whl/torch/"
PLATFORM = "win_amd64"
# Left out like in the bundled PyTorch (desktop/scripts/bundle.py), plus the C++ link
# libraries.
SKIP = re.compile(r"^torch/(include|_inductor|onnx|_dynamo)/|^torch/lib/.*\.lib$")
# Oldest Windows driver per CUDA major version.
MIN_DRIVER = {12: 528, 13: 580}
FREE_SPACE = 7e9  # the wheel plus what it unpacks to

Report = Callable[[str, "float | None"], None]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def nvidia() -> dict | None:
    """The first NVIDIA GPU: {"name", "driver" (major version), "cc" (compute capability or None)}."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    for fields in ("name,driver_version,compute_cap", "name,driver_version"):
        try:
            out = subprocess.run([exe, f"--query-gpu={fields}", "--format=csv,noheader"], capture_output=True,
                                 text=True, stdin=subprocess.DEVNULL, timeout=15, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = out.stdout.strip().splitlines()
        if out.returncode == 0 and lines:
            parts = [p.strip() for p in lines[0].split(",")]
            try:
                return {"name": parts[0], "driver": int(parts[1].split(".")[0]),
                        "cc": float(parts[2]) if len(parts) > 2 else None}
            except (IndexError, ValueError):
                return None
    return None


def site(gpu_dir: Path) -> Path:
    return gpu_dir / "site"


def torch_version(paths: list[str] | None = None) -> str | None:
    """The PyTorch version on `paths` (default: sys.path), e.g. "2.15.0+cu134"."""
    for dist in importlib.metadata.distributions(name="torch", path=paths if paths is not None else sys.path):
        return dist.version
    return None


def base(version: str) -> str:
    return version.split("+")[0]


def bundled_version(gpu_dir: Path) -> str | None:
    gpu = str(site(gpu_dir))
    return torch_version([p for p in sys.path if p != gpu])


def installed(gpu_dir: Path) -> bool:
    version = torch_version([str(site(gpu_dir))])
    bundled = bundled_version(gpu_dir)
    return bool(version and bundled and base(version) == base(bundled))


def active(gpu_dir: Path) -> bool:
    torch = sys.modules.get("torch")
    if torch is not None:
        return Path(torch.__file__).is_relative_to(site(gpu_dir))
    return str(site(gpu_dir)) in sys.path


def activate(gpu_dir: Path) -> bool:
    """Put the downloaded PyTorch first, unless PyTorch is already loaded or the app
    has since been updated to another PyTorch version."""
    if "torch" in sys.modules or not installed(gpu_dir):
        return False
    if str(site(gpu_dir)) not in sys.path:
        sys.path.insert(0, str(site(gpu_dir)))
        importlib.invalidate_caches()
    return True


def wheels(version: str, index: str | None = None) -> dict[str, tuple[str, str]]:
    """CUDA builds of this PyTorch version for this Python: {"cu134": (url, sha256)}."""
    if index is None:
        with urllib.request.urlopen(urllib.request.Request(INDEX, headers={"User-Agent": "trackyard"})) as r:
            index = r.read().decode()
    py = f"cp{sys.version_info.major}{sys.version_info.minor}"
    pattern = (rf'href="([^"]*/torch-{re.escape(version)}%2B(cu\d+)-{py}-{py}-{PLATFORM}\.whl)'
               r'#sha256=([0-9a-f]{64})"')
    return {tag: (url, sha) for url, tag, sha in re.findall(pattern, index)}


def choose(found: dict[str, tuple[str, str]], gpu: dict, version: str) -> tuple[str, str, str]:
    """The newest CUDA build this GPU and driver can run: (tag, url, sha256)."""
    def cuda(tag: str) -> tuple[int, int]:
        n = int(tag[2:])
        return n // 10, n % 10

    usable = []
    for tag in found:
        major, minor = cuda(tag)
        if gpu["driver"] < MIN_DRIVER.get(major, 10**6):
            continue
        cc = gpu["cc"]
        if cc is not None and (major >= 13 and cc < 7.5 or cc >= 10 and (major, minor) < (12, 8)):
            continue
        usable.append(tag)
    if not usable:
        if not found:
            raise RuntimeError(f"PyTorch {version} has no NVIDIA build for this system")
        needed = min(MIN_DRIVER.get(cuda(t)[0], 10**6) for t in found)
        if gpu["driver"] < needed:
            raise RuntimeError(f"update the NVIDIA driver to version {needed} or newer, then try again")
        raise RuntimeError(f"PyTorch {version} no longer supports this GPU ({gpu['name']})")
    tag = max(usable, key=cuda)
    return (tag, *found[tag])


def check(folder: Path) -> None:
    """Load the downloaded PyTorch in a separate Python and make sure it sees the GPU."""
    env = {**os.environ, "PYTHONPATH": str(folder)}
    out = subprocess.run([sys.executable, "-c", "import torch; print(torch.cuda.is_available())"],
                         capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                         timeout=600, creationflags=NO_WINDOW)
    if out.stdout.strip() != "True":
        detail = (out.stderr.strip().splitlines() or ["it doesn't find the GPU"])[-1]
        raise RuntimeError(f"the GPU version of PyTorch doesn't work here: {detail}")


def install(gpu_dir: Path, report: Report, check: Callable[[Path], None] = check) -> None:
    """Download, unpack and test the CUDA build, then use it if PyTorch isn't loaded yet."""
    gpu = nvidia()
    if not gpu:
        raise RuntimeError("no NVIDIA GPU found")
    version = base(bundled_version(gpu_dir) or "")
    report("Looking up the GPU version of PyTorch", None)
    tag, url, sha = choose(wheels(version), gpu, version)
    gpu_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(gpu_dir).free < FREE_SPACE:
        raise RuntimeError(f"needs {FREE_SPACE / 1e9:.0f} GB of free disk space")
    wheel = gpu_dir / f"torch-{version}+{tag}.whl"
    fetch(url, wheel, sha, lambda done, total: report(
        f"Downloading PyTorch for NVIDIA GPUs ({total / 1e9:.1f} GB)", done / total if total else None))

    partial = gpu_dir / "site.partial"
    shutil.rmtree(partial, ignore_errors=True)
    with zipfile.ZipFile(wheel) as z:
        members = [m for m in z.infolist() if not SKIP.match(m.filename)]
        for i, member in enumerate(members):
            if i % 200 == 0:
                report("Unpacking", i / len(members))
            z.extract(member, partial)
    wheel.unlink()
    report("Testing the GPU", None)
    try:
        check(partial)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    shutil.rmtree(site(gpu_dir), ignore_errors=True)
    partial.rename(site(gpu_dir))
    activate(gpu_dir)
