import { LEVELS, ROW_ORDER, SongDoc, rowOf } from "./songdoc.js";
import { TempoMap } from "./tempomap.js";
import { Transport } from "./transport.js";

// Rows top to bottom: Beat-Train's six instruments. In the chart file (Clone Hero pro
// drums) "orange" is the green lane as a cymbal and "green" the green lane as a tom.
const ROWS = ROW_ORDER;
const ROW_NAMES = { red: "Snare", yellow: "Hi-hat / Tom", blue: "Tom / Ride", orange: "Cymbal", green: "Floor tom", kick: "Bass drum" };
const PADS = ["yellow", "blue", "green"];  // chart lanes that can be cymbal or tom
const COLORS = { kick: "#9b6bff", red: "#ef4f4f", yellow: "#f5c84b", blue: "#4f8ff7", orange: "#ff9a3c", green: "#3fbf6f" };
const DEFAULT_LANE = { kick: "kick", red: "red", yellow: "yellow-cym", blue: "blue-tom", orange: "green-cym", green: "green-tom" };
const GUTTER = 104;
const LOW_CONFIDENCE = 0.5;

const RULER_H = 24, WAVE_H = 56, ROW_H = 44, NOTE_W = 14;

const state = {
  app: null,           // /api/state
  song: null,          // /api/song
  doc: null,           // SongDoc: notes, level settings, tempo map, undo
  level: "expert",
  selection: new Set(), // keys "tick:lane" in the current level
  clipboard: null,     // [{dt, lane}]
  view: { start: 0, pxPerSec: 160 },
  snap: 4,
  speed: 1,
  ticksOn: true, metronomeOn: false,
  drag: null,
  box: null,           // rubber-band selection
  tempoMode: false,
  tempoDrag: null,     // {tick, base, key, moved}
  tempoOpened: null,   // TempoMap as opened, for "Revert tempo"
  onsets: [],          // drum hits in audio seconds, sorted
};

// The stems as drawn: waveform peaks, role (drums, mix, crowd) and the longest duration.
// Playback itself is transport.js.
const audio = { peaks: {}, roles: {}, duration: 0 };

const canvas = document.getElementById("view");
const g = canvas.getContext("2d");
const $ = (id) => document.getElementById(id);

// MARK: - Time

const tickToSec = (tick) => state.doc.tempo.seconds(tick);  // audio time
const secToTick = (sec) => state.doc.tempo.tick(sec);

// Beats up to the later of the last note and the end of the audio, plus two bars; they
// only change with the time signatures and that end.
let beatCache = { end: -1, list: [] };
function beats() {
  const tm = state.doc.tempo;
  const last = Math.max(0, ...LEVELS.map((l) => state.doc.levels[l].at(-1)?.tick ?? 0), tm.tick(audio.duration));
  const end = (Math.ceil(last / tm.ppq) + 8) * tm.ppq;
  if (beatCache.end !== end) beatCache = { end, list: tm.beats(end) };
  return beatCache.list;
}

function snapTick(tick) {
  if (!state.snap) return Math.max(0, Math.round(tick));
  const step = state.song.ppq / state.snap;
  return Math.max(0, Math.round(Math.round(tick / step) * step));
}

function barOf(tick) {
  let bar = 0;
  for (const b of beats()) { if (b.tick > tick) break; if (b.down) bar++; }
  return bar;
}

const secToX = (sec) => GUTTER + (sec - state.view.start) * state.view.pxPerSec;
const xToSec = (x) => state.view.start + (x - GUTTER) / state.view.pxPerSec;
const lanesTop = () => RULER_H + waveH() * waveStems().length;

// MARK: - Notes and history

const key = (n) => `${n.tick}:${n.lane}`;
const color = (lane) => lane.split("-")[0];
const notes = () => state.doc.levels[state.level];

function commit(change, options = {}) {
  state.doc.edit(change, { level: state.level, ...options });
}

function onDocChange(what) {
  if (what.undo) state.selection.clear();
  renderHistory();
  renderSoundButtons();
  renderTune();
  renderTempo();
  draw();
}

function renderHistory() {
  $("save").classList.toggle("dirty", state.doc.dirty);
  $("undo").disabled = !state.doc.canUndo;
  $("redo").disabled = !state.doc.canRedo;
  renderLevels();
}

function confidence(n) {
  const c = state.song.confidence[key(n)];
  return c === undefined ? 1 : c;
}

function noteAt(x, y) {
  const row = rowAt(y);
  if (row < 0) return null;
  let best = null, bestDx = NOTE_W;
  for (const n of notes()) {
    if (rowOf(n.lane) !== ROWS[row]) continue;
    const dx = Math.abs(secToX(tickToSec(n.tick)) - x);
    if (dx < bestDx) { best = n; bestDx = dx; }
  }
  return best;
}

function rowAt(y) {
  const r = Math.floor((y - lanesTop()) / ROW_H);
  return r >= 0 && r < ROWS.length ? r : -1;
}

function laneInRow(lane, row) {
  const r = ROWS[row];
  if (r === "yellow" || r === "blue") {  // keep cymbal or tom when moving between pads
    if (lane.endsWith("-cym")) return `${r}-cym`;
    if (lane.endsWith("-tom")) return `${r}-tom`;
  }
  return DEFAULT_LANE[r];
}

// MARK: - Drawing

function waveStems() {
  const rank = (n) => ({ drums: 0, mix: 1, crowd: 2 })[audio.roles[n]] ?? 1;
  return Object.keys(audio.peaks).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

const waveH = () => (waveStems().length > 3 ? 42 : WAVE_H);  // name, mute and volume must fit

function stemAt(x, y) {
  if (!state.song || x >= GUTTER || y < RULER_H) return null;
  return waveStems()[Math.floor((y - RULER_H) / waveH())] || null;
}

// MARK: - Mixer

const mix = { levels: {} };
try { mix.levels = JSON.parse(localStorage.getItem("trackyard.mix")) || {}; } catch { /* private mode */ }

// The note ticks and the metronome for a stretch of audio time, as the switches say.
function sounds(from, until) {
  const out = [];
  if (state.ticksOn) {
    for (const n of notes()) {
      const sec = tickToSec(n.tick);
      if (sec >= from && sec < until) out.push({ sec, sound: n.lane });
    }
  }
  if (state.metronomeOn) {
    for (const b of beats()) {
      const sec = tickToSec(b.tick);
      if (sec >= from && sec < until) out.push({ sec, sound: b.down ? "downbeat" : "beat" });
    }
  }
  return out;
}

const transport = new Transport({
  sounds,
  levels: mix.levels,
  onState: (playing) => {
    $("play").textContent = playing ? "❚❚ Pause" : "▶︎ Play";
    if (playing) requestAnimationFrame(frame);
    else draw();
  },
});

function setLevelVolume(key, value) {
  try { localStorage.setItem("trackyard.mix", JSON.stringify({ ...mix.levels, [key]: value })); } catch { /* not remembered */ }
  transport.setVolume(key, value);
}

function renderMixer() {
  // Stems up to 100 % (an <audio> element can't go louder); the synthesized clicks, with
  // their on/off buttons, to 200 %.
  const stems = $("stem-volumes");
  stems.innerHTML = "";
  waveStems().forEach((s, i) => {
    const input = document.createElement("input");
    Object.assign(input, { type: "range", min: 0, max: 100, step: 5 });
    input.style.top = `${RULER_H + i * waveH() + waveH() - (waveH() > 50 ? 22 : 17)}px`;
    input.setAttribute("aria-label", `${s} volume`);
    const show = () => { input.title = `${s}: ${input.value} %`; };
    input.value = Math.round(Math.min(mix.levels[`stem:${s}`] ?? 1, 1) * 100);
    show();
    input.oninput = () => { show(); setLevelVolume(`stem:${s}`, Number(input.value) / 100); };
    stems.appendChild(input);
  });
  bindVolume($("vol-ticks"), "ticks");
  bindVolume($("vol-metronome"), "metronome");
  renderSoundButtons();
  $("transport").hidden = $("toolbar").hidden = !state.song;
}

function bindVolume(input, key) {
  const out = input.nextElementSibling;
  input.value = Math.round(Math.min(mix.levels[key] ?? 1, Number(input.max) / 100) * 100);
  out.textContent = `${input.value} %`;
  input.oninput = () => {
    out.textContent = `${input.value} %`;
    setLevelVolume(key, Number(input.value) / 100);
  };
}

function toggleMute(stem) {
  transport.toggleMute(stem);
  draw();
}

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  g.fillStyle = "#15171c";
  g.fillRect(0, 0, w, h);
  if (!state.song) return;

  const t0 = state.view.start, t1 = xToSec(w);
  drawGrid(w, h, t0, t1);
  drawWaves(w);
  drawLanes(w, t0, t1);
  drawRuler(w, t0, t1);
  if (state.tempoMode) drawTempo(w, t0, t1);

  if (state.box) {
    const { x0, y0, x1, y1 } = state.box;
    g.fillStyle = "rgba(91,156,255,0.12)";
    g.strokeStyle = "rgba(91,156,255,0.8)";
    g.lineWidth = 1;
    g.fillRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    g.strokeRect(Math.min(x0, x1) + 0.5, Math.min(y0, y1) + 0.5, Math.abs(x1 - x0), Math.abs(y1 - y0));
  }

  drawGutter(h);
  const x = secToX(position());
  if (x >= GUTTER) {
    g.strokeStyle = "#ff5555";
    g.lineWidth = 2;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
  }
  if (state.tempoMode) updateTempoReadout();
  updatePosition();
}

