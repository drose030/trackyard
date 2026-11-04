"use strict";

const LEVELS = ["expert", "hard", "medium", "easy"];
// Rows top to bottom: Beat-Train's six instruments. In the chart file (Clone Hero pro
// drums) "orange" is the green lane as a cymbal and "green" the green lane as a tom.
const ROWS = ["red", "yellow", "blue", "orange", "green", "kick"];
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
  level: "expert",
  levels: {},          // level → [{tick, lane}], sorted by tick
  selection: new Set(), // keys "tick:lane" in the current level
  clipboard: null,     // [{dt, lane}]
  undo: [], redo: [],
  dirty: false,
  view: { start: 0, pxPerSec: 160 },
  snap: 4,
  speed: 1,
  ticksOn: true, metronomeOn: false,
  drag: null,
  box: null,           // rubber-band selection
  levelSettings: {},   // hard/medium/easy → how they are generated (trackyard.json)
  tuning: null,        // {level, at}: slider moves within a second share one undo step
  tempoMode: false,
  tempoDrag: null,     // {tick, base, baseOffset, before, moved}
  tempoOpened: null,   // tempo map and offset as opened, for "Revert tempo"
  tempoSaved: "",      // tempoKey() at the last save
  onsets: [],          // drum hits in audio seconds, sorted
};

// Playback runs on <audio> elements (they keep the pitch when slowed down); the decoded
// buffers are only for the waveforms. Note ticks and the metronome use Web Audio.
const audio = { ctx: null, els: {}, peaks: {}, muted: {}, playing: false, pausedAt: 0, scheduledUntil: 0, timer: null };

const canvas = document.getElementById("view");
const g = canvas.getContext("2d");
const $ = (id) => document.getElementById(id);

// MARK: - Time

function tempoAt(tick) {
  const t = state.song.tempos;
  let i = 0;
  while (i + 1 < t.length && t[i + 1].tick <= tick) i++;
  return t[i];
}

function tickToSec(tick) {  // audio time
  const t = tempoAt(tick);
  return t.sec + (tick - t.tick) / state.song.ppq * 60 / t.bpm + state.song.offset;
}

function secToTick(sec) {
  const s = sec - state.song.offset, t = state.song.tempos;
  let i = 0;
  while (i + 1 < t.length && t[i + 1].sec <= s) i++;
  return t[i].tick + (s - t[i].sec) * t[i].bpm / 60 * state.song.ppq;
}

function snapTick(tick) {
  if (!state.snap) return Math.max(0, Math.round(tick));
  const step = state.song.ppq / state.snap;
  return Math.max(0, Math.round(Math.round(tick / step) * step));
}

function barOf(tick) {
  let bar = 0;
  for (const b of state.song.beats) { if (b.tick > tick) break; if (b.down) bar++; }
  return bar;
}

const secToX = (sec) => GUTTER + (sec - state.view.start) * state.view.pxPerSec;
const xToSec = (x) => state.view.start + (x - GUTTER) / state.view.pxPerSec;
const lanesTop = () => RULER_H + waveH() * waveStems().length;

// MARK: - Notes and history

const key = (n) => `${n.tick}:${n.lane}`;
const color = (lane) => lane.split("-")[0];
const rowOf = (lane) => (lane === "green-cym" ? "orange" : lane === "green-tom" ? "green" : color(lane));
const notes = () => state.levels[state.level];

function snapshot() {
  const levels = {};
  for (const l of LEVELS) levels[l] = state.levels[l].map((n) => ({ ...n }));
  return { levels, settings: structuredClone(state.levelSettings),
           tempos: structuredClone(state.song.tempos), offset: state.song.offset };
}

function commit(change, coalesce = false) {
  if (!coalesce) state.undo.push(snapshot());
  state.redo = [];
  change();
  for (const l of LEVELS) state.levels[l] = dedupe(state.levels[l]);
  syncPadTypes();
  setDirty(true);
  draw();
}

function dedupe(list) {
  const seen = new Map();
  for (const n of list) seen.set(`${n.tick}:${color(n.lane)}`, n);  // one note per lane and tick
  return [...seen.values()].sort((a, b) => a.tick - b.tick || ROWS.indexOf(rowOf(a.lane)) - ROWS.indexOf(rowOf(b.lane)));
}

