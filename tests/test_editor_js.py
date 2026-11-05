"""The editor's JavaScript modules: their node tests, and the tempo map against Python's."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from trackyard.tempomap import TempoMap

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "trackyard" / "editor" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_js_modules():
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    r = subprocess.run([NODE, "--test", *tests], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


@needs_node
def test_js_tempo_map_matches_python():
    tempos, offset, ticks = [(0, 97.5), (1700, 133.25), (5000, 61.0)], 0.123, [0, 1, 999, 1700, 4321, 5000, 9876]
    py = [TempoMap(480, list(tempos), []).seconds(t) + offset for t in ticks]
    script = (f"import {{ TempoMap }} from {json.dumps((STATIC / 'tempomap.js').as_uri())};"
              f"const m = new TempoMap(480, {json.dumps(tempos)}, {offset});"
              f"console.log(JSON.stringify({json.dumps(ticks)}.map((t) => m.seconds(t))));")
    r = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == pytest.approx(py, abs=1e-9)