function drawGutter(h) {
  g.fillStyle = "#1e2128";
  g.fillRect(0, 0, GUTTER, h);
  g.font = "600 12px -apple-system, sans-serif";
  g.textBaseline = "middle";
  waveStems().forEach((name, i) => {
    const mid = RULER_H + i * waveH() + (waveH() > 50 ? 17 : 12);  // the volume slider sits below
    let label = name;
    while (label.length > 1 && g.measureText(label).width > GUTTER - 44) label = label.slice(0, -1);
    g.fillStyle = transport.muted[name] ? "#5c6270" : "#9097a6";
    g.fillText(label === name ? name : `${label}…`, 10, mid);
    drawSpeaker(GUTTER - 20, mid, transport.muted[name]);
  });
  const top = lanesTop();
  ROWS.forEach((row, i) => {
    g.fillStyle = COLORS[row];
    g.beginPath();
    g.roundRect(4, top + i * ROW_H + 3, GUTTER - 10, ROW_H - 6, 5);
    g.fill();
    g.fillStyle = ["yellow", "orange", "green"].includes(row) ? "rgba(0,0,0,0.78)" : "#ffffff";
    g.fillText(ROW_NAMES[row], 12, top + i * ROW_H + ROW_H / 2);
  });
  g.fillStyle = "#343946";
  g.fillRect(GUTTER - 1, 0, 1, h);
}

function drawSpeaker(x, y, muted) {
  g.save();
  g.fillStyle = g.strokeStyle = muted ? "#ff6b6b" : "#9097a6";
  g.beginPath();
  g.moveTo(x - 8, y - 3); g.lineTo(x - 5, y - 3); g.lineTo(x - 1, y - 7);
  g.lineTo(x - 1, y + 7); g.lineTo(x - 5, y + 3); g.lineTo(x - 8, y + 3);
  g.closePath();
  g.fill();
  g.lineWidth = 1.5;
  g.lineCap = "round";
  g.beginPath();
  if (muted) {
    g.moveTo(x + 2, y - 3); g.lineTo(x + 8, y + 3);
    g.moveTo(x + 8, y - 3); g.lineTo(x + 2, y + 3);
  } else {
    g.arc(x, y, 4, -0.8, 0.8);
    g.moveTo(x + 8 * Math.cos(-0.8), y + 8 * Math.sin(-0.8));
    g.arc(x, y, 8, -0.8, 0.8);
  }
  g.stroke();
  g.restore();
}

function drawGrid(w, h, t0, t1) {
  for (const b of beats()) {
    const s = tickToSec(b.tick);
    if (s < t0 - 1 || s > t1 + 1) continue;
    const x = Math.round(secToX(s)) + 0.5;
    g.strokeStyle = state.tempoMode ? (b.down ? "#8a91a3" : "#5a6070") : b.down ? "#3d4352" : "#262a33";
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(x, RULER_H); g.lineTo(x, h); g.stroke();
  }
}

function drawRuler(w, t0, t1) {
  g.fillStyle = "#1e2128";
  g.fillRect(0, 0, w, RULER_H);
  g.font = "11px -apple-system, sans-serif";
  g.textBaseline = "middle";
  let bar = 0;
  for (const b of beats()) {
    if (b.down) bar++;
    const s = tickToSec(b.tick);
    if (s < t0 - 2 || s > t1) continue;
    const x = secToX(s);
    g.fillStyle = b.down ? "#c8ccd6" : "#555b69";
    g.fillRect(Math.round(x), b.down ? 8 : 16, 1, b.down ? 16 : 8);
    if (b.down) g.fillText(String(bar), x + 4, 11);
  }
  g.fillStyle = "#9097a6";
  const step = state.view.pxPerSec > 60 ? 5 : 15;
  for (let s = Math.ceil(t0 / step) * step; s < t1; s += step) {
    const m = Math.floor(s / 60), sec = String(Math.floor(s % 60)).padStart(2, "0");
    g.fillText(`${m}:${sec}`, secToX(s) + 3, RULER_H - 6);
  }
}

function drawWaves(w) {
  waveStems().forEach((name, i) => {
    const h = waveH(), top = RULER_H + i * h, mid = top + h / 2;
    const p = audio.peaks[name];
    g.fillStyle = "#1a1d23";
    g.fillRect(0, top, w, h - 1);
    g.fillStyle = transport.muted[name] ? "#2c313b" : audio.roles[name] === "drums" ? "#6f7a92" : "#4d5566";
    const perPx = p.rate / state.view.pxPerSec;
    for (let x = GUTTER; x < w; x++) {
      const a = Math.floor(xToSec(x) * p.rate);
      const b = Math.max(a + 1, Math.floor(a + perPx));
      let peak = 0;
      for (let k = Math.max(0, a); k < Math.min(b, p.data.length); k++) peak = Math.max(peak, p.data[k]);
      const half = peak * (h / 2 - 3);
      g.fillRect(x, mid - half, 1, Math.max(1, half * 2));
    }
  });
}

