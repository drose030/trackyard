"""Progress of a conversion for the editor: the current step and, where known, how far
it is. Model downloads and Demucs report their own progress through the hooks below."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

Report = Callable[[str, "float | None"], None]


class Progress:
    """Remembers the current step, so a download in the middle can hand it back."""

    def __init__(self, report: Report | None = None):
        self.report = report or (lambda step, fraction: None)
        self.step = ""

    def __call__(self, step: str, fraction: float | None = None) -> None:
        self.step = step
        self.report(step, fraction)

    def fraction(self, value: float | None) -> None:
        self.report(self.step, value)


def fetch(url: str, dst: Path, sha256_prefix: str | None, on_bytes: Callable[[int, int], None]) -> None:
    """Download `url` to `dst` through a .partial file, reporting (done, total) bytes."""
    request = urllib.request.Request(url, headers={"User-Agent": "trackyard"})
    partial = dst.with_name(dst.name + ".partial")
    sha = hashlib.sha256()
    with urllib.request.urlopen(request) as response, open(partial, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            on_bytes(done, total)
    if sha256_prefix and not sha.hexdigest().startswith(sha256_prefix):
        partial.unlink()
        raise RuntimeError(f"download of {dst.name} is corrupt; try again")
    shutil.move(str(partial), dst)


@contextmanager
def model_downloads(progress: Progress) -> Iterator[None]:
    """torch.hub downloads (Demucs, Beat This!) report their bytes instead of a console bar."""
    import torch.hub

    original = torch.hub.download_url_to_file

    def download(url: str, dst: str, hash_prefix: str | None = None, progress_bar: bool = True, **_) -> None:
        model = "Beat This!" if "Beat This" in progress.step else "Demucs" if "Demucs" in progress.step else "a"
        fetch(url, Path(dst), hash_prefix, lambda done, total: progress.report(
            f"Downloading the {model} model, first time only ({total / 1e6:.0f} MB)", done / total if total else None))
        progress.fraction(None)

    torch.hub.download_url_to_file = download
    try:
        yield
    finally:
        torch.hub.download_url_to_file = original


@contextmanager
def demucs_segments(progress: Progress) -> Iterator[None]:
    """Demucs walks the song in segments behind a tqdm bar; count them instead."""
    import demucs.apply

    original = demucs.apply.tqdm

    def counted(items, **_):
        items = list(items)
        for i, item in enumerate(items):
            progress.fraction(i / max(1, len(items)))
            yield item
        progress.fraction(1.0)

    demucs.apply.tqdm = SimpleNamespace(tqdm=counted)
    try:
        yield
    finally:
        demucs.apply.tqdm = original
