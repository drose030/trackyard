// The song being edited: the notes of every level, how the lower levels are generated
// (level settings) and the tempo map, with undo, redo and whether it is saved.
//
// Every change goes through `edit`, which keeps what a chart file can hold: one note per
// colour and tick, and cymbal versus tom per colour and tick on every level at once
// (notes.mid stores tom markers for all difficulties together), decided by the level
// being edited. Undo states are whole documents; nothing in them changes afterwards.

export const LEVELS = ["expert", "hard", "medium", "easy"];
const PADS = ["yellow", "blue", "green"];
const color = (lane) => lane.split("-")[0];

// Order within a tick, top row to bottom (the editor draws rows in this order too).
export const ROW_ORDER = ["red", "yellow", "blue", "orange", "green", "kick"];
export const rowOf = (lane) => (lane === "green-cym" ? "orange" : lane === "green-tom" ? "green" : color(lane));

export class SongDoc {
  // levels: level → [{tick, lane}] or [[tick, lane]]; settings: level → settings;
  // tempo: a TempoMap. `dirty`: not saved anywhere yet (a new song).
  constructor({ levels, settings, tempo, dirty = false }) {
    const notes = {};
    for (const l of LEVELS) notes[l] = (levels[l] || []).map((n) => (Array.isArray(n) ? { tick: n[0], lane: n[1] } : { ...n }));
    this.current = freeze({ levels: normalize(notes, "expert"), settings: { ...settings }, tempo });
    this.undoStack = [];
    this.redoStack = [];
    this.last = null;  // {key, at} of the last edit, for coalescing
    this.dirty = dirty;
    this.savedTempo = tempo;
    this.listeners = [];
  }

  get levels() { return this.current.levels; }
  get settings() { return this.current.settings; }
  get tempo() { return this.current.tempo; }
  get canUndo() { return this.undoStack.length > 0; }
  get canRedo() { return this.redoStack.length > 0; }

  // `change(draft)` may change draft.levels (arrays and notes are copies), replace
  // draft.settings[level] or draft.tempo. `level` decides cymbal vs tom. Edits with the
  // same `coalesce` key less than `within` ms apart are one undo step.
  edit(change, { level = "expert", coalesce = null, within = Infinity } = {}) {
    const draft = {
      levels: Object.fromEntries(LEVELS.map((l) => [l, this.levels[l].map((n) => ({ ...n }))])),
      settings: { ...this.settings },
      tempo: this.tempo,
    };
    change(draft);
    const now = Date.now();
    const merge = coalesce !== null && this.last && this.last.key === coalesce && now - this.last.at < within;
    if (!merge) this.undoStack.push(this.current);
    this.redoStack = [];
    this.last = coalesce === null ? null : { key: coalesce, at: now };
    this.current = freeze({ levels: normalize(draft.levels, level), settings: draft.settings, tempo: draft.tempo });
    this.dirty = true;
    this.emit({ edit: true });
  }

  undo() { this.step(this.undoStack, this.redoStack); }
  redo() { this.step(this.redoStack, this.undoStack); }

  step(from, to) {
    if (!from.length) return;
    to.push(this.current);
    this.current = from.pop();
    this.last = null;
    this.dirty = true;
    this.emit({ undo: true });
  }

  tempoChanged() {
    return !this.tempo.equals(this.savedTempo);
  }

  // What /api/save takes: every level, the settings and, if it changed, the tempo map.
  saveBody() {
    const body = { levels: Object.fromEntries(LEVELS.map((l) => [l, this.levels[l].map((n) => [n.tick, n.lane])])),
                   settings: this.settings };
    if (this.tempoChanged()) Object.assign(body, { tempos: this.tempo.pairs(), offset: this.tempo.offset });
    return body;
  }

  markSaved() {
    this.dirty = false;
    this.savedTempo = this.tempo;
    this.emit({ saved: true });
  }

  onChange(fn) {
    this.listeners.push(fn);
  }

  emit(what) {
    for (const fn of this.listeners) fn(what);
  }
}

function normalize(levels, decider) {
  const out = {};
  for (const l of LEVELS) {
    const seen = new Map();
    for (const n of levels[l]) seen.set(`${n.tick}:${color(n.lane)}`, n);  // the later note of a colour wins
    out[l] = [...seen.values()].sort((a, b) => a.tick - b.tick || ROW_ORDER.indexOf(rowOf(a.lane)) - ROW_ORDER.indexOf(rowOf(b.lane)));
  }
  const kind = new Map();
  for (const n of out[decider]) if (PADS.includes(color(n.lane))) kind.set(`${n.tick}:${color(n.lane)}`, n.lane);
  for (const l of LEVELS) {
    if (l === decider) continue;
    out[l] = out[l].map((n) => {
      const lane = kind.get(`${n.tick}:${color(n.lane)}`);
      return lane && lane !== n.lane ? { ...n, lane } : n;
    });
  }
  return out;
}

function freeze(doc) {
  for (const l of LEVELS) { doc.levels[l].forEach(Object.freeze); Object.freeze(doc.levels[l]); }
  Object.freeze(doc.levels);
  Object.freeze(doc.settings);
  return Object.freeze(doc);
}