function drawLanes(w, t0, t1) {
  const top = lanesTop();
  ROWS.forEach((row, i) => {
    g.fillStyle = i % 2 ? "rgba(255,255,255,0.015)" : "rgba(255,255,255,0.035)";
    g.fillRect(0, top + i * ROW_H, w, ROW_H);
  });
  drawSoundCut(top);
  const moving = state.drag && state.drag.moved;
  for (const n of notes()) {
    const selected = state.selection.has(key(n));
    let tick = n.tick, row = ROWS.indexOf(rowOf(n.lane)), lane = n.lane;
    if (selected && moving) {
      tick = Math.max(0, tick + state.drag.delta);
      row = Math.min(ROWS.length - 1, Math.max(0, row + state.drag.rows));
      lane = laneInRow(n.lane, row);
    }
    const s = tickToSec(tick);
    if (s < t0 - 1 || s > t1 + 1) continue;
    drawNote({ ...n, lane }, secToX(s), top + row * ROW_H + ROW_H / 2, selected);
  }
}

function drawNote(n, x, y, selected) {
  const low = confidence(n) < LOW_CONFIDENCE;
  g.globalAlpha = low ? 0.45 : 1;
  g.fillStyle = COLORS[rowOf(n.lane)];
  g.beginPath();
  if (n.lane === "kick") {
    g.roundRect(x - 4, y - ROW_H / 2 + 5, 8, ROW_H - 10, 3);
  } else if (n.lane.endsWith("-cym")) {
    g.arc(x, y, NOTE_W / 2, 0, Math.PI * 2);
  } else {
    g.roundRect(x - NOTE_W / 2, y - NOTE_W / 2, NOTE_W, NOTE_W, 3);
  }
  g.fill();
  if (n.lane.endsWith("-cym")) {
    g.fillStyle = "rgba(255,255,255,0.55)";
    g.beginPath(); g.arc(x, y, 2.5, 0, Math.PI * 2); g.fill();
  }
  g.globalAlpha = 1;
  if (selected || low) {
    g.setLineDash(low && !selected ? [3, 2] : []);
    g.strokeStyle = selected ? "#ffffff" : "rgba(255,255,255,0.7)";
    g.lineWidth = selected ? 2 : 1;
    g.beginPath();
    g.rect(x - NOTE_W / 2 - 3, y - ROW_H / 2 + 3, NOTE_W + 6, ROW_H - 6);
    g.stroke();
    g.setLineDash([]);
  }
}

// MARK: - Audio

const position = () => transport.position();

async function loadAudio() {
  audio.peaks = {}; audio.roles = {}; audio.duration = 0;
  const roles = state.song.stem_roles;
  const stems = state.song.stems.map((name) => ({
    name, stem: name.replace(/\.[^.]+$/, ""), url: `/audio/${encodeURIComponent(name)}?v=${state.app.version}`,
    muted: roles[name] === "crowd",
  }));
  const hasDrums = stems.some((s) => roles[s.name] === "drums");
  status(`Loading audio (${stems.length} stems)…`);
  const buffers = await transport.load(stems);
  for (const { name, stem } of stems) {
    const buf = buffers[stem];
    if (!buf) continue;
    audio.roles[stem] = roles[name];
    audio.peaks[stem] = peaks(buf);
    audio.duration = Math.max(audio.duration, buf.duration);
    if (roles[name] === "drums" || (!hasDrums && /^song$/i.test(stem))) {
      state.onsets = mergeOnsets(state.onsets, detectOnsets(buf));
    }
  }
  renderMixer();
  resize();
  const n = notes().filter((x) => confidence(x) < LOW_CONFIDENCE).length;
  status(`${Object.keys(buffers).length} stems loaded · ${n} uncertain notes in ${state.level} (N jumps to them)`);
}

function peaks(buf, rate = 200) {
  const n = Math.ceil(buf.duration * rate), out = new Float32Array(n);
  const step = buf.sampleRate / rate;
  for (let ch = 0; ch < buf.numberOfChannels; ch++) {
    const d = buf.getChannelData(ch);
    for (let i = 0; i < n; i++) {
      let m = 0;
      const end = Math.min(d.length, (i + 1) * step);
      for (let k = Math.floor(i * step); k < end; k += 4) m = Math.max(m, Math.abs(d[k]));
      out[i] = Math.max(out[i], m);
    }
  }
  return { rate, data: out };
}

function play() {
  if (!state.song) return;
  if (transport.playing) transport.pause();
  else transport.play();
}

function seek(sec) {
  transport.seek(sec);
  if (!transport.playing) draw();
}

function frame() {
  if (!transport.playing) return;
  const pos = position(), w = canvas.clientWidth;
  if (secToX(pos) > w * 0.75 || secToX(pos) < GUTTER) state.view.start = pos - (w - GUTTER) * 0.2 / state.view.pxPerSec;
  draw();
  requestAnimationFrame(frame);
}

// MARK: - Editing