function syncPadTypes() {
  // notes.mid stores "tom" per lane and tick for all difficulties at once, so the level
  // being edited decides cymbal vs. tom for the same lane and tick everywhere.
  const type = new Map();
  for (const n of notes()) if (PADS.includes(color(n.lane))) type.set(`${n.tick}:${color(n.lane)}`, n.lane);
  for (const l of LEVELS) {
    if (l === state.level) continue;
    for (const n of state.levels[l]) {
      const t = type.get(`${n.tick}:${color(n.lane)}`);
      if (t) n.lane = t;
    }
  }
}

function undo(from, to) {
  if (!from.length) return;
  to.push(snapshot());
  ({ levels: state.levels, settings: state.levelSettings, tempos: state.song.tempos, offset: state.song.offset } = from.pop());
  renderTempo();
  state.tuning = null;
  state.selection.clear();
  renderTune();
  setDirty(true);
  draw();
}

function setDirty(dirty) {
  state.dirty = dirty;
  $("save").classList.toggle("dirty", dirty);
  $("undo").disabled = !state.undo.length;
  $("redo").disabled = !state.redo.length;
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
  const rank = (n) => (/^drums/i.test(n) ? 0 : /^crowd/i.test(n) ? 2 : 1);
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

function setLevelVolume(key, value) {
  mix.levels[key] = value;
  try { localStorage.setItem("trackyard.mix", JSON.stringify(mix.levels)); } catch { /* not remembered */ }
  if (key.startsWith("stem:")) {
    const el = audio.els[key.slice(5)];
    if (el) el.volume = Math.min(1, value);
  } else if (audio[key]) {
    audio[key].gain.value = value;
  }
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
  audio.muted[stem] = !audio.muted[stem];
  audio.els[stem].muted = audio.muted[stem];
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
    g.fillStyle = audio.muted[name] ? "#5c6270" : "#9097a6";
    g.fillText(label === name ? name : `${label}…`, 10, mid);
    drawSpeaker(GUTTER - 20, mid, audio.muted[name]);
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
  for (const b of state.song.beats) {
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
  for (const b of state.song.beats) {
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
    g.fillStyle = audio.muted[name] ? "#2c313b" : /^drums/i.test(name) ? "#6f7a92" : "#4d5566";
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

function refElement() {
  const els = Object.values(audio.els);
  return els.reduce((a, b) => ((b.duration || 0) > (a.duration || 0) ? b : a), els[0]);
}

function position() {
  const ref = audio.playing && refElement();
  return ref ? ref.currentTime : audio.pausedAt;
}

async function loadAudio() {
  stopAudio();
  audio.els = {}; audio.peaks = {}; audio.muted = {};
  if (!audio.ctx) {
    audio.ctx = new AudioContext();
    for (const bus of ["ticks", "metronome"]) {
      audio[bus] = audio.ctx.createGain();
      audio[bus].gain.value = mix.levels[bus] ?? 1;
      audio[bus].connect(audio.ctx.destination);
    }
  }
  const names = state.song.stems;
  status(`Loading audio (${names.length} stems)…`);
  await Promise.all(names.map(async (name) => {
    const url = `/audio/${encodeURIComponent(name)}?v=${state.app.version}`;
    const stem = name.replace(/\.[^.]+$/, "");
    try {
      const data = await (await fetch(url)).arrayBuffer();
      const buf = await audio.ctx.decodeAudioData(data);
      audio.peaks[stem] = peaks(buf);
      if (/^drums/i.test(stem) || (/^song$/i.test(stem) && !names.some((n) => /^drums/i.test(n)))) {
        state.onsets = mergeOnsets(state.onsets, detectOnsets(buf));
      }
      const el = new Audio(url);
      el.preload = "auto";
      el.preservesPitch = true;
      el.webkitPreservesPitch = true;
      el.muted = audio.muted[stem] = /^crowd/i.test(stem);
      el.volume = Math.min(1, mix.levels[`stem:${stem}`] ?? 1);
      el.addEventListener("ended", () => { if (el === refElement()) pause(); });
      audio.els[stem] = el;
    } catch (e) {
      console.warn("could not load", name, e);
    }
  }));
  renderMixer();
  resize();
  const n = notes().filter((x) => confidence(x) < LOW_CONFIDENCE).length;
  status(`${Object.keys(audio.els).length} stems loaded · ${n} uncertain notes in ${state.level} (N jumps to them)`);
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

async function play() {
  if (!state.song) return;
  if (audio.playing) return pause();
  audio.ctx.resume();
  const from = Math.max(0, audio.pausedAt);
  for (const el of Object.values(audio.els)) {
    el.currentTime = Math.min(from, el.duration || from);
    el.playbackRate = state.speed;
  }
  await Promise.all(Object.values(audio.els).map((el) => el.play().catch(() => {})));
  audio.scheduledUntil = from;
  audio.playing = true;
  $("play").textContent = "❚❚ Pause";
  // Clicks run on their own timer: animation frames stop in background tabs.
  clearInterval(audio.timer);
  audio.timer = setInterval(tick, 25);
  requestAnimationFrame(frame);
}

function pause() {
  audio.pausedAt = position();
  stopAudio();
  draw();
}

function stopAudio() {
  for (const el of Object.values(audio.els)) el.pause();
  clearInterval(audio.timer);
  audio.playing = false;
  $("play").textContent = "▶︎ Play";
}

function seek(sec) {
  const was = audio.playing;
  if (was) pause();
  audio.pausedAt = Math.max(0, sec);
  if (was) play(); else draw();
}

function tick() {
  if (!audio.playing) return;
  const ref = refElement(), pos = position();
  for (const el of Object.values(audio.els)) {  // keep the stems together
    if (el !== ref && Math.abs(el.currentTime - pos) > 0.04) el.currentTime = pos;
  }
  scheduleClicks(pos);
}

function frame() {
  if (!audio.playing) return;
  const pos = position(), w = canvas.clientWidth;
  if (secToX(pos) > w * 0.75 || secToX(pos) < GUTTER) state.view.start = pos - (w - GUTTER) * 0.2 / state.view.pxPerSec;
  draw();
  requestAnimationFrame(frame);
}

function scheduleClicks(pos) {
  // After a seek (or if the audio ever jumps), start scheduling from where it plays now.
  if (Math.abs(audio.scheduledUntil - pos) > 0.5) audio.scheduledUntil = pos;
  const until = pos + 0.12, from = Math.max(audio.scheduledUntil, pos - 0.02);
  if (until <= from) return;
  const at = (s) => audio.ctx.currentTime + (s - pos) / state.speed;
  if (state.ticksOn) {
    for (const n of notes()) {
      const s = tickToSec(n.tick);
      if (s >= from && s < until) click(n.lane, at(s));
    }
  }
  if (state.metronomeOn) {
    for (const b of state.song.beats) {
      const s = tickToSec(b.tick);
      if (s >= from && s < until) beep(b.down ? 1600 : 1100, at(s));
    }
  }
  audio.scheduledUntil = until;
}

function click(lane, when) {
  const ctx = audio.ctx;
  when = Math.max(when, ctx.currentTime);
  const gain = ctx.createGain();
  gain.connect(audio.ticks);
  const c = color(lane);
  if (lane === "kick" || lane.endsWith("-tom")) {
    const osc = ctx.createOscillator();
    osc.frequency.setValueAtTime(lane === "kick" ? 110 : { yellow: 330, blue: 240, green: 170 }[c], when);
    osc.frequency.exponentialRampToValueAtTime(lane === "kick" ? 45 : 90, when + 0.08);
    gain.gain.setValueAtTime(0.5, when);
    gain.gain.exponentialRampToValueAtTime(0.001, when + 0.09);
    osc.connect(gain);
    osc.start(when); osc.stop(when + 0.1);
    return;
  }
  const len = lane === "red" ? 0.06 : 0.03;
  const buf = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * len), ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const filter = ctx.createBiquadFilter();
  filter.type = lane === "red" ? "bandpass" : "highpass";
  filter.frequency.value = lane === "red" ? 1800 : 7000;
  gain.gain.value = lane === "red" ? 0.5 : 0.3;
  src.connect(filter); filter.connect(gain);
  src.start(when);
}

function beep(freq, when) {
  when = Math.max(when, audio.ctx.currentTime);
  const osc = audio.ctx.createOscillator(), gain = audio.ctx.createGain();
  osc.type = "square";
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.08, when);
  gain.gain.exponentialRampToValueAtTime(0.001, when + 0.04);
  osc.connect(gain); gain.connect(audio.metronome);
  osc.start(when); osc.stop(when + 0.05);
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
    commit(() => state.levels[state.level].push({ tick, lane }));
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
  commit(() => {
    for (const n of notes()) {
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
  commit(() => {
    state.levels[state.level] = notes().filter((n) => !state.selection.has(key(n)));
  });
  state.selection.clear();
  draw();
}

function toggleCymbal() {
  const targets = notes().filter((n) => state.selection.has(key(n)) && PADS.includes(color(n.lane)));
  if (!targets.length) return;
  commit(() => {
    for (const n of targets) n.lane = color(n.lane) + (n.lane.endsWith("-cym") ? "-tom" : "-cym");
  });
  state.selection = new Set(targets.map((n) => key(n)));
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
  commit(() => state.levels[state.level].push(...pasted));
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
  if (audio.playing) pause();
  audio.pausedAt = s;
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
  if (e.code === "Space") { e.preventDefault(); play(); }
  else if (mod && k === "z") { e.preventDefault(); e.shiftKey ? undo(state.redo, state.undo) : undo(state.undo, state.redo); }
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
    b.innerHTML = `${l[0].toUpperCase() + l.slice(1)}<span class="count">${state.levels[l].length}</span>`;
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

// Every tempo event is an anchor: a tick pinned to a time. Dragging a beat line pins its
// tick to a new time and recomputes the tempo on both sides; notes keep their ticks, so
// they move with the grid.

const tempoKey = () => JSON.stringify([state.song.tempos.map((t) => [t.tick, t.bpm]), state.song.offset]);

function retime(tempos) {
  tempos[0].sec = 0;
  for (let i = 1; i < tempos.length; i++) {
    const a = tempos[i - 1];
    tempos[i].sec = a.sec + (tempos[i].tick - a.tick) / state.song.ppq * 60 / a.bpm;
  }
  return tempos;
}

function placeBeat(base, tick, sec) {
  // Pin `tick` to chart second `sec`; tempos stay between 20 and 400 BPM.
  const ppq = state.song.ppq, t = base.map((x) => ({ ...x }));
  let a = t.findIndex((x) => x.tick === tick);
  if (a < 0) {
    a = t.findLastIndex((x) => x.tick < tick) + 1;
    t.splice(a, 0, { tick, bpm: t[a - 1].bpm, sec: 0 });
    retime(t);
  }
  const prev = t[a - 1], next = t[a + 1];
  const span = (from, to, bpm) => (to - from) / ppq * 60 / bpm;
  let lo = prev.sec + span(prev.tick, tick, 400), hi = prev.sec + span(prev.tick, tick, 20);
  if (next) {
    lo = Math.max(lo, next.sec - span(tick, next.tick, 20));
    hi = Math.min(hi, next.sec - span(tick, next.tick, 400));
  }
  sec = Math.min(hi, Math.max(lo, sec));
  prev.bpm = (tick - prev.tick) / ppq * 60 / (sec - prev.sec);
  if (next) t[a].bpm = (next.tick - tick) / ppq * 60 / (next.sec - sec);
  return retime(t);
}

function beatNear(x, px = 6) {
  let best = null, dist = px;
  for (const b of state.song.beats) {
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
  state.tempoDrag = { tick: b.tick, base: structuredClone(state.song.tempos), baseOffset: state.song.offset,
                      before: snapshot(), moved: false };
}

function dragTempo(x, free) {
  const d = state.tempoDrag;
  let target = xToSec(x);
  const hit = free ? null : nearestOnset(target, 0.04);
  if (hit !== null) target = hit;
  if (d.tick === 0) {
    state.song.offset = target;  // the first beat carries the whole chart: that is the offset
  } else {
    state.song.tempos = placeBeat(d.base, d.tick, target - state.song.offset);
  }
  d.moved = true;
  const t = state.song.tempos, i = t.findIndex((x) => x.tick === d.tick);
  status(d.tick === 0 ? `Offset ${Math.round(state.song.offset * 1000)} ms`
    : `${t[i - 1].bpm.toFixed(2)} BPM before · ${t[i].bpm.toFixed(2)} BPM after${hit !== null ? " · on a hit" : ""}`);
  draw();
}

function endTempoDrag() {
  const d = state.tempoDrag;
  state.tempoDrag = null;
  if (!d.moved) return draw();
  state.undo.push(d.before);
  state.redo = [];
  setDirty(true);
  renderTempo();
  draw();
}

function changeTempo(change) {
  state.undo.push(snapshot());
  state.redo = [];
  change();
  setDirty(true);
  renderTempo();
  draw();
}

function removeAnchor(tick) {
  changeTempo(() => {
    const t = state.song.tempos.map((x) => ({ ...x })), a = t.findIndex((x) => x.tick === tick);
    const prev = t[a - 1], next = t[a + 1];
    if (next) prev.bpm = (next.tick - prev.tick) / state.song.ppq * 60 / (next.sec - prev.sec);
    t.splice(a, 1);
    state.song.tempos = retime(t);
  });
  status("Anchor removed");
}

function setOffset(sec) {
  if (!Number.isFinite(sec)) return renderTempo();
  changeTempo(() => { state.song.offset = Math.round(sec * 1000) / 1000; });
}

function snapBeatToHit() {
  const pos = position();
  let beat = null;
  for (const b of state.song.beats) {
    if (!beat || Math.abs(tickToSec(b.tick) - pos) < Math.abs(tickToSec(beat.tick) - pos)) beat = b;
  }
  const hit = beat && nearestOnset(tickToSec(beat.tick), 0.08);
  if (hit === null || hit === undefined) return status("No drum hit within 80 ms of the nearest beat", true);
  changeTempo(() => {
    if (beat.tick === 0) state.song.offset = hit;
    else state.song.tempos = placeBeat(state.song.tempos, beat.tick, hit - state.song.offset);
  });
  status(`Beat pinned to the hit (${Math.round((hit - pos) * 1000)} ms from the playhead)`);
}

function revertTempo() {
  changeTempo(() => {
    state.song.tempos = structuredClone(state.tempoOpened.tempos);
    state.song.offset = state.tempoOpened.offset;
  });
  status("Tempo map and offset as opened");
}

function setTempoMode(on) {
  state.tempoMode = on && Boolean(state.song);
  $("mode-notes").classList.toggle("active", !state.tempoMode);
  $("mode-tempo").classList.toggle("active", state.tempoMode);
  if (state.tempoMode) state.selection.clear();
  renderTempo();
  renderTune();
  draw();
}

function renderTempo() {
  $("tempo-tools").hidden = !state.tempoMode;
  $("notes-tools").hidden = state.tempoMode;
  if (!state.tempoMode) return;
  $("tempo-offset").value = Math.round(state.song.offset * 1000);
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
  const sig = [...state.song.signatures].reverse().find((s) => s.tick <= tick) || { num: 4, den: 4 };
  const text = `${tempoAt(tick).bpm.toFixed(2)} BPM · ${sig.num}/${sig.den}`;
  if ($("tempo-here").textContent !== text) $("tempo-here").textContent = text;
}

function drawTempo(w, t0, t1) {
  // Anchors and tempos in the ruler; under each beat a dot for how far the nearest drum
  // hit is: green on it, amber 10–25 ms, red further (no dot: no hit within 60 ms).
  g.font = "11px -apple-system, sans-serif";
  g.textBaseline = "middle";
  let lastLabel = -Infinity;
  for (const t of state.song.tempos) {
    const x = secToX(t.sec + state.song.offset);
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
  for (const b of state.song.beats) {
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

// MARK: - Level settings

const INSTRUMENTS = [["kick", "Kick"], ["snare", "Snare"], ["hihat", "Hi-hat"], ["toms", "Toms"], ["cymbals", "Cymbals"]];
const expertPairs = () => state.levels.expert.map((n) => [n.tick, n.lane]);
const tuneSettings = () => state.levelSettings[state.level];

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
  const ex = state.levels.expert, mine = notes();
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

function applyTune(s) {
  const level = state.level;
  // Moves of the same level within a second are one undo step.
  const coalesce = state.tuning && state.tuning.level === level && Date.now() - state.tuning.at < 1000;
  if (!coalesce) { state.undo.push(snapshot()); state.redo = []; }
  state.tuning = { level, at: Date.now() };
  state.levelSettings = { ...state.levelSettings, [level]: s };
  setDirty(true);
  renderTune();
  clearTimeout(tuneTimer);
  tuneTimer = setTimeout(async () => {
    try {
      const data = await api("/api/reduce", { expert: expertPairs(), settings: { [level]: s } });
      if (state.levelSettings[level] !== s) return;  // a newer edit or an undo came first
      commit(() => { state.levels[level] = data[level].map(([tick, lane]) => ({ tick, lane })); }, true);
      renderTuneCount();
    } catch (e) { status(e.message, true); }
  }, 120);
}

function pickPreset(preset) {
  const current = tuneSettings();
  let s;
  if (preset === "custom") s = { ...structuredClone(current.preset === "clone-hero" ? state.song.presets.mpc[state.level] : current), preset: "custom" };
  else s = structuredClone(state.song.presets[preset][state.level]);
  applyTune(s);
  state.tuning = null;  // a preset is its own undo step
}

function setSongControls(enabled) {
  for (const id of ["play", "mode-notes", "mode-tempo", "uncertain", "regen", "save", "save-menu", "undo", "redo", "speed", "snap", "ticks", "metronome"]) {
    $(id).disabled = !enabled;
  }
  if (enabled) setDirty(state.dirty);
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
    const data = await api("/api/reduce", { expert: expertPairs(), settings: state.levelSettings });
    commit(() => {
      for (const l of levels) state.levels[l] = data[l].map(([tick, lane]) => ({ tick, lane }));
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
  const levels = {};
  for (const l of LEVELS) levels[l] = state.levels[l].map((n) => [n.tick, n.lane]);
  const body = { levels, settings: state.levelSettings };
  const tempoChanged = tempoKey() !== state.tempoSaved;
  if (tempoChanged) {
    body.tempos = state.song.tempos.map((t) => [t.tick, t.bpm]);
    body.offset = state.song.offset;
  }
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
      state.app.settings[draft ? "out_dir" : "export_dir"] = body.dest;
      status(draft ? `Saved the new song in ${data.folder}` : `Saved a copy in ${data.folder}; you're editing the copy now`);
    } else {
      status(`Saved ${data.file} in ${state.song.folder} (original kept as ${data.file}.orig)`);
    }
    if (tempoChanged) state.tempoSaved = tempoKey();
    setDirty(false);
    return true;
  } catch (e) { status(e.message, true); return false; }
}

// MARK: - Opening and converting

function okToLeave() {
  return !state.dirty || confirm("Discard the unsaved changes in this song?");
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

function showJob(job) {
  $("job").hidden = false;
  $("start").hidden = true;
  const name = job.audio.split(/[\\/]/).pop();
  $("job-title").textContent = job.state === "failed" ? "Conversion failed" : job.state === "done" ? "Done" : `Converting ${name}`;
  $("job-progress").hidden = job.state !== "running";
  if (job.state === "running") {
    const known = typeof job.fraction === "number";
    $("job-step").textContent = known ? `${job.step}… ${Math.round(job.fraction * 100)} %` : `${job.step}…`;
    $("job-time").textContent = `${Math.floor(job.elapsed / 60)}:${String(job.elapsed % 60).padStart(2, "0")}`;
    $("job-bar").classList.toggle("busy", !known);
    $("job-bar").style.width = known ? `${Math.round(job.fraction * 100)}%` : "";
  }
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

async function refresh() {
  state.app = await api("/api/state");
  if (state.app.job && state.app.job.state === "running") { showJob(state.app.job); pollJob(); }
  if (!state.app.song) {
    state.song = null;
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
  for (const l of LEVELS) {
    state.levels[l] = dedupe((state.song.levels[l] || []).map(([tick, lane]) => ({ tick, lane })));
  }
  state.undo = []; state.redo = []; state.selection.clear(); state.tuning = null;
  state.levelSettings = structuredClone(state.song.level_settings);
  state.tempoOpened = { tempos: structuredClone(state.song.tempos), offset: state.song.offset };
  state.tempoSaved = tempoKey();
  state.onsets = [];
  setTempoMode(false);
  state.dirty = Boolean(state.song.draft);  // a new song isn't saved anywhere yet
  state.level = "expert";
  audio.pausedAt = 0;
  $("song-title").textContent = [state.song.artist, state.song.name].filter(Boolean).join(" – ");
  document.title = `${state.song.name} · Trackyard`;
  setSongControls(true);
  renderTune();
  const first = state.levels.expert[0];
  state.view.start = first ? Math.max(-0.5, tickToSec(first.tick) - 1) : -0.5;
  resize();
  await loadAudio();
}

window.addEventListener("beforeunload", (e) => { if (state.dirty) e.preventDefault(); });
new ResizeObserver(() => resize()).observe(document.querySelector("main"));  // also when the level panel shows or hides
$("play").onclick = () => play();
$("save").onclick = () => save();
$("undo").onclick = () => undo(state.undo, state.redo);
$("redo").onclick = () => undo(state.redo, state.undo);
$("regen").onchange = (e) => {
  const which = e.target.value;
  e.target.value = "";
  if (which) regenerate(which);
};
$("uncertain").onclick = () => jumpUncertain();
$("open-mp3").onclick = $("start-mp3").onclick = () => openMp3();
$("open-folder").onclick = $("start-folder").onclick = () => openFolder();
$("hint-ok").onclick = () => closeLevelsHint();
$("save-menu").onclick = (e) => { e.stopPropagation(); $("save-pop").hidden = !$("save-pop").hidden; };
$("save-as").onclick = () => { $("save-pop").hidden = true; save(true); };
window.addEventListener("click", () => { $("save-pop").hidden = true; });
$("job-close").onclick = () => { $("job").hidden = true; if (!state.song) $("start").hidden = false; };
$("snap").onchange = (e) => { state.snap = Number(e.target.value); };
$("tune-preset").onchange = (e) => pickPreset(e.target.value);
$("mode-notes").onclick = () => setTempoMode(false);
$("mode-tempo").onclick = () => setTempoMode(true);
$("tempo-offset").onchange = (e) => setOffset(Number(e.target.value) / 1000);
$("offset-down").onclick = () => setOffset(state.song.offset - 0.005);
$("offset-up").onclick = () => setOffset(state.song.offset + 0.005);
$("snap-beat").onclick = () => snapBeatToHit();
$("tempo-revert").onclick = () => revertTempo();
canvas.addEventListener("dblclick", (e) => {
  if (!state.tempoMode) return;
  const { x } = pointer(e);
  const b = beatNear(x);
  if (b && b.tick > 0 && state.song.tempos.some((t) => t.tick === b.tick)) removeAnchor(b.tick);
});
$("tune-density").oninput = (e) => editTune((s) => { s.density = Number(e.target.value) / 100; });
$("tune-finest").onchange = (e) => editTune((s) => { s.finest = Number(e.target.value); });
$("tune-triplets").onchange = (e) => editTune((s) => { s.triplets = e.target.checked; });
$("tune-hands").onchange = (e) => editTune((s) => { s.max_hands = Number(e.target.value); });
$("tune-notes").onchange = (e) => editTune((s) => { s.max_notes = Number(e.target.value); });
$("tune-gap").onchange = (e) => editTune((s) => { s.min_gap_ms = Math.max(0, Number(e.target.value) || 0); });
$("speed").onchange = (e) => {
  state.speed = Number(e.target.value);
  for (const el of Object.values(audio.els)) el.playbackRate = state.speed;
};
$("ticks").onclick = (e) => { state.ticksOn = !state.ticksOn; e.target.classList.toggle("on", state.ticksOn); };
$("metronome").onclick = (e) => { state.metronomeOn = !state.metronomeOn; e.target.classList.toggle("on", state.metronomeOn); };

refresh().catch((e) => status(`Could not start: ${e.message}`, true));
