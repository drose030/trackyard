"""Local web server for the chart editor: `trackyard edit [song folder]`.

Serves the static UI, the open song's audio stems and a small JSON API. Binds to
127.0.0.1 only. Uses the standard library only (conversion runs the normal Trackyard
pipeline in a background thread).

    GET  /api/state     whether a song is open (and its folder), version, settings, running
                        conversion, picker support
    GET  /api/song      song info, tempo map, notes of every level, confidences, stems, level
                        settings and presets, draft flag
    GET  /audio/<name>  one of the open song's stems (only names listed in /api/song), with
                        range requests
    POST /api/pick      {"kind": "audio"|"folder"|"save", "start"} → native macOS dialog;
                        {"path", "cancelled"}
    POST /api/open      {"folder"} → open a song folder
    POST /api/convert   {"audio"} → convert into a draft in the background, then open it
    GET  /api/job       progress of that conversion
    POST /api/gpu       download PyTorch for the NVIDIA GPU (the Windows app); progress in
                        /api/state "gpu"
    POST /api/settings  {"export_dir", "hide_hint"} → remember the Save-as folder and dismissed hints
    POST /api/reduce    {"expert": [[tick, lane], …], "settings"} → generated hard/medium/easy
    POST /api/save      {"levels": {"expert": [[tick, lane], …], …}, "settings", "tempos", "offset",
                        "dest", "overwrite"} → writes the chart, the level settings
                        (trackyard.json) and, when sent, the tempo map ([[tick, bpm], …]) and
                        offset (seconds, as song.ini delay); a draft moves into
                        dest/<song folder> (or dest/BeatTrain/Songs/…) on its first save
    POST /api/save_as   same body, "dest" required → copy the song folder into dest (or
                        dest/BeatTrain/Songs on an SD card), save the edits there and keep
                        editing the copy; the original stays as it was

Errors come back as {"error": …} with 400 or 404; an existing target without
"overwrite" gives 409 {"error": "exists", "target"}.

New songs stay drafts under <cache>/drafts until saved; `out_dir` in the settings is
the folder they were last saved to.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from ..chart import DIFFICULTIES, read_notes
from ..chartedit import save_drums, save_tempo
from ..difficulty import PRESETS, all_levels, clean_settings, preset_settings
from ..notes import ChartNote
from ..songfolder import AUDIO_EXTENSIONS, read_ini

STATIC = Path(__file__).parent / "static"
LANES = ("kick", "red", "yellow-cym", "yellow-tom", "blue-cym", "blue-tom", "green-cym", "green-tom")
SETTINGS = Path.home() / ".trackyard" / "editor.json"


class Song:
    """One song folder opened in the editor."""

    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        if not (self.folder / "song.ini").exists():
            raise FileNotFoundError(f"no song.ini in {self.folder}")
        if not ((self.folder / "notes.mid").exists() or (self.folder / "notes.chart").exists()):
            raise FileNotFoundError(f"no notes.mid or notes.chart in {self.folder}")
        self.lock = threading.Lock()
        self.load()

    def load(self) -> None:
        self.ini = read_ini(self.folder / "song.ini")
        self.levels: dict[str, list[tuple[int, str]]] = {}
        expert = read_notes(self.folder, self.ini, "expert")
        self.tempo_map, self.offset, self.five_lane = expert.tempo_map, expert.offset, expert.five_lane
        for level in DIFFICULTIES:
            try:
                self.levels[level] = read_notes(self.folder, self.ini, level).notes
            except Exception:
                self.levels[level] = []
        data = self._read_sidecar()
        self.confidence = {f"{n['tick']}:{n['lane']}": n["confidence"] for n in data.get("notes", [])}
        # How Hard/Medium/Easy are regenerated. Songs without settings (hand-made charts,
        # older conversions) got their lower levels from charter-like rules.
        self.level_settings = {**preset_settings("clone-hero"), **clean_settings(data.get("levels", {}))}

    def _read_sidecar(self) -> dict:
        sidecar = self.folder / "trackyard.json"
        return json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}

    def stems(self) -> list[str]:
        return sorted(f.name for f in self.folder.iterdir()
                      if f.suffix.lower() in AUDIO_EXTENSIONS and f.stem.lower() != "preview")

    def info(self) -> dict:
        tm = self.tempo_map
        last = max([t for notes in self.levels.values() for t, _ in notes] + [0])
        beat_ticks, downs = tm.beats(last + 8 * tm.ppq)
        return {
            "folder": str(self.folder),
            "name": self.ini.get("name", self.folder.name),
            "artist": self.ini.get("artist", ""),
            "ppq": tm.ppq,
            "offset": self.offset,
            "tempos": [{"tick": t, "bpm": bpm, "sec": tm.seconds(t)} for t, bpm in tm.tempos],
            "signatures": [{"tick": t, "num": n, "den": d} for t, n, d in tm.signatures],
            "beats": [{"tick": t, "down": d} for t, d in zip(beat_ticks, downs)],
            "levels": {lvl: [[t, lane] for t, lane in notes] for lvl, notes in self.levels.items()},
            "confidence": self.confidence,
            "stems": self.stems(),
            "level_settings": self.level_settings,
            "presets": {p: preset_settings(p) for p in PRESETS},
            "editable": not self.five_lane,
            "reason": "5-lane charts can't be saved yet" if self.five_lane else "",
        }

    def reduce(self, expert: list[list], settings: dict | None = None) -> dict:
        tm = self.tempo_map
        notes = [ChartNote(int(t), lane, self.confidence.get(f"{t}:{lane}", 1.0), tm.seconds(int(t)), "")
                 for t, lane in expert]
        beat_ticks, downs = tm.beats(max([n.tick for n in notes] + [0]) + tm.ppq)
        settings = {**self.level_settings, **clean_settings(settings or {})}
        levels = all_levels(notes, tm.ppq, {t for t, d in zip(beat_ticks, downs) if d}, settings)
        return {lvl: [[n.tick, n.lane] for n in levels[lvl]] for lvl in ("hard", "medium", "easy")}

    def save(self, levels: dict[str, list[list]], settings: dict | None = None,
             tempos: list[list] | None = None, offset: float | None = None, keep_original: bool = True) -> str:
        """`keep_original`: back the chart up as *.orig once; not for drafts and copies."""
        if self.five_lane:
            raise ValueError(self.info()["reason"])
        if tempos is not None:
            tempos = [(int(t), float(bpm)) for t, bpm in tempos]
            ticks = [t for t, _ in tempos]
            if not tempos or ticks[0] != 0 or ticks != sorted(set(ticks)) or not all(10 <= b <= 1000 for _, b in tempos):
                raise ValueError("bad tempo map")
        if offset is not None and not -60 < float(offset) < 60:
            raise ValueError("bad offset")
        clean: dict[str, list[tuple[int, str]]] = {}
        for lvl, notes in levels.items():
            if lvl not in DIFFICULTIES:
                raise ValueError(f"unknown difficulty {lvl!r}")
            pairs = {(int(t), str(lane)) for t, lane in notes}
            if any(lane not in LANES or t < 0 for t, lane in pairs):
                raise ValueError("bad note in request")
            clean[lvl] = sorted(pairs)
        level_settings = clean_settings(settings) if settings else None
        with self.lock:
            path = save_drums(self.folder, clean, keep_original)
            if tempos is not None or offset is not None:
                save_tempo(self.folder, tempos, None if offset is None else float(offset), keep_original)
            if level_settings:
                data = {"trackyard": 1, **self._read_sidecar()}
                data["levels"] = {**data.get("levels", {}), **level_settings}
                (self.folder / "trackyard.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
            self.load()
        return path.name


DEVICES = {"mps": "Apple GPU", "cuda": "NVIDIA GPU", "cpu": "CPU"}


class Task:
    """Something slow in the background: its step and, where known, how far it is."""

    def __init__(self):
        self.state = "running"
        self.error = ""
        self.step, self.fraction = "Starting", None
        self.started = time.time()

    def set_progress(self, step: str, fraction: float | None = None) -> None:
        self.step, self.fraction = step, fraction

    def info(self) -> dict:
        return {"state": self.state, "error": self.error, "step": self.step, "fraction": self.fraction,
                "elapsed": round(time.time() - self.started)}


class Job(Task):
    """One background conversion."""

    def __init__(self, audio: Path, out_dir: Path):
        super().__init__()
        self.audio, self.out_dir = audio, out_dir
        self.log: list[str] = []
        self.folder: Path | None = None

    def info(self) -> dict:
        return {**super().info(), "audio": str(self.audio), "out_dir": str(self.out_dir), "log": self.log[-12:],
                "folder": str(self.folder) if self.folder else None}


class App:
    """What the editor has open, plus settings and the running conversion."""

    def __init__(self, folder: Path | None, cache: Path, device: str | None = None,
                 settings_path: Path = SETTINGS, converter=None, gpu_dir: Path | None = None):
        self.song: Song | None = Song(folder) if folder else None
        self.cache = cache.resolve()
        self.drafts = self.cache / "drafts"
        self.device = device
        self.settings_path = settings_path
        self.settings = {"out_dir": str(Path("out").resolve())}
        if settings_path.exists():
            self.settings.update(json.loads(settings_path.read_text()))
        self.job: Job | None = None
        self.version = 0  # bumps on every newly opened song, so the UI reloads its audio
        self.converter = converter  # injectable for tests
        self.gpu_dir = gpu_dir
        self.gpu_task: Task | None = None
        self._nvidia: dict | None = None
        self.lock = threading.Lock()

    def save_settings(self, **values) -> None:
        self.settings.update(values)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=1))

    def state(self) -> dict:
        return {"song": bool(self.song), "version": self.version, "settings": self.settings,
                "job": self.job.info() if self.job else None, "native_picker": sys.platform == "darwin",
                "folder": str(self.song.folder) if self.song else None, "gpu": self.gpu()}

    def gpu(self) -> dict | None:
        """NVIDIA GPU support, when the app can download it and there is a GPU."""
        if not self.gpu_dir:
            return None
        from .. import gpu

        if self._nvidia is None:
            self._nvidia = gpu.nvidia() or {}
        if not self._nvidia:
            return None
        return {"name": self._nvidia["name"], "installed": gpu.installed(self.gpu_dir),
                "active": gpu.active(self.gpu_dir), "task": self.gpu_task.info() if self.gpu_task else None}

    def install_gpu(self) -> dict:
        if not self.gpu_dir:
            raise ValueError("GPU support is only in the desktop app")
        with self.lock:
            if self.gpu_task and self.gpu_task.state == "running":
                raise ValueError("already downloading")
            task = self.gpu_task = Task()

        def run() -> None:
            from .. import gpu

            try:
                gpu.install(self.gpu_dir, task.set_progress)
                task.state = "done"
            except Exception as e:  # shown in the start screen
                task.error = str(e)
                task.state = "failed"

        threading.Thread(target=run, daemon=True).start()
        return task.info()

    def open(self, folder: Path) -> dict:
        song = Song(folder)
        with self.lock:
            self.song = song
            self.version += 1
        return song.info()

    def is_draft(self) -> bool:
        return bool(self.song) and self.drafts in self.song.folder.parents

    def convert(self, audio: Path) -> Job:
        if not audio.is_file():
            raise ValueError(f"not a file: {audio}")
        with self.lock:
            if self.job and self.job.state == "running":
                raise ValueError("a conversion is already running")
            # Unsaved drafts other than the open song are abandoned once a new one starts.
            for old in self.drafts.glob("*"):
                if not (self.song and old in self.song.folder.parents):
                    shutil.rmtree(old, ignore_errors=True)
            job = self.job = Job(audio, self.drafts / time.strftime("%Y%m%d-%H%M%S"))
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        try:
            convert = self.converter
            if convert is None:
                job.set_progress("Loading PyTorch (the first start takes a while)")
                from .. import analysis
                from ..pipeline import convert as pipeline_convert
                from ..progress import Progress

                device = self.device or analysis.best_device()
                job.log.append(f"Analysing on the {DEVICES.get(device, device)}")

                def convert(src, out_root, log):
                    return pipeline_convert(src, out_root, self.cache, device, log=log,
                                            progress=Progress(job.set_progress))
            job.folder = convert(job.audio, job.out_dir, job.log.append)
            self.open(job.folder)
            job.log.append(f"Done: {job.folder}")
            job.state = "done"
        except Exception as e:  # report every failure to the UI
            job.error = f"{type(e).__name__}: {e}"
            job.state = "failed"

    def save(self, levels: dict[str, list[list]], dest: Path | None, overwrite: bool,
             settings: dict | None = None, tempos: list[list] | None = None, offset: float | None = None) -> dict:
        song = self.song
        if not self.is_draft():
            return {"file": song.save(levels, settings, tempos, offset), "folder": str(song.folder)}
        if dest is None:
            raise ValueError("choose a folder for the new song")
        target = self._target(dest)
        if target.exists() and not overwrite:
            raise FileExistsError(str(target))
        file = song.save(levels, settings, tempos, offset, keep_original=False)
        if target.exists():
            shutil.rmtree(target)
        draft = song.folder
        shutil.move(str(draft), str(target))
        shutil.rmtree(draft.parent, ignore_errors=True)  # the conversion's drafts/<time> folder
        with song.lock:
            song.folder = target
            song.load()
        self.save_settings(out_dir=str(dest.resolve()))
        return {"file": file, "folder": str(target)}

    def save_as(self, levels: dict[str, list[list]], dest: Path, overwrite: bool,
                settings: dict | None = None, tempos: list[list] | None = None, offset: float | None = None) -> dict:
        """Copy the song into `dest`, save the edits into the copy and keep editing it."""
        if self.is_draft():
            return self.save(levels, dest, overwrite, settings, tempos, offset)
        target = self._target(dest)
        if target == self.song.folder:
            raise ValueError("that is the song's own folder")
        if target.exists():
            if not overwrite:
                raise FileExistsError(str(target))
            shutil.rmtree(target)
        shutil.copytree(self.song.folder, target, ignore=shutil.ignore_patterns("*.orig"))
        copy = Song(target)
        file = copy.save(levels, settings, tempos, offset, keep_original=False)
        with self.lock:
            self.song = copy  # same stem names: the UI keeps its audio
        self.save_settings(export_dir=str(dest.resolve()))
        return {"file": file, "folder": str(target)}

    def _target(self, dest: Path) -> Path:
        """Where the song folder goes in `dest`: on an SD card with BeatTrain/Songs, in there."""
        if not dest.is_dir():
            raise ValueError(f"not a folder: {dest}")
        songs_dir = dest / "BeatTrain" / "Songs"
        return ((songs_dir if songs_dir.is_dir() else dest) / self.song.folder.name).resolve()


def pick(kind: str, start: str | None = None) -> str | None:
    """Native macOS dialog via osascript; None when cancelled."""
    if sys.platform != "darwin":
        raise ValueError("file dialogs need macOS; type the path instead")
    quoted = (start or "").replace("\\", "\\\\").replace('"', '\\"')  # AppleScript string literal
    where = f' default location (POSIX file "{quoted}")' if start and Path(start).is_dir() else ""
    if kind == "audio":
        script = f'POSIX path of (choose file with prompt "Choose a song to convert" of type {{"public.audio"}}{where})'
    elif kind == "folder":
        script = f'POSIX path of (choose folder with prompt "Choose a folder"{where})'
    elif kind == "save":
        script = f'POSIX path of (choose folder with prompt "Choose where to save the song"{where})'
    else:
        raise ValueError(f"unknown picker {kind!r}")
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None  # cancelled (-128) or no permission
    return r.stdout.strip().rstrip("/") or None


def make_handler(app: App, picker=pick):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_ranged(self, data: bytes, ctype: str) -> None:
            """Serve a byte range when asked: <audio> can only seek with range requests."""
            rng = self.headers.get("Range", "")
            start, end = 0, len(data) - 1
            if rng.startswith("bytes="):
                a, _, b = rng[6:].split(",")[0].partition("-")
                if a:
                    start, end = int(a), int(b) if b else end
                elif b:  # suffix range: the last b bytes
                    start = max(0, len(data) - int(b))
                if start > end or start >= len(data):
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.end_headers()
                    return
                end = min(end, len(data) - 1)
            part = data[start : end + 1]
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(part)))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(part)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                return self._json(app.state())
            if path == "/api/job":
                return self._json(app.job.info() if app.job else None)
            if path == "/api/song":
                if not app.song:
                    return self._json({"error": "no song open"}, 404)
                return self._json({**app.song.info(), "draft": app.is_draft()})
            if path.startswith("/audio/"):
                name = unquote(path[len("/audio/"):])
                if not app.song or name not in app.song.stems():  # whitelist: no path traversal
                    return self._json({"error": "not found"}, 404)
                data = (app.song.folder / name).read_bytes()
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                return self._send_ranged(data, ctype)
            name = "index.html" if path == "/" else path.lstrip("/")
            file = (STATIC / name).resolve()
            if file.parent != STATIC.resolve() or not file.is_file():
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            return self._send(200, file.read_bytes(), ctype)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/pick":
                    path = picker(body.get("kind", ""), body.get("start"))
                    return self._json({"path": path, "cancelled": path is None})
                if self.path == "/api/open":
                    return self._json(app.open(Path(body["folder"]).expanduser()))
                if self.path == "/api/convert":
                    return self._json(app.convert(Path(body["audio"]).expanduser()).info())
                if self.path == "/api/gpu":
                    return self._json(app.install_gpu())
                if self.path == "/api/settings":
                    values = {k: str(Path(v).expanduser()) for k, v in body.items()
                              if k == "export_dir" and v}
                    if body.get("hide_hint"):
                        values["hidden_hints"] = sorted({*app.settings.get("hidden_hints", []), str(body["hide_hint"])})
                    app.save_settings(**values)
                    return self._json(app.settings)
                if not app.song:
                    return self._json({"error": "no song open"}, HTTPStatus.BAD_REQUEST)
                if self.path == "/api/reduce":
                    return self._json(app.song.reduce(body["expert"], body.get("settings")))
                if self.path in ("/api/save", "/api/save_as"):
                    dest = Path(body["dest"]).expanduser() if body.get("dest") else None
                    if self.path == "/api/save_as" and dest is None:
                        raise ValueError("choose a folder")
                    save = app.save_as if self.path == "/api/save_as" else app.save
                    return self._json({"ok": True, **save(body["levels"], dest, bool(body.get("overwrite")),
                                                          body.get("settings"), body.get("tempos"),
                                                          body.get("offset"))})
                return self._json({"error": "not found"}, 404)
            except FileExistsError as e:
                return self._json({"error": "exists", "target": str(e)}, HTTPStatus.CONFLICT)
            except (ValueError, KeyError, TypeError, FileNotFoundError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    return Handler


def exit_with_parent() -> None:
    """Started by the desktop app: its stdin pipe closes when the app quits or crashes, and
    this process must not outlive it."""
    def watch() -> None:
        sys.stdin.read()
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def serve(folder: Path | None, port: int = 8765, open_browser: bool = True, cache: Path = Path("cache"),
          exit_with_stdin: bool = False, gpu_dir: Path | None = None) -> None:
    app = App(folder, cache, gpu_dir=gpu_dir)
    if exit_with_stdin:
        exit_with_parent()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    what = app.song.folder.name if app.song else "Trackyard"
    print(f"Editing {what} at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