function pointer(e) {
  const r = canvas.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

canvas.addEventListener("mousedown", (e) => {
  if (!state.song) return;
  const { x, y } = pointer(e);
  if (x < GUTTER) {
    const stem = stemAt(x, y);
    if (stem) toggleMute(stem);
    return;
  }
  if (y < RULER_H) return seek(xToSec(x));
  if (state.tempoMode) return startTempoDrag(x);
  const hit = noteAt(x, y);
  if (!hit) {
    // Empty space: a click adds a note, a drag selects (see mouseup).
    state.box = { x0: x, y0: y, x1: x, y1: y, add: e.shiftKey };
    return;
  }
  if (e.shiftKey) {
    state.selection.has(key(hit)) ? state.selection.delete(key(hit)) : state.selection.add(key(hit));
  } else if (!state.selection.has(key(hit))) {
    state.selection = new Set([key(hit)]);
  }
  state.drag = { anchor: hit.tick, startX: x, startRow: rowAt(y), delta: 0, rows: 0, moved: false };
  draw();
});

window.addEventListener("mousemove", (e) => {
  const { x, y } = pointer(e);
  canvas.style.cursor = e.target !== canvas ? "" : stemAt(x, y) ? "pointer"
    : state.tempoMode && (state.tempoDrag || (y >= RULER_H && beatNear(x))) ? "ew-resize" : "";
  if (state.tempoDrag) return dragTempo(x, e.altKey);
  if (state.box) {
    state.box.x1 = x; state.box.y1 = y;
    return draw();
  }
  if (!state.drag) return;
  const row = rowAt(y);
  if (Math.abs(x - state.drag.startX) > 3 || (row >= 0 && row !== state.drag.startRow)) state.drag.moved = true;
  if (!state.drag.moved) return;
  state.drag.delta = snapTick(secToTick(xToSec(x))) - state.drag.anchor;
  if (row >= 0) state.drag.rows = row - state.drag.startRow;
  draw();
});

window.addEventListener("mouseup", () => {
  if (state.tempoDrag) return endTempoDrag();
  const box = state.box, d = state.drag;
  state.box = null; state.drag = null;
  if (box) return finishBox(box);
  if (d && d.moved && (d.delta || d.rows)) moveSelection(d.delta, d.rows);
  else draw();
});

function finishBox(box) {
  if (Math.abs(box.x1 - box.x0) < 4 && Math.abs(box.y1 - box.y0) < 4) {
    const row = rowAt(box.y0);
    if (row < 0) return draw();
    if (!state.song.editable) return status(state.song.reason, true);
    const tick = snapTick(secToTick(xToSec(box.x0)));
    const lane = DEFAULT_LANE[ROWS[row]];
    commit((d) => d.levels[state.level].push({ tick, lane }));
    state.selection = new Set([`${tick}:${lane}`]);
    return draw();
  }
  const s0 = xToSec(Math.min(box.x0, box.x1)), s1 = xToSec(Math.max(box.x0, box.x1));
  const r0 = rowAt(Math.min(box.y0, box.y1)), r1 = rowAt(Math.max(box.y0, box.y1));
  const top = r0 < 0 ? 0 : r0, bottom = r1 < 0 ? ROWS.length - 1 : r1;
  if (!box.add) state.selection.clear();
  for (const n of notes()) {
    const s = tickToSec(n.tick), r = ROWS.indexOf(rowOf(n.lane));
    if (s >= s0 && s <= s1 && r >= top && r <= bottom) state.selection.add(key(n));
  }
  status(`${state.selection.size} notes selected`);
  draw();
}

function moveSelection(delta, rows = 0) {
  if (!state.selection.size) return;
  const moved = new Set();
  commit((d) => {
    for (const n of d.levels[state.level]) {
      if (!state.selection.has(key(n))) continue;
      n.tick = Math.max(0, n.tick + delta);
      const row = Math.min(ROWS.length - 1, Math.max(0, ROWS.indexOf(rowOf(n.lane)) + rows));
      n.lane = laneInRow(n.lane, row);
      moved.add(key(n));
    }
  });
  state.selection = new Set([...moved].filter((k) => notes().some((n) => key(n) === k)));
  draw();
}

function deleteSelection() {
  if (!state.selection.size) return;
  commit((d) => {
    d.levels[state.level] = d.levels[state.level].filter((n) => !state.selection.has(key(n)));
  });
  state.selection.clear();
  draw();
}

function toggleCymbal() {
  const targets = new Set(notes().filter((n) => state.selection.has(key(n)) && PADS.includes(color(n.lane))).map(key));
  if (!targets.size) return;
  const toggled = [];
  commit((d) => {
    for (const n of d.levels[state.level]) {
      if (!targets.has(key(n))) continue;
      n.lane = color(n.lane) + (n.lane.endsWith("-cym") ? "-tom" : "-cym");
      toggled.push(key(n));
    }
  });
  state.selection = new Set(toggled);
  draw();
}

function copySelection(cut = false) {
  const picked = notes().filter((n) => state.selection.has(key(n)));
  if (!picked.length) return status("Nothing selected to copy");
  const base = Math.min(...picked.map((n) => n.tick));
  state.clipboard = picked.map((n) => ({ dt: n.tick - base, lane: n.lane }));
  if (cut) deleteSelection();
  status(`${cut ? "Cut" : "Copied"} ${picked.length} notes · ⌘V pastes them at the playhead`);
}

function paste() {
  if (!state.clipboard) return status("Clipboard is empty");
  const base = snapTick(secToTick(position()));
  const pasted = state.clipboard.map((c) => ({ tick: base + c.dt, lane: c.lane }));
  commit((d) => d.levels[state.level].push(...pasted));
  state.selection = new Set(pasted.map(key));
  status(`Pasted ${pasted.length} notes at bar ${barOf(base)}`);
  draw();
}

function jumpUncertain(back = false) {
  const pos = position();
  const low = notes().filter((n) => confidence(n) < LOW_CONFIDENCE);
  if (!low.length) return status("No uncertain notes in this difficulty");
  const next = back
    ? [...low].reverse().find((n) => tickToSec(n.tick) < pos - 0.01) || low[low.length - 1]
    : low.find((n) => tickToSec(n.tick) > pos + 0.01) || low[0];
  const s = tickToSec(next.tick);
  if (transport.playing) transport.pause();
  transport.pausedAt = s;
  state.view.start = s - (canvas.clientWidth - GUTTER) * 0.4 / state.view.pxPerSec;
  state.selection = new Set([key(next)]);
  const i = low.indexOf(next) + 1;
  status(`Uncertain note ${i} of ${low.length} (confidence ${Math.round(confidence(next) * 100)} %, bar ${barOf(next.tick)})`);
  draw();
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (!state.song) return;
  if (e.ctrlKey || e.metaKey) {
    const { x } = pointer(e);
    const at = xToSec(x);
    state.view.pxPerSec = Math.min(2000, Math.max(20, state.view.pxPerSec * Math.exp(-e.deltaY * 0.002)));
    state.view.start = at - (x - GUTTER) / state.view.pxPerSec;
  } else {
    state.view.start += (e.deltaX + e.deltaY) / state.view.pxPerSec;
  }
  state.view.start = Math.max(-1, state.view.start);
  draw();
}, { passive: false });

window.addEventListener("keydown", (e) => {
  if (!state.song || ["SELECT", "INPUT"].includes(e.target.tagName)) return;
  const mod = e.metaKey || e.ctrlKey, k = e.key.toLowerCase();
  if (!$("sound-picker").hidden && ["Escape", "ArrowLeft", "ArrowRight"].includes(e.key)) {
    e.preventDefault();
    if (e.key === "Escape") closeSoundPicker();
    else stepSound(e.key === "ArrowLeft" ? -1 : 1);
    return;
  }
  if (e.code === "Space") { e.preventDefault(); play(); }
  else if (mod && k === "z") { e.preventDefault(); e.shiftKey ? state.doc.redo() : state.doc.undo(); }
  else if (mod && k === "s") { e.preventDefault(); save(e.shiftKey); }
  else if (mod && k === "c") { e.preventDefault(); copySelection(); }
  else if (mod && k === "x") { e.preventDefault(); copySelection(true); }
  else if (mod && k === "v") { e.preventDefault(); paste(); }
  else if (mod && k === "a") { e.preventDefault(); state.selection = new Set(notes().map(key)); draw(); }
  else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); deleteSelection(); }
  else if (k === "t" && !mod) toggleCymbal();
  else if (k === "n" && !mod) jumpUncertain(e.shiftKey);
  else if (k === "m" && !mod) setTempoMode(!state.tempoMode);
  else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && state.selection.size) {
    e.preventDefault();
    moveSelection(0, e.key === "ArrowUp" ? -1 : 1);
  }
  else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    const dir = e.key === "ArrowLeft" ? -1 : 1;
    if (state.selection.size) moveSelection((state.snap ? state.song.ppq / state.snap : 1) * dir);
    else seek(tickToSec(Math.max(0, secToTick(position()) + dir * state.song.ppq)));
  }
  else if (["1", "2", "3", "4"].includes(e.key) && !mod) setLevel(LEVELS[Number(e.key) - 1]);
  else if (e.key === "Home") { seek(0); state.view.start = -0.5; draw(); }
  else if (e.key === "Escape") { state.selection.clear(); draw(); }
});

// MARK: - UI

function status(text, error = false) {
  const s = $("status");
  s.textContent = text;
  s.classList.toggle("error", error);
}

function renderLevels() {
  const nav = $("levels");
  nav.innerHTML = "";
  if (!state.song) return;
  for (const l of LEVELS) {
    const b = document.createElement("button");
    b.className = l === state.level ? "active" : "";
    b.innerHTML = `${l[0].toUpperCase() + l.slice(1)}<span class="count">${state.doc.levels[l].length}</span>`;
    b.onclick = () => setLevel(l);
    nav.appendChild(b);
  }
  if (state.song && state.level !== "expert") renderTuneCount();
}

function setLevel(level) {
  state.level = level;
  state.selection.clear();
  renderLevels();
  renderTune();
  draw();
}

// MARK: - Tempo map

// Dragging a beat line pins its tick to a new time (tempomap.js); notes keep their ticks,
// so they move with the grid.

function beatNear(x, px = 6) {
  let best = null, dist = px;
  for (const b of beats()) {
    const d = Math.abs(secToX(tickToSec(b.tick)) - x);
    if (d <= dist) { best = b; dist = d; }
  }
  return best;
}

function nearestOnset(sec, within) {
  const o = state.onsets;
  let lo = 0, hi = o.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (o[mid] < sec) lo = mid + 1; else hi = mid; }
  let best = null;
  for (const i of [lo - 1, lo]) {
    if (i >= 0 && i < o.length && Math.abs(o[i] - sec) <= within && (best === null || Math.abs(o[i] - sec) < Math.abs(best - sec))) best = o[i];
  }
  return best;
}

function startTempoDrag(x) {
  const b = beatNear(x);
  if (!b) return;
  state.tempoDrag = { tick: b.tick, base: state.doc.tempo, key: `tempo-drag:${Date.now()}`, moved: false };
}

function dragTempo(x, free) {
  const d = state.tempoDrag;
  let target = xToSec(x);
  const hit = free ? null : nearestOnset(target, 0.04);
  if (hit !== null) target = hit;
  commit((doc) => { doc.tempo = d.base.pin(d.tick, target); }, { coalesce: d.key });  // one undo step per drag
  d.moved = true;
  const t = state.doc.tempo.tempos, i = t.findIndex((x) => x.tick === d.tick);
  status(d.tick === 0 ? `Offset ${Math.round(state.doc.tempo.offset * 1000)} ms`
    : `${t[i - 1].bpm.toFixed(2)} BPM before · ${t[i].bpm.toFixed(2)} BPM after${hit !== null ? " · on a hit" : ""}`);
  draw();
}

function endTempoDrag() {
  state.tempoDrag = null;
  draw();
}

function removeAnchor(tick) {
  commit((d) => { d.tempo = d.tempo.withoutAnchor(tick); });
  status("Anchor removed");
}

function setOffset(sec) {
  if (!Number.isFinite(sec)) return renderTempo();
  commit((d) => { d.tempo = d.tempo.withOffset(Math.round(sec * 1000) / 1000); });
}

function snapBeatToHit() {
  const pos = position();
  let beat = null;
  for (const b of beats()) {
    if (!beat || Math.abs(tickToSec(b.tick) - pos) < Math.abs(tickToSec(beat.tick) - pos)) beat = b;
  }
  const hit = beat && nearestOnset(tickToSec(beat.tick), 0.08);
  if (hit === null || hit === undefined) return status("No drum hit within 80 ms of the nearest beat", true);
  commit((d) => { d.tempo = d.tempo.pin(beat.tick, hit); });
  status(`Beat pinned to the hit (${Math.round((hit - pos) * 1000)} ms from the playhead)`);
}

function revertTempo() {
  commit((d) => { d.tempo = state.tempoOpened; });
  status("Tempo map and offset as opened");
}

function setTempoMode(on) {
  state.tempoMode = on && Boolean(state.song);
  $("mode-notes").classList.toggle("active", !state.tempoMode);
  $("mode-tempo").classList.toggle("active", state.tempoMode);
  if (state.tempoMode) state.selection.clear();
  renderTempo();
  renderTune();
  renderSoundButtons();
  draw();
}

function renderTempo() {
  $("tempo-tools").hidden = !state.tempoMode;
  $("notes-tools").hidden = state.tempoMode;
  if (!state.tempoMode) return;
  $("tempo-offset").value = Math.round(state.doc.tempo.offset * 1000);
  updateTempoReadout();
}

function updatePosition() {
  if (!state.song) return;
  const pos = Math.max(0, position()), m = Math.floor(pos / 60);
  const text = `${m}:${(pos - m * 60).toFixed(1).padStart(4, "0")} · bar ${Math.max(1, barOf(Math.max(0, secToTick(pos))))}`;
  if ($("position").textContent !== text) $("position").textContent = text;
}

function updateTempoReadout() {
  const tick = Math.max(0, secToTick(position()));
  const tm = state.doc.tempo, sig = tm.signatureAt(tick);
  const text = `${tm.at(tick).bpm.toFixed(2)} BPM · ${sig.num}/${sig.den}`;
  if ($("tempo-here").textContent !== text) $("tempo-here").textContent = text;
}

function drawTempo(w, t0, t1) {
  // Anchors and tempos in the ruler; under each beat a dot for how far the nearest drum
  // hit is: green on it, amber 10–25 ms, red further (no dot: no hit within 60 ms).
  g.font = "11px -apple-system, sans-serif";
  g.textBaseline = "middle";
  let lastLabel = -Infinity;
  for (const t of state.doc.tempo.tempos) {
    const x = secToX(t.sec + state.doc.tempo.offset);
    if (x < GUTTER - 10 || x > w + 10) continue;
    g.strokeStyle = "rgba(127,119,221,0.9)";
    g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(x, RULER_H); g.lineTo(x, canvas.clientHeight); g.stroke();
    g.fillStyle = "#7F77DD";
    g.beginPath(); g.moveTo(x, RULER_H - 11); g.lineTo(x + 5, RULER_H - 6); g.lineTo(x, RULER_H - 1); g.lineTo(x - 5, RULER_H - 6); g.closePath(); g.fill();
    if (x - lastLabel > 80) {
      const label = `${t.bpm.toFixed(1)} BPM`;
      g.fillStyle = "rgba(21,23,28,0.85)";
      g.fillRect(x + 7, 1, g.measureText(label).width + 6, 13);
      g.fillStyle = "#b9b4f0";
      g.fillText(label, x + 10, 8);
      lastLabel = x;
    }
  }
  if (!state.onsets.length) return;
  const y = RULER_H + 6;
  for (const b of beats()) {
    const s = tickToSec(b.tick);
    if (s < t0 - 0.1 || s > t1 + 0.1) continue;
    const hit = nearestOnset(s, 0.06);
    if (hit === null) continue;
    const off = Math.abs(hit - s);
    g.fillStyle = off < 0.010 ? "#3fbf6f" : off < 0.025 ? "#f5a623" : "#ef4f4f";
    g.beginPath(); g.arc(secToX(s), y, 3.5, 0, Math.PI * 2); g.fill();
  }
}

function detectOnsets(buf) {
  // Drum hits: frames of 2 ms whose energy jumps by 8 dB within 4 ms, one per 30 ms.
  const rate = 500, step = buf.sampleRate / rate, n = Math.floor(buf.length / step);
  const energy = new Float32Array(n);
  for (let ch = 0; ch < buf.numberOfChannels; ch++) {
    const d = buf.getChannelData(ch);
    for (let i = 0; i < n; i++) {
      let sum = 0;
      const end = Math.min(d.length, Math.floor((i + 1) * step));
      for (let k = Math.floor(i * step); k < end; k += 2) sum += d[k] * d[k];
      energy[i] += sum;
    }
  }
  let top = -Infinity;
  const db = energy.map((e) => { const v = 10 * Math.log10(e + 1e-9); top = Math.max(top, v); return v; });
  const rise = new Float32Array(n);
  for (let i = 2; i < n; i++) rise[i] = db[i] - db[i - 2];
  const out = [];
  for (let i = 2; i < n; i++) {
    if (rise[i] < 8 || db[i] < top - 45) continue;
    let peak = true;
    for (let k = Math.max(2, i - 15); k <= Math.min(n - 1, i + 15) && peak; k++) if (rise[k] > rise[i]) peak = false;
    if (peak && (!out.length || i / rate - out[out.length - 1] > 0.03)) out.push((i - 1) / rate);
  }
  return out;
}

function mergeOnsets(a, b) {
  const all = [...a, ...b].sort((x, y) => x - y), out = [];
  for (const s of all) if (!out.length || s - out[out.length - 1] > 0.015) out.push(s);
  return out;
}

// MARK: - Pad sounds

// Beat-Train can mute the drum stem and play one sound per pad instead. Each row (= the
// pad's voice) gets a button on the right; it steps through hits of that row cut from
// the drum stem, best first (sounds.py), and the chosen one is saved on Save.

const picker = { voice: null, cuts: [], i: 0, player: null };

function renderSoundButtons() {
  const box = $("row-sounds");
  box.innerHTML = "";
  if (!state.song || state.tempoMode) return;
  const top = lanesTop();
  ROWS.forEach((voice, i) => {
    const pick = state.doc.sounds[voice];
    const b = document.createElement("button");
    b.className = `row-sound${pick ? " set" : ""}`;
    b.textContent = "♪";
    b.style.top = `${top + i * ROW_H + (ROW_H - 26) / 2}px`;
    b.disabled = !state.song.has_drums;
    b.title = !state.song.has_drums ? "Pad sounds are cut from the drum stem; this song has none"
      : pick?.file ? `${ROW_NAMES[voice]}: your own ${pick.file}`
      : pick ? `${ROW_NAMES[voice]}: a sound is picked for Beat-Train's pad`
      : `Pick a ${ROW_NAMES[voice]} sound for Beat-Train's pad`;
    b.onclick = () => (picker.voice === voice && !$("sound-picker").hidden ? closeSoundPicker() : openSoundPicker(voice));
    box.appendChild(b);
  });
}

// The cut being offered, as a band over the waveforms and in its row.
function drawSoundCut(top) {
  const c = !$("sound-picker").hidden && picker.cuts[picker.i];
  if (!c) return;
  const x0 = secToX(c.start), x1 = Math.max(x0 + 2, secToX(c.start + c.length));
  const row = top + ROWS.indexOf(picker.voice) * ROW_H;
  g.fillStyle = "rgba(91,156,255,0.12)";
  g.fillRect(x0, RULER_H, x1 - x0, top - RULER_H);
  g.fillStyle = "rgba(91,156,255,0.28)";
  g.strokeStyle = "rgba(91,156,255,0.9)";
  g.lineWidth = 1;
  g.beginPath();
  g.roundRect(x0, row + 4, x1 - x0, ROW_H - 8, 4);
  g.fill();
  g.stroke();
}

async function openSoundPicker(voice) {
  Object.assign(picker, { voice, cuts: [], i: 0 });
  renderSoundPicker("Finding clean hits…");
  try {
    picker.cuts = await api("/api/sounds", { voice, expert: expertPairs(), tempos: tempoPairs(), offset: state.doc.tempo.offset });
  } catch (e) { return renderSoundPicker(e.message); }
  if (picker.voice !== voice) return;
  const pick = state.doc.sounds[voice];
  if (pick && !pick.file) {
    const j = picker.cuts.findIndex((c) => Math.abs(c.start - pick.start) < 0.002);
    if (j >= 0) picker.i = j;
    else picker.cuts.unshift({ ...pick, at: pick.start, free: null });
  }
  showCut();
}

function renderSoundPicker(message) {
  const p = $("sound-picker"), voice = picker.voice, c = picker.cuts[picker.i];
  p.hidden = false;
  // Below the row, or above it when there is no room: the row shows the cut.
  const row = lanesTop() + ROWS.indexOf(voice) * ROW_H;
  const below = row + ROW_H + 4;
  p.style.top = `${below + p.offsetHeight <= canvas.clientHeight - 4 ? below : Math.max(4, row - p.offsetHeight - 4)}px`;
  $("sp-title").textContent = `${ROW_NAMES[voice]} sound`;
  const pick = state.doc.sounds[voice];
  const inUse = c && pick && !pick.file && Math.abs(c.start - pick.start) < 0.002;
  $("sp-info").textContent = message ?? (!c ? "No notes in this row to cut a sound from."
    : `Hit ${picker.i + 1} of ${picker.cuts.length} · bar ${barOf(Math.max(0, Math.round(secToTick(c.at))))} · ` +
      `${c.length.toFixed(2)} s${c.free !== null && c.free < c.length + 0.01 ? " (cut before the next note)" : ""}${inUse ? " · in use" : ""}`);
  for (const id of ["sp-prev", "sp-next"]) $(id).disabled = picker.cuts.length < 2;
  $("sp-play").disabled = $("sp-use").disabled = !c;
  $("sp-none").disabled = !pick;
}

function showCut() {
  const c = picker.cuts[picker.i];
  renderSoundPicker();
  if (!c) return;
  state.view.start = c.at - (canvas.clientWidth - GUTTER) * 0.25 / state.view.pxPerSec;
  draw();
  playCut();
}

function playCut() {
  const c = picker.cuts[picker.i];
  if (!c) return;
  picker.player?.pause();
  picker.player = new Audio(`/api/sound.wav?start=${c.start}&length=${c.length}`);
  picker.player.play().catch(() => {});
}

function stepSound(dir) {
  const n = picker.cuts.length;
  if (n < 2) return;
  picker.i = (picker.i + dir + n) % n;
  showCut();
}

function useSound() {
  const c = picker.cuts[picker.i], voice = picker.voice;
  commit((d) => { d.sounds[voice] = { start: c.start, length: c.length }; });
  status(`${ROW_NAMES[voice]} sound picked; Save writes it as sounds/${voice}.wav`);
  closeSoundPicker();
}

function removeSound() {
  const voice = picker.voice;
  commit((d) => { d.sounds[voice] = null; });
  status(`No ${ROW_NAMES[voice]} sound: with pad sounds on, that drum stays silent in Beat-Train`);
  closeSoundPicker();
}

function closeSoundPicker() {
  picker.player?.pause();
  picker.voice = null;
  $("sound-picker").hidden = true;
  if (state.song) draw();
}

// MARK: - Level settings

const INSTRUMENTS = [["kick", "Kick"], ["snare", "Snare"], ["hihat", "Hi-hat"], ["toms", "Toms"], ["cymbals", "Cymbals"]];
const expertPairs = () => state.doc.levels.expert.map((n) => [n.tick, n.lane]);
const tempoPairs = () => state.doc.tempo.pairs();
const tuneSettings = () => state.doc.settings[state.level];

function renderTune() {
  const s = state.song && state.level !== "expert" && !state.tempoMode ? tuneSettings() : null;
  $("tune").hidden = !s;
  if (!s) return;
  const fixed = s.preset === "clone-hero";
  $("tune").classList.toggle("fixed", fixed);
  $("tune-title").textContent = state.level[0].toUpperCase() + state.level.slice(1);
  $("tune-preset").value = s.preset;
  // Clone Hero rules have no controls; show the MPC values greyed out.
  const c = fixed ? state.song.presets.mpc[state.level] : s;
  $("tune-density").value = Math.round(c.density * 100);
  $("tune-density-v").textContent = `${Math.round(c.density * 100)} %`;
  $("tune-density-v").className = "value";
  const inst = $("tune-inst");
  if (!inst.children.length) {
    for (const [id, name] of INSTRUMENTS) {
      const label = document.createElement("label");
      label.className = "slider";
      label.title = `Share of Expert's ${name.toLowerCase()} notes; 0 turns it off`;
      label.innerHTML = `${name} <input type="range" min="0" max="100" step="5" data-inst="${id}"><span class="value"></span>`;
      label.querySelector("input").oninput = (e) => setShare(id, Number(e.target.value) / 100);
      inst.appendChild(label);
    }
  }
  for (const input of inst.querySelectorAll("input")) {
    const share = Math.min(1, c.density * c.weights[input.dataset.inst]);
    input.value = Math.round(share * 100);
    input.nextElementSibling.textContent = share > 0 ? `${Math.round(share * 100)} %` : "off";
  }
  $("tune-finest").value = String(c.finest);
  $("tune-triplets").checked = c.triplets;
  $("tune-triplets").disabled = c.finest === 1;
  $("tune-hands").value = String(c.max_hands);
  $("tune-notes").value = String(c.max_notes);
  $("tune-gap").value = Math.round(c.min_gap_ms);
  renderTuneCount();
}

function renderTuneCount() {
  const ex = state.doc.levels.expert, mine = notes();
  if (!ex.length) return ($("tune-count").textContent = "");
  const span = Math.max(1, tickToSec(ex[ex.length - 1].tick) - tickToSec(ex[0].tick));
  $("tune-count").textContent = `${mine.length} notes · ${(mine.length / span).toFixed(1)}/s · ` +
    `${Math.round((100 * mine.length) / ex.length)} % of Expert`;
}

function editTune(change) {
  const s = structuredClone(tuneSettings());
  change(s);
  s.preset = "custom";
  applyTune(s);
}

function setShare(inst, share) {
  editTune((s) => {
    // The slider shows the share; the weight keeps it relative to the density.
    s.weights[inst] = share / Math.max(s.density, 0.05);
  });
}

let tuneTimer = null;

// Slider moves of one level within a second are one undo step, together with the notes
// they generate; a preset is a step of its own.
function applyTune(s, key = `tune:${state.level}`) {
  const level = state.level;
  commit((d) => { d.settings[level] = s; }, { coalesce: key, within: 1000 });
  clearTimeout(tuneTimer);
  tuneTimer = setTimeout(async () => {
    try {
      const data = await api("/api/reduce", { expert: expertPairs(), settings: { [level]: s }, tempos: tempoPairs() });
      if (state.doc.settings[level] !== s) return;  // a newer edit or an undo came first
      commit((d) => { d.levels[level] = data[level].map(([tick, lane]) => ({ tick, lane })); }, { coalesce: key });
      renderTuneCount();
    } catch (e) { status(e.message, true); }
  }, 120);
}

function pickPreset(preset) {
  const current = tuneSettings();
  let s;
  if (preset === "custom") s = { ...structuredClone(current.preset === "clone-hero" ? state.song.presets.mpc[state.level] : current), preset: "custom" };
  else s = structuredClone(state.song.presets[preset][state.level]);
  applyTune(s, `preset:${state.level}:${Date.now()}`);
}

function setSongControls(enabled) {
  for (const id of ["play", "mode-notes", "mode-tempo", "uncertain", "regen", "save", "save-menu", "undo", "redo", "speed", "snap", "ticks", "metronome"]) {
    $(id).disabled = !enabled;
  }
  if (enabled) renderHistory();
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), { data, status: res.status });
  return data;
}

async function regenerate(which) {
  const levels = which === "all" ? ["hard", "medium", "easy"] : [which];
  const names = levels.map((l) => l[0].toUpperCase() + l.slice(1)).join(", ").replace(/, (\w+)$/, " and $1");
  if (!confirm(`Generate ${names} from Expert? Hand edits on ${levels.length > 1 ? "those levels" : "that level"} are replaced.`)) return;
  try {
    const data = await api("/api/reduce", { expert: expertPairs(), settings: state.doc.settings, tempos: tempoPairs() });
    commit((d) => {
      for (const l of levels) d.levels[l] = data[l].map(([tick, lane]) => ({ tick, lane }));
    });
    status(`${names} regenerated from Expert`);
    showLevelsHint();
  } catch (e) { status(e.message, true); }
}

function showLevelsHint() {
  if ((state.app.settings.hidden_hints || []).includes("levels")) return;
  $("hint-hide").checked = false;
  $("hint").hidden = false;
}

async function closeLevelsHint() {
  $("hint").hidden = true;
  if (!$("hint-hide").checked) return;
  try {
    state.app.settings = await api("/api/settings", { hide_hint: "levels" });
  } catch (e) { status(e.message, true); }
}

async function save(as = false) {
  // Save writes into the open song folder. Save as copies the folder into another place
  // (into BeatTrain/Songs on an SD card), saves there and keeps editing the copy. A new
  // song's first save asks for the place either way.
  if (!state.song) return false;
  if (!state.song.editable) { status(state.song.reason, true); return false; }
  const body = state.doc.saveBody();
  const draft = state.song.draft;
  if (draft || as) {
    const settings = state.app.settings;
    body.dest = await pickPath("save", draft ? settings.out_dir : settings.export_dir || settings.out_dir);
    if (!body.dest) return false;
  }
  const url = as && !draft ? "/api/save_as" : "/api/save";
  status("Saving…");
  try {
    let data;
    try {
      data = await api(url, body);
    } catch (e) {
      if (e.status !== 409 || !confirm(`${e.data.target} exists. Replace it?`)) throw e;
      data = await api(url, { ...body, overwrite: true });
    }
    if (draft || as) {
      state.song.draft = false;
      state.song.folder = data.folder;
      state.app.settings = data.settings;
      status(draft ? `Saved the new song in ${data.folder}` : `Saved a copy in ${data.folder}; you're editing the copy now`);
    } else {
      status(`Saved ${data.file} in ${state.song.folder} (original kept as ${data.file}.orig)`);
    }
    state.doc.markSaved();
    return true;
  } catch (e) { status(e.message, true); return false; }
}

// MARK: - Opening and converting

function okToLeave() {
  return !state.doc?.dirty || confirm("Discard the unsaved changes in this song?");
}

async function pickPath(kind, start) {
  if (window.trackyardDesktop) return (await window.trackyardDesktop.pick(kind, start)) || null;
  if (!state.app.native_picker) {
    return prompt(kind === "audio" ? "Path of the audio file:" : "Path of the folder:", start || "") || null;
  }
  status(kind === "audio" ? "Choose a song in the dialog…" : "Choose a folder in the dialog…");
  const r = await api("/api/pick", { kind, start });
  status(r.cancelled ? "Cancelled" : r.path);
  return r.cancelled ? null : r.path;
}

async function openMp3() {
  if (!okToLeave()) return;
  try {
    const path = await pickPath("audio");
    if (!path) return;
    const job = await api("/api/convert", { audio: path });
    showJob(job);
    pollJob();
  } catch (e) { status(e.message, true); }
}

async function openFolder() {
  if (!okToLeave()) return;
  try {
    const path = await pickPath("folder", state.app.settings.out_dir);
    if (!path) return;
    await api("/api/open", { folder: path });
    await refresh();
  } catch (e) { status(e.message, true); }
}

function showProgress(prefix, task) {
  $(`${prefix}-progress`).hidden = task.state !== "running";
  if (task.state !== "running") return;
  const known = typeof task.fraction === "number";
  $(`${prefix}-step`).textContent = known ? `${task.step}… ${Math.round(task.fraction * 100)} %` : `${task.step}…`;
  $(`${prefix}-time`).textContent = `${Math.floor(task.elapsed / 60)}:${String(task.elapsed % 60).padStart(2, "0")}`;
  $(`${prefix}-bar`).classList.toggle("busy", !known);
  $(`${prefix}-bar`).style.width = known ? `${Math.round(task.fraction * 100)}%` : "";
}

function showJob(job) {
  $("job").hidden = false;
  $("start").hidden = true;
  const name = job.audio.split(/[\\/]/).pop();
  $("job-title").textContent = job.state === "failed" ? "Conversion failed" : job.state === "done" ? "Done" : `Converting ${name}`;
  showProgress("job", job);
  $("job-log").textContent = [...job.log, job.error].filter(Boolean).join("\n");
  $("job-close").hidden = job.state === "running";
}

async function pollJob() {
  const job = await api("/api/job");
  if (!job) return;
  showJob(job);
  if (job.state === "running") return setTimeout(pollJob, 700);
  if (job.state === "done") {
    $("job").hidden = true;
    await refresh();
    status("Converted. Save (⌘S) to choose where the song goes.");
    showLevelsHint();
  }
}

// NVIDIA GPU support (Windows app): offer the download, show its progress, then use it.
let gpuTimer = null;
function showGpu(gpu) {
  $("gpu").hidden = !gpu;
  if (!gpu) return;
  const task = gpu.task;
  const running = task && task.state === "running";
  const restart = gpu.installed && !gpu.active;
  $("gpu-text").textContent =
    gpu.active ? `Songs convert on the GPU (${gpu.name}).`
    : restart ? "GPU support is ready. Restart Trackyard to use it."
    : running ? `Getting GPU support for the ${gpu.name}. You can keep working; this takes a while.`
    : task && task.state === "failed" ? `GPU support failed: ${task.error}`
    : `Your ${gpu.name} can convert songs several times faster than the CPU. It needs a one-time download.`;
  showProgress("gpu", task || { state: "" });
  $("gpu-install").hidden = gpu.active || restart || running;
  $("gpu-install").textContent = task && task.state === "failed" ? "Try again" : "Download GPU support (2 GB)";
  $("gpu-restart").hidden = !restart || !window.trackyardDesktop;
  clearTimeout(gpuTimer);
  if (running) gpuTimer = setTimeout(pollGpu, 1000);
}

async function pollGpu() {
  try { showGpu((await api("/api/state")).gpu); } catch (e) { status(e.message, true); }
}

async function refresh() {
  state.app = await api("/api/state");
  showGpu(state.app.gpu);
  if (state.app.job && state.app.job.state === "running") { showJob(state.app.job); pollJob(); }
  if (!state.app.song) {
    state.song = state.doc = null;
    closeSoundPicker();
    renderSoundButtons();
    $("start").hidden = false;
    $("song-title").textContent = "Trackyard";
    setSongControls(false);
    $("transport").hidden = $("toolbar").hidden = true;
    $("stem-volumes").innerHTML = "";
    renderLevels();
    renderTune();
    return draw();
  }
  $("start").hidden = true;
  state.song = await api("/api/song");
  state.doc = new SongDoc({
    levels: state.song.levels, settings: structuredClone(state.song.level_settings),
    tempo: new TempoMap(state.song.ppq, state.song.tempos, state.song.offset, state.song.signatures),
    sounds: state.song.sounds,
    dirty: Boolean(state.song.draft),  // a new song isn't saved anywhere yet
  });
  closeSoundPicker();
  state.doc.onChange(onDocChange);
  state.selection.clear();
  beatCache = { end: -1, list: [] };
  state.tempoOpened = state.doc.tempo;
  state.onsets = [];
  setTempoMode(false);
  state.level = "expert";
  transport.stop();
  transport.pausedAt = 0;
  $("song-title").textContent = [state.song.artist, state.song.name].filter(Boolean).join(" – ");
  document.title = `${state.song.name} · Trackyard`;
  setSongControls(true);
  renderTune();
  const first = state.doc.levels.expert[0];
  state.view.start = first ? Math.max(-0.5, tickToSec(first.tick) - 1) : -0.5;
  resize();
  await loadAudio();
}

window.addEventListener("beforeunload", (e) => { if (state.doc?.dirty) e.preventDefault(); });
new ResizeObserver(() => resize()).observe(document.querySelector("main"));  // also when the level panel shows or hides
$("play").onclick = () => play();
$("save").onclick = () => save();
$("undo").onclick = () => state.doc.undo();
$("redo").onclick = () => state.doc.redo();
$("regen").onchange = (e) => {
  const which = e.target.value;
  e.target.value = "";
  if (which) regenerate(which);
};
$("uncertain").onclick = () => jumpUncertain();
$("open-mp3").onclick = $("start-mp3").onclick = () => openMp3();
$("open-folder").onclick = $("start-folder").onclick = () => openFolder();
$("hint-ok").onclick = () => closeLevelsHint();
$("sp-prev").onclick = () => stepSound(-1);
$("sp-next").onclick = () => stepSound(1);
$("sp-play").onclick = () => playCut();
$("sp-use").onclick = () => useSound();
$("sp-none").onclick = () => removeSound();
$("sp-close").onclick = () => closeSoundPicker();
$("gpu-install").onclick = async () => {
  try { await api("/api/gpu", {}); pollGpu(); } catch (e) { status(e.message, true); }
};
$("gpu-restart").onclick = () => window.trackyardDesktop.restart();
$("save-menu").onclick = (e) => { e.stopPropagation(); $("save-pop").hidden = !$("save-pop").hidden; };
$("save-as").onclick = () => { $("save-pop").hidden = true; save(true); };
window.addEventListener("click", () => { $("save-pop").hidden = true; });
$("job-close").onclick = () => { $("job").hidden = true; if (!state.song) $("start").hidden = false; };
$("snap").onchange = (e) => { state.snap = Number(e.target.value); };
$("tune-preset").onchange = (e) => pickPreset(e.target.value);
$("mode-notes").onclick = () => setTempoMode(false);
$("mode-tempo").onclick = () => setTempoMode(true);
$("tempo-offset").onchange = (e) => setOffset(Number(e.target.value) / 1000);
$("offset-down").onclick = () => setOffset(state.doc.tempo.offset - 0.005);
$("offset-up").onclick = () => setOffset(state.doc.tempo.offset + 0.005);
$("snap-beat").onclick = () => snapBeatToHit();
$("tempo-revert").onclick = () => revertTempo();
canvas.addEventListener("dblclick", (e) => {
  if (!state.tempoMode) return;
  const { x } = pointer(e);
  const b = beatNear(x);
  if (b && b.tick > 0 && state.doc.tempo.isAnchor(b.tick)) removeAnchor(b.tick);
});
$("tune-density").oninput = (e) => editTune((s) => { s.density = Number(e.target.value) / 100; });
$("tune-finest").onchange = (e) => editTune((s) => { s.finest = Number(e.target.value); });
$("tune-triplets").onchange = (e) => editTune((s) => { s.triplets = e.target.checked; });
$("tune-hands").onchange = (e) => editTune((s) => { s.max_hands = Number(e.target.value); });
$("tune-notes").onchange = (e) => editTune((s) => { s.max_notes = Number(e.target.value); });
$("tune-gap").onchange = (e) => editTune((s) => { s.min_gap_ms = Math.max(0, Number(e.target.value) || 0); });
$("speed").onchange = (e) => {
  state.speed = Number(e.target.value);
  transport.setSpeed(state.speed);
};
$("ticks").onclick = (e) => { state.ticksOn = !state.ticksOn; e.target.classList.toggle("on", state.ticksOn); };
$("metronome").onclick = (e) => { state.metronomeOn = !state.metronomeOn; e.target.classList.toggle("on", state.metronomeOn); };

refresh().catch((e) => status(`Could not start: ${e.message}`, true));
